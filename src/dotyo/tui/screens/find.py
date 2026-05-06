"""FindScreen — full-screen search-as-you-type for cyphers.

Layout:
  +-----------------------------------------------------+
  | search input (debounced)                            |
  +-----------------------------------------------------+
  | results list           | preview pane               |
  | ▸ ranked cyphers       | title, host, jammers,      |
  |   …                    | seeks, status, age, goal   |
  |                        | [enter] /drop  [j] /join   |
  +-----------------------------------------------------+
  | status bar (footer)                                 |
  +-----------------------------------------------------+

Server hits /api/cyphers/discover?q=&capabilities=&status=&cursor=. We
debounce typing (~120ms) so each keystroke doesn't fire a request.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Input, Label, ListItem, ListView, Static

from ...banner import GREEN, GRAY, GRAY_FAINT, MAGENTA, WHITE
from ...lib.api import ApiError, get, post
from ..widgets.status_bar import StatusBar


def _ago(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        # Postgres ISO with TZ
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    delta = datetime.now(timezone.utc) - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


class CypherListItem(ListItem):
    """A row in the ranked results list. Holds the full cypher dict for preview."""

    def __init__(self, cypher: dict[str, Any]) -> None:
        title = cypher.get("title", "(untitled)")
        slug = cypher.get("slug") or (cypher.get("id", "") or "")[:8]
        status = cypher.get("status", "?")
        jammers = cypher.get("jammer_count", 0) or 0
        skills = ", ".join(cypher.get("requirements_skills") or []) or "any"

        line = Text()
        # status pip
        if status == "live":
            line.append("● ", style=GREEN)
        elif status == "lobby":
            line.append("● ", style="#d4a017")
        else:
            line.append("● ", style=GRAY_FAINT)
        line.append(title, style=f"bold {WHITE}")
        line.append(f"  {slug}", style=GRAY_FAINT)
        line.append(f"   {jammers} jammers", style=GRAY)
        line.append(f"  ·  seeks {skills}", style=GRAY_FAINT)
        super().__init__(Static(line))
        self.cypher = cypher


class FindScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "back"),
        Binding("enter", "drop_selected", "drop", show=False),
        Binding("j", "join_selected", "join"),
        Binding("d", "drop_selected", "drop"),
        Binding("ctrl+r", "refresh", "refresh"),
    ]

    DEFAULT_CSS = """
    FindScreen {
        background: #0a0a0a;
    }

    #find_header {
        dock: top;
        height: 3;
        border: tall #25C75E;
        padding: 0 1;
        background: #0a0a0a;
    }

    #find_input {
        background: #0a0a0a;
        border: none;
    }

    #find_body {
        height: 1fr;
    }

    #results_pane {
        width: 50%;
        height: 1fr;
        border-right: solid #2a2a2a;
        padding: 0 1;
    }

    #preview_pane {
        width: 50%;
        height: 1fr;
        padding: 0 2;
    }

    ListView {
        height: 1fr;
        background: #0a0a0a;
    }

    ListView > ListItem {
        background: #0a0a0a;
        padding: 0 1;
    }

    ListView > ListItem.--highlight {
        background: #1a3a24;
    }

    #preview_title {
        color: #ffffff;
        text-style: bold;
    }

    #preview_meta {
        color: #a0a0a0;
        margin: 1 0 0 0;
    }

    #preview_goal {
        color: #e8e8e8;
        margin: 1 0 0 0;
    }

    #preview_actions {
        color: #707070;
        margin-top: 1;
    }
    """

    query: reactive[str] = reactive("", recompose=False)

    def __init__(self, initial_query: str = "") -> None:
        super().__init__()
        self._initial_query = initial_query
        self._results: list[dict[str, Any]] = []
        self._next_cursor: str | None = None
        self._search_seq = 0
        self._debounce_handle: asyncio.Task | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="find_header"):
            yield Input(
                placeholder="search cyphers — title, goal, skills…",
                value=self._initial_query,
                id="find_input",
            )
        with Horizontal(id="find_body"):
            with Vertical(id="results_pane"):
                yield ListView(id="results_list")
            with Vertical(id="preview_pane"):
                yield Label("", id="preview_title")
                yield Label("", id="preview_meta")
                yield Static("", id="preview_goal")
                yield Label(
                    "[enter] /drop into  ·  [j] /join  ·  esc back",
                    id="preview_actions",
                )
        yield StatusBar(id="status_bar")

    async def on_mount(self) -> None:
        self.query_one("#find_input", Input).focus()
        # Initial query (capability-matched if no text)
        await self._fetch(self._initial_query)

    # ── Input handlers ─────────────────────────────────────────────────────

    async def on_input_changed(self, event: Input.Changed) -> None:
        # Debounce: cancel any pending fetch and schedule a new one ~150ms out
        if self._debounce_handle:
            self._debounce_handle.cancel()
        q = (event.value or "").strip()
        self._debounce_handle = asyncio.create_task(self._debounced_fetch(q))

    async def _debounced_fetch(self, q: str) -> None:
        try:
            await asyncio.sleep(0.15)
        except asyncio.CancelledError:
            return
        await self._fetch(q)

    async def _fetch(self, q: str) -> None:
        self._search_seq += 1
        seq = self._search_seq
        params: list[str] = []
        if q:
            from urllib.parse import quote
            params.append(f"q={quote(q)}")
        skills = (self.app.capabilities or {}).get("skills") or []
        if skills:
            from urllib.parse import quote
            params.append(f"capabilities={quote(','.join(skills))}")
        params.append("status=open")
        path = "/api/cyphers/discover" + ("?" + "&".join(params) if params else "")
        try:
            r = await get(path)
        except ApiError:
            return
        # Drop stale responses
        if seq != self._search_seq:
            return
        items = r.get("cyphers", []) if isinstance(r, dict) else []
        self._results = items
        self._next_cursor = r.get("next_cursor") if isinstance(r, dict) else None
        self._refresh_list()

    def _refresh_list(self) -> None:
        lv = self.query_one("#results_list", ListView)
        lv.clear()
        for c in self._results:
            lv.append(CypherListItem(c))
        if self._results:
            lv.index = 0
            self._update_preview(self._results[0])
        else:
            self._update_preview(None)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if isinstance(item, CypherListItem):
            self._update_preview(item.cypher)

    def _update_preview(self, c: dict[str, Any] | None) -> None:
        title_w = self.query_one("#preview_title", Label)
        meta_w = self.query_one("#preview_meta", Label)
        goal_w = self.query_one("#preview_goal", Static)
        if not c:
            title_w.update("")
            meta_w.update(Text("no results — try a different query, or /host one", style=GRAY_FAINT))
            goal_w.update("")
            return
        title_w.update(c.get("title", "(untitled)"))
        slug = c.get("slug") or (c.get("id", "") or "")[:8]

        meta = Text()
        status = c.get("status", "?")
        if status == "live":
            meta.append("● live  ", style=GREEN)
        elif status == "lobby":
            meta.append("● lobby  ", style="#d4a017")
        else:
            meta.append(f"● {status}  ", style=GRAY_FAINT)
        meta.append(slug, style=GRAY)
        meta.append(f"\nseeks  ", style=GRAY_FAINT)
        meta.append(", ".join(c.get("requirements_skills") or []) or "any", style=GRAY)
        meta.append(f"\njammers  ", style=GRAY_FAINT)
        meta.append(str(c.get("jammer_count", 0) or 0), style=GRAY)
        meta.append(f"\nstarted  ", style=GRAY_FAINT)
        meta.append(_ago(c.get("created_at")) or "—", style=GRAY)
        meta_w.update(meta)

        goal = c.get("goal") or c.get("description") or ""
        goal_w.update(Text(goal, style="#e8e8e8") if goal else Text("(no goal set)", style=GRAY_FAINT))

    # ── Actions ────────────────────────────────────────────────────────────

    def _selected(self) -> dict[str, Any] | None:
        lv = self.query_one("#results_list", ListView)
        idx = lv.index
        if idx is None or idx < 0 or idx >= len(self._results):
            return None
        return self._results[idx]

    async def action_drop_selected(self) -> None:
        c = self._selected()
        if not c:
            return
        ref = c.get("slug") or c.get("id")
        if not ref:
            return
        # Pop find first so the back stack is clean (drop replaces).
        self.app.pop_screen()
        from .drop import DropScreen
        await self.app.push_screen(DropScreen(ref=ref))

    async def action_join_selected(self) -> None:
        c = self._selected()
        if not c:
            return
        ref = c.get("slug") or c.get("id")
        if not ref:
            return
        try:
            await post(f"/api/cyphers/{ref}/join")
            # Bounce a notification in the title row briefly
            title_w = self.query_one("#preview_title", Label)
            old = title_w.renderable
            title_w.update(Text(f"✓ joined {ref}", style=GREEN))
            await asyncio.sleep(0.8)
            title_w.update(old)
        except ApiError as e:
            title_w = self.query_one("#preview_title", Label)
            title_w.update(Text(f"✗ {e}", style="#d44a4a"))

    async def action_back(self) -> None:
        self.app.pop_screen()

    async def action_refresh(self) -> None:
        q = self.query_one("#find_input", Input).value
        await self._fetch(q.strip())

    # Submitting the input runs an immediate (non-debounced) fetch; useful
    # if the user typed fast and wants to force a search now.
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._debounce_handle:
            self._debounce_handle.cancel()
        await self._fetch((event.value or "").strip())
