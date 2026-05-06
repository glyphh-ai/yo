"""DropScreen — drop into a live cypher.

Layout:
  +------------------------------------------------------------------+
  | 🟢 <title>  ·  <status>  ·  jammers N/M               [esc back] |  header
  +------------------------------------------------------------------+
  | event stream (live)                  | jammer roster              |
  | 14:02 alice spawn → "summarize…"     | ▸ chris (host)             |
  | 14:03 alice ✓ done (12k tok)         |   alice (you)              |
  | 14:04 bob   spawn → "compare …"      |   bob                      |
  | …                                    |                            |
  +------------------------------------------------------------------+
  | say> [_______________________________]                           |
  | status bar                                                       |
  +------------------------------------------------------------------+

The event stream subscribes to /api/cyphers/:ref/events/stream via
`cypher_event_stream`. Jammers reload every 10s. Input lines post to
/api/cyphers/:ref/events as a 'message' event so other jammers see them.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Input, Label, RichLog, Static

from ...banner import GREEN, GRAY, GRAY_FAINT, MAGENTA, WHITE
from ...lib.api import ApiError, get, post
from ...network import cypher_event_stream
from ..widgets.status_bar import StatusBar


def _hhmm(iso: str | None) -> str:
    if not iso:
        return "--:--"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%H:%M:%S")
    except ValueError:
        return "--:--"


class DropScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "back"),
        Binding("ctrl+j", "join_cypher", "join"),
        Binding("ctrl+w", "wrap_cypher", "wrap"),
    ]

    DEFAULT_CSS = """
    DropScreen {
        background: #0a0a0a;
    }

    #drop_header {
        dock: top;
        height: 3;
        padding: 0 1;
        border: tall #25C75E;
    }

    #drop_title {
        color: #ffffff;
        text-style: bold;
    }

    #drop_meta {
        color: #a0a0a0;
    }

    #drop_body {
        height: 1fr;
    }

    #event_pane {
        width: 75%;
        border-right: solid #2a2a2a;
        padding: 0 1;
    }

    #roster_pane {
        width: 25%;
        padding: 0 1;
    }

    #roster_title {
        color: #707070;
        text-style: italic;
    }

    #event_log {
        height: 1fr;
        background: #0a0a0a;
        border: none;
    }

    #say_input {
        dock: bottom;
        margin: 0 0 1 0;
        height: 3;
        border: tall #707070;
        padding: 0 1;
    }

    #say_input:focus {
        border: tall #25C75E;
    }
    """

    def __init__(self, ref: str) -> None:
        super().__init__()
        self._ref = ref
        self._cypher: dict[str, Any] | None = None
        self._jammers: list[dict[str, Any]] = []
        self._stream_shutdown = asyncio.Event()
        self._stream_task: asyncio.Task | None = None
        self._roster_task: asyncio.Task | None = None
        self.event_log: RichLog | None = None

    # ── Layout ─────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Vertical(id="drop_header"):
            yield Label("loading…", id="drop_title")
            yield Label("", id="drop_meta")
        with Horizontal(id="drop_body"):
            with Vertical(id="event_pane"):
                self.event_log = RichLog(
                    wrap=True, markup=False, highlight=False, auto_scroll=True, id="event_log",
                )
                yield self.event_log
            with Vertical(id="roster_pane"):
                yield Label("jammers", id="roster_title")
                yield Static("", id="roster_list")
        yield Input(placeholder="say something to the cypher (ctrl+j join · ctrl+w wrap · esc back)", id="say_input")
        yield StatusBar(id="status_bar")

    async def on_mount(self) -> None:
        # Pull the cypher header + roster
        await self._load_cypher()
        if not self._cypher:
            self.query_one("#drop_title", Label).update(
                Text(f"✗ couldn't open cypher {self._ref}", style="#d44a4a")
            )
            return

        self._update_header()
        await self._load_roster()
        # Start event stream + roster refresh loop
        self._stream_task = asyncio.create_task(self._run_event_stream())
        self._roster_task = asyncio.create_task(self._roster_refresh_loop())
        self.query_one("#say_input", Input).focus()

    async def on_unmount(self) -> None:
        self._stream_shutdown.set()
        for t in (self._stream_task, self._roster_task):
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

    # ── Data loaders ───────────────────────────────────────────────────────

    async def _load_cypher(self) -> None:
        try:
            r = await get(f"/api/cyphers/{self._ref}")
        except ApiError as e:
            if getattr(e, "status", 0) == 409 and isinstance(getattr(e, "body", None), dict):
                cands = e.body.get("candidates") or []
                if self.event_log:
                    self.event_log.write(Text(f"ambiguous ref — candidates: {[c.get('slug') for c in cands][:5]}", style="#d4a017"))
                return
            return
        self._cypher = r.get("cypher") if isinstance(r, dict) else None

    async def _load_roster(self) -> None:
        if not self._cypher:
            return
        try:
            r = await get(f"/api/cyphers/{self._cypher['id']}/jammers")
        except ApiError:
            return
        self._jammers = r.get("jammers", []) if isinstance(r, dict) else []
        self._render_roster()

    async def _roster_refresh_loop(self) -> None:
        try:
            while not self._stream_shutdown.is_set():
                await asyncio.sleep(10)
                await self._load_roster()
        except asyncio.CancelledError:
            return

    # ── Renderers ──────────────────────────────────────────────────────────

    def _update_header(self) -> None:
        if not self._cypher:
            return
        c = self._cypher
        title_w = self.query_one("#drop_title", Label)
        meta_w = self.query_one("#drop_meta", Label)

        slug = c.get("slug") or (c.get("id", "") or "")[:8]
        title_w.update(Text(c.get("title") or "(untitled)", style=f"bold {WHITE}"))

        status = c.get("status", "?")
        meta = Text()
        if status == "live":
            meta.append("● live", style=GREEN)
        elif status == "lobby":
            meta.append("● lobby", style="#d4a017")
        elif status == "wrapped":
            meta.append("● wrapped", style=GRAY_FAINT)
        else:
            meta.append(f"● {status}", style=GRAY_FAINT)
        meta.append("  ·  ", style=GRAY_FAINT)
        meta.append(slug, style=GRAY)
        skills = ", ".join(c.get("requirements_skills") or []) or "any"
        meta.append("  ·  seeks ", style=GRAY_FAINT)
        meta.append(skills, style=GRAY)
        goal = c.get("goal") or ""
        if goal:
            meta.append("  ·  ", style=GRAY_FAINT)
            meta.append(goal[:80] + ("…" if len(goal) > 80 else ""), style=GRAY)
        meta_w.update(meta)

    def _render_roster(self) -> None:
        roster_w = self.query_one("#roster_list", Static)
        if not self._jammers:
            roster_w.update(Text("no one yet — invite via /find", style=GRAY_FAINT))
            return
        owner_id = (self._cypher or {}).get("owner_user_id")
        my_id = (self.app.user or {}).get("id")
        body = Text()
        for j in self._jammers:
            uid = j.get("user_id")
            label = j.get("display_name") or j.get("email", "?")
            is_host = uid == owner_id
            is_me = uid == my_id
            line = Text()
            if is_host:
                line.append("▸ ", style=GREEN)
            else:
                line.append("  ")
            line.append(label, style=f"bold {WHITE}" if is_host else GRAY)
            if is_me:
                line.append(" (you)", style=GRAY_FAINT)
            elif is_host:
                line.append(" (host)", style=GRAY_FAINT)
            body.append_text(line)
            body.append("\n")
        roster_w.update(body)

    # ── Event stream ───────────────────────────────────────────────────────

    async def _run_event_stream(self) -> None:
        if not self._cypher:
            return

        def on_event(evt: dict[str, Any]) -> None:
            self._render_event(evt)

        try:
            await cypher_event_stream(
                self._cypher["id"],
                on_event,
                self._stream_shutdown,
            )
        except asyncio.CancelledError:
            return
        except Exception:
            pass

    def _render_event(self, evt: dict[str, Any]) -> None:
        if not self.event_log:
            return
        kind = evt.get("kind", "event")
        if kind == "_stream_connected":
            self.event_log.write(Text("· stream connected", style=GRAY_FAINT))
            return
        if kind == "_stream_disconnected":
            self.event_log.write(
                Text(f"· stream disconnected ({evt.get('error', '?')}) — reconnecting", style="#d4a017")
            )
            return

        data = evt.get("data") or {}
        ts = _hhmm(data.get("created_at"))
        line = Text()
        line.append(f"{ts}  ", style=GRAY_FAINT)

        # Nicer rendering for known event kinds
        actor = data.get("by_user_email") or data.get("by_user_id") or ""
        actor_short = (actor[:16] + "…") if len(actor) > 17 else actor

        if kind == "message":
            line.append(actor_short or "?", style=MAGENTA)
            line.append("  ", style=GRAY_FAINT)
            text = ((data.get("payload") or {}).get("text") or "").strip()
            line.append(text or "(empty message)", style="#e8e8e8")
        elif kind == "spawn_started":
            line.append(actor_short or "?", style=MAGENTA)
            line.append("  spawn →  ", style=GRAY_FAINT)
            prompt = (((data.get("payload") or {}).get("prompt")) or "")[:60]
            line.append(prompt or "(no prompt)", style=GRAY)
        elif kind == "spawn_done":
            payload = data.get("payload") or {}
            in_t = payload.get("input_tokens", 0)
            out_t = payload.get("output_tokens", 0)
            line.append(actor_short or "?", style=MAGENTA)
            line.append(f"  ✓ done ({in_t}+{out_t} tok)", style=GREEN)
        elif kind in ("cypher_start", "cypher_wrap", "cypher_cancel"):
            line.append(kind.replace("_", " "), style=GREEN)
        elif kind == "join":
            line.append(actor_short or "?", style=GREEN)
            line.append("  joined", style=GRAY_FAINT)
        elif kind == "leave":
            line.append(actor_short or "?", style=GRAY)
            line.append("  left", style=GRAY_FAINT)
        else:
            line.append(kind, style=GRAY_FAINT)
            payload = data.get("payload")
            if payload:
                import json as _j
                line.append("  ", style=GRAY_FAINT)
                line.append(_j.dumps(payload)[:120], style=GRAY)

        self.event_log.write(line)

    # ── Input ──────────────────────────────────────────────────────────────

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = (event.value or "").strip()
        if not text:
            return
        self.query_one("#say_input", Input).value = ""
        if not self._cypher:
            return
        try:
            await post(
                f"/api/cyphers/{self._cypher['id']}/events",
                json={"kind": "message", "payload": {"text": text}},
            )
        except ApiError as e:
            if self.event_log:
                self.event_log.write(Text(f"✗ post failed: {e}", style="#d44a4a"))

    # ── Actions ────────────────────────────────────────────────────────────

    async def action_back(self) -> None:
        self.app.pop_screen()

    async def action_join_cypher(self) -> None:
        if not self._cypher:
            return
        try:
            await post(f"/api/cyphers/{self._cypher['id']}/join")
            if self.event_log:
                self.event_log.write(Text(f"✓ joined", style=GREEN))
            await self._load_roster()
        except ApiError as e:
            if self.event_log:
                self.event_log.write(Text(f"✗ join failed: {e}", style="#d44a4a"))

    async def action_wrap_cypher(self) -> None:
        if not self._cypher:
            return
        try:
            await post(f"/api/cyphers/{self._cypher['id']}/wrap")
            if self.event_log:
                self.event_log.write(Text("✓ cypher wrapped", style=GREEN))
        except ApiError as e:
            if self.event_log:
                self.event_log.write(Text(f"✗ wrap failed: {e}", style="#d44a4a"))
