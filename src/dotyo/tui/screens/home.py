"""HomeScreen — the chat REPL home.

Layout:
  • Banner (one-time render at the top of the scrollback)
  • ChatLog scrollback (RichLog)
  • Input prompt at the bottom (Input widget)
  • StatusBar docked at the very bottom

Slash commands:
  • / handled here, pushed in priority order
  • Anything else → orchestrator.query()
"""

from __future__ import annotations

import asyncio
import shlex
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Input

from ...banner import GREEN, GRAY, GRAY_FAINT, WHITE
from ...lib.api import ApiError, get, post, put
from ...commands.mcp_cmd import (
    is_yo_mcp_registered,
    register_yo_mcp,
    unregister_yo_mcp,
)
from ..widgets.banner import Banner
from ..widgets.chat_log import ChatLog
from ..widgets.status_bar import StatusBar


CAPABILITIES = [
    ("code", "general programming, debugging, refactoring"),
    ("research", "web search, summarization, deep dives"),
    ("writing", "long-form, technical writing, editing"),
    ("design", "UI/UX, visual, layout decisions"),
    ("data", "analysis, queries, viz"),
    ("planning", "roadmaps, breakdowns, prioritization"),
    ("review", "audit, critique, code review"),
    ("ops", "infra, scripts, deploy"),
]

WELCOME_HINT = (
    "type to chat with yo  ·  /help for commands  ·  /find to search cyphers  ·  "
    "/drop <ref> to enter a cypher  ·  /quit to exit"
)


class HomeScreen(Screen):
    BINDINGS = [
        Binding("ctrl+f", "find", "find"),
        Binding("ctrl+l", "clear", "clear", show=False),
    ]

    DEFAULT_CSS = """
    HomeScreen {
        background: #0a0a0a;
    }

    #chat_input {
        dock: bottom;
        height: 3;
        background: #0a0a0a;
        border: tall #25C75E;
        padding: 0 1;
        margin: 0 0 1 0;
    }

    #chat_input:focus {
        border: tall #25C75E;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.chat: ChatLog | None = None
        self.input: Input | None = None
        self._busy = False  # locks input while orchestrator response is streaming

    def compose(self) -> ComposeResult:
        with Vertical():
            self.chat = ChatLog()
            yield self.chat
        self.input = Input(placeholder="message yo, or /help …", id="chat_input")
        yield self.input
        yield StatusBar(id="status_bar")

    def on_mount(self) -> None:
        assert self.chat and self.input
        self.input.focus()

        # Print banner + welcome lines once.
        self.chat.write(Banner())
        user = self.app.user or {}
        skills = ", ".join(self.app.capabilities.get("skills") or []) or "—"
        self.chat.write_dim(
            f"signed in as {user.get('email', '?')}  ·  stack: {skills}"
        )
        self.chat.write_dim(WELCOME_HINT)
        self.chat.write_blank()

    # ── App-event hook (worker SSE incoming) ───────────────────────────────

    def on_app_event(self, kind: str, evt: dict[str, Any]) -> None:
        if not self.chat:
            return
        if kind == "incoming_started":
            prompt = (evt.get("prompt") or "")[:60]
            self.chat.write_dim(f"→ incoming spawn: {prompt}…")
        elif kind == "incoming_done":
            elapsed = evt.get("elapsed", 0)
            in_t = evt.get("input_tokens", 0)
            out_t = evt.get("output_tokens", 0)
            self.chat.write_dim(f"✓ served ({in_t}+{out_t} tok, {elapsed:.1f}s)")
        elif kind == "incoming_failed":
            self.chat.write_status(f"✗ spawn failed: {evt.get('error', '?')}", kind="err")

    # ── Input handling ─────────────────────────────────────────────────────

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        line = (event.value or "").strip()
        if not self.input or self.chat is None:
            return
        self.input.value = ""
        if not line:
            return
        if self._busy:
            return

        self.chat.write_user(line)

        if line.lower() in ("exit", "quit"):
            self.app.exit()
            return

        if line.startswith("/"):
            await self._handle_slash(line)
            return

        await self._send_to_orchestrator(line)

    async def _send_to_orchestrator(self, prompt: str) -> None:
        assert self.chat
        client = await self.app.wait_for_orchestrator(timeout=30.0)
        if client is None:
            self.chat.write_status("✗ orchestrator unavailable — try /quit and restart", kind="err")
            return

        self._busy = True
        if self.input:
            self.input.disabled = True
        try:
            await client.query(prompt)
            await self._stream_response(client)
        except Exception as e:
            self.chat.write_status(f"✗ orchestrator error: {e}", kind="err")
        finally:
            self._busy = False
            if self.input:
                self.input.disabled = False
                self.input.focus()

    async def _stream_response(self, client: Any) -> None:
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            SystemMessage,
            TextBlock,
            ThinkingBlock,
            ToolResultBlock,
            ToolUseBlock,
            UserMessage,
        )

        assert self.chat
        text_buf: list[str] = []

        def flush() -> None:
            if text_buf:
                self.chat.write_assistant_md("".join(text_buf))
                text_buf.clear()

        async for msg in client.receive_response():
            if isinstance(msg, SystemMessage):
                continue
            if isinstance(msg, AssistantMessage):
                for block in (msg.content or []):
                    if isinstance(block, TextBlock):
                        text_buf.append(block.text)
                    elif isinstance(block, ThinkingBlock):
                        pass
                    elif isinstance(block, ToolUseBlock):
                        flush()
                        name = getattr(block, "name", "tool")
                        inp = getattr(block, "input", {})
                        summary = None
                        if isinstance(inp, dict):
                            if name.endswith("__spawn") and "prompt" in inp:
                                p = str(inp["prompt"])[:60]
                                summary = f"→ {p}{'…' if len(str(inp['prompt'])) > 60 else ''}"
                            elif name.endswith("__spawn_parallel") and "prompts" in inp:
                                try:
                                    import json as _j
                                    ps = _j.loads(inp["prompts"]) if isinstance(inp["prompts"], str) else inp["prompts"]
                                    summary = f"→ ×{len(ps)}"
                                except Exception:
                                    pass
                        self.chat.write_tool(name, summary)
                    elif isinstance(block, ToolResultBlock):
                        flush()
            elif isinstance(msg, UserMessage):
                pass
            elif isinstance(msg, ResultMessage):
                flush()
                in_t = 0
                out_t = 0
                try:
                    if isinstance(msg.usage, dict):
                        in_t = int(msg.usage.get("input_tokens", 0) or 0)
                        out_t = int(msg.usage.get("output_tokens", 0) or 0)
                    else:
                        in_t = int(getattr(msg.usage, "input_tokens", 0) or 0)
                        out_t = int(getattr(msg.usage, "output_tokens", 0) or 0)
                except Exception:
                    pass
                self.chat.write_result_tail(in_t, out_t)
                self.chat.write_blank()
                return
        flush()

    # ── Slash dispatcher ───────────────────────────────────────────────────

    async def _handle_slash(self, line: str) -> None:
        assert self.chat
        try:
            parts = shlex.split(line.strip())
        except ValueError:
            parts = line.strip().split()
        if not parts:
            return
        cmd = parts[0].lstrip("/").lower()
        args = parts[1:]

        if cmd in ("quit", "q", "exit"):
            self.app.exit()
            return

        if cmd == "help":
            self._print_help()
            return

        if cmd == "clear":
            self.chat.clear()
            return

        if cmd == "me":
            if args and args[0] == "edit":
                await self._capability_picker()
            else:
                self._show_me()
            return

        if cmd == "online":
            await self._slash_online()
            return

        if cmd == "host":
            if not args:
                self.chat.write_status('usage: /host "<goal>" [public|unlisted]', kind="warn")
                return
            visibility = args[1] if len(args) > 1 and args[1] in ("public", "unlisted", "invite") else "public"
            await self._slash_host(args[0], visibility)
            return

        if cmd == "find":
            from .find import FindScreen
            initial = " ".join(args) if args else ""
            await self.app.push_screen(FindScreen(initial_query=initial))
            return

        if cmd == "drop":
            if not args:
                self.chat.write_status("usage: /drop <cypher_id_or_slug>", kind="warn")
                return
            await self._open_drop(args[0])
            return

        if cmd in ("join", "leave"):
            if not args:
                self.chat.write_status(f"usage: /{cmd} <cypher_id_or_slug>", kind="warn")
                return
            await self._slash_join_leave(cmd, args[0])
            return

        if cmd == "cyphers":
            await self._slash_cyphers()
            return

        if cmd == "start":
            if not args:
                self.chat.write_status("usage: /start <cypher_id_or_slug>", kind="warn")
                return
            await self._slash_start(args[0])
            return

        if cmd == "wrap":
            if not args:
                self.chat.write_status("usage: /wrap <cypher_id_or_slug>", kind="warn")
                return
            await self._slash_wrap(args[0])
            return

        if cmd == "mcp":
            await self._slash_mcp(args)
            return

        self.chat.write_status(f"unknown command: {line}  (/help)", kind="warn")

    # ── Slash command implementations ──────────────────────────────────────

    def _print_help(self) -> None:
        assert self.chat
        rows = [
            ("/help", "this list"),
            ("/quit, /q", "leave the app"),
            ("/clear", "clear scrollback"),
            ("", ""),
            ("/me", "your profile"),
            ("/me edit", "edit capability stack"),
            ("/online", "collaborators online right now"),
            ("", ""),
            ("/host \"<goal>\"", "host a cypher (publishes to lobby)"),
            ("/find [query]", "search cyphers (rich)"),
            ("/drop <ref>", "drop into a cypher (live cockpit)"),
            ("/join <ref>", "offer your AI to a cypher"),
            ("/leave <ref>", "stop offering"),
            ("/cyphers", "cyphers you're in / hosting"),
            ("/start <ref>", "flip your lobby cypher live"),
            ("/wrap <ref>", "wrap a cypher you host"),
            ("", ""),
            ("/mcp", "yo MCP registration status"),
            ("/mcp install", "(re)register yo MCP with Claude Code"),
            ("/mcp uninstall", "remove yo MCP from Claude Code"),
        ]
        from rich.table import Table
        t = Table(show_header=False, box=None, padding=(0, 2))
        t.add_column(style=f"bold {GREEN}", width=20)
        t.add_column(style=GRAY)
        for left, right in rows:
            t.add_row(left, right)
        self.chat.write(t)
        self.chat.write_blank()

    def _show_me(self) -> None:
        assert self.chat
        from rich.table import Table
        u = self.app.user or {}
        caps = self.app.capabilities or {}
        t = Table(show_header=False, box=None, padding=(0, 2))
        t.add_column(style=GRAY, width=14)
        t.add_column(style=f"bold {WHITE}")
        t.add_row("email", u.get("email", "?"))
        t.add_row("tier", u.get("tier", "free"))
        t.add_row("stack", ", ".join(caps.get("skills") or []) or "—")
        if caps.get("blurb"):
            t.add_row("blurb", caps["blurb"])
        t.add_row("model", caps.get("model") or "claude-sonnet-4-5")
        self.chat.write(t)
        self.chat.write_blank()

    async def _capability_picker(self) -> None:
        """Lightweight inline editor for the capability stack — no modal screen."""
        from .picker import CapabilityPicker
        await self.app.push_screen(CapabilityPicker())

    async def _slash_online(self) -> None:
        assert self.chat
        try:
            r = await get("/api/spawn/workers")
        except ApiError as e:
            self.chat.write_status(f"✗ {e}", kind="err")
            return
        workers = r.get("workers", []) if isinstance(r, dict) else []
        if not workers:
            self.chat.write_dim("no collaborators online right now — invite a friend to run yo")
            return
        from rich.table import Table
        t = Table(show_header=True, header_style=f"bold {GREEN}", box=None, padding=(0, 2))
        t.add_column("name")
        t.add_column("id", style=GRAY)
        t.add_column("inflight")
        t.add_column("capabilities", style=GRAY)
        for w in workers:
            t.add_row(
                w.get("name", "?"),
                (w.get("worker_id") or "?")[:8],
                f"{w.get('inflight', 0)}/{w.get('max_concurrent', 1)}",
                ", ".join(w.get("capabilities") or []) or "any",
            )
        self.chat.write(t)
        self.chat.write_blank()

    async def _slash_host(self, goal: str, visibility: str) -> None:
        assert self.chat
        body: dict[str, Any] = {
            "title": goal[:80],
            "type": "open",
            "goal": goal,
            "visibility": visibility,
        }
        skills = (self.app.capabilities or {}).get("skills")
        if skills:
            body["requirements_skills"] = skills
        try:
            r = await post("/api/cyphers", json=body)
        except ApiError as e:
            self.chat.write_status(f"✗ couldn't create cypher — {e}", kind="err")
            return
        c = r.get("cypher") if isinstance(r, dict) else None
        if not c:
            self.chat.write_status("✗ unexpected response", kind="err")
            return
        cid = c.get("id", "")
        slug = c.get("slug", "")
        try:
            pub = await post(f"/api/cyphers/{cid}/publish", json={"initial_kitty_deposit": 0})
            c = pub.get("cypher") if isinstance(pub, dict) else c
        except ApiError as e:
            self.chat.write_status(
                f"· created but couldn't publish — {e}. Cypher {cid[:8]} is in draft.",
                kind="warn",
            )
            return
        self.chat.write_status(
            f"✓ cypher {slug or cid[:8]} live in lobby — {visibility}", kind="ok"
        )
        self.chat.write_dim(f"  share:  yo://cypher/{slug or cid[:8]}")
        self.chat.write_dim(f"  ready?  /start {slug or cid[:8]}  · drop in: /drop {slug or cid[:8]}")
        self.chat.write_blank()

    async def _slash_start(self, ref: str) -> None:
        assert self.chat
        try:
            r = await post(f"/api/cyphers/{ref}/start")
        except ApiError as e:
            if getattr(e, "status", 0) == 409 and isinstance(getattr(e, "body", None), dict):
                self._show_candidates(e.body.get("candidates") or [])
                return
            self.chat.write_status(f"✗ {e}", kind="err")
            return
        c = r.get("cypher") if isinstance(r, dict) else None
        if c:
            self.chat.write_status(f"✓ cypher {c.get('slug') or c.get('id','')[:8]} is now live", kind="ok")

    async def _slash_wrap(self, ref: str) -> None:
        assert self.chat
        try:
            await post(f"/api/cyphers/{ref}/wrap")
            self.chat.write_status(f"✓ cypher {ref} wrapped", kind="ok")
        except ApiError as e:
            if getattr(e, "status", 0) == 409 and isinstance(getattr(e, "body", None), dict):
                self._show_candidates(e.body.get("candidates") or [])
                return
            self.chat.write_status(f"✗ {e}", kind="err")

    async def _slash_join_leave(self, action: str, ref: str) -> None:
        assert self.chat
        try:
            await post(f"/api/cyphers/{ref}/{action}")
        except ApiError as e:
            if getattr(e, "status", 0) == 409 and isinstance(getattr(e, "body", None), dict):
                self._show_candidates(e.body.get("candidates") or [])
                return
            self.chat.write_status(f"✗ {e}", kind="err")
            return
        verb = "joined" if action == "join" else "left"
        self.chat.write_status(f"✓ {verb} cypher {ref}", kind="ok")

    async def _slash_cyphers(self) -> None:
        assert self.chat
        try:
            r = await get("/api/cyphers/mine")
        except ApiError as e:
            self.chat.write_status(f"✗ {e}", kind="err")
            return
        items = r.get("cyphers", []) if isinstance(r, dict) else []
        if not items:
            self.chat.write_dim("you're not in any cyphers — try /find or /host")
            return
        from rich.table import Table
        t = Table(show_header=True, header_style=f"bold {GREEN}", box=None, padding=(0, 2))
        t.add_column("title")
        t.add_column("ref", style=GRAY)
        t.add_column("status")
        t.add_column("role")
        for c in items:
            t.add_row(
                c.get("title", "?"),
                c.get("slug") or (c.get("id", "?") or "")[:8],
                c.get("status", "?"),
                c.get("role", "jammer"),
            )
        self.chat.write(t)
        self.chat.write_blank()

    async def _open_drop(self, ref: str) -> None:
        from .drop import DropScreen
        await self.app.push_screen(DropScreen(ref=ref))

    async def _slash_mcp(self, args: list[str]) -> None:
        assert self.chat
        sub = (args[0] if args else "status").lower()
        scope = args[1] if len(args) > 1 else "user"

        if sub == "status":
            registered = is_yo_mcp_registered(scope)
            if scope == "user":
                self.app.mcp_registered = registered
            if registered:
                self.chat.write_status(f"✓ yo MCP registered with Claude Code ({scope})", kind="ok")
            else:
                self.chat.write_status(f"· yo MCP not registered ({scope})", kind="warn")
                self.chat.write_dim("  run /mcp install to add it")
            return

        if sub == "install":
            ok, msg = register_yo_mcp(scope)
            if ok:
                if scope == "user":
                    self.app.mcp_registered = True
                self.chat.write_status(f"✓ yo MCP registered ({scope})", kind="ok")
            else:
                self.chat.write_status(f"✗ {msg}", kind="err")
            return

        if sub == "uninstall":
            ok, msg = unregister_yo_mcp(scope)
            if ok:
                if scope == "user":
                    self.app.mcp_registered = False
                self.chat.write_status(f"✓ yo MCP removed ({scope})", kind="ok")
                self.chat.write_dim("  it'll be re-registered next time `yo` starts")
            else:
                self.chat.write_status(f"✗ {msg}", kind="err")
            return

        self.chat.write_status("usage: /mcp [status|install|uninstall] [user|project|local]", kind="warn")

    def _show_candidates(self, cands: list[dict]) -> None:
        if not self.chat:
            return
        from rich.table import Table
        t = Table(show_header=True, header_style=f"bold {GREEN}", box=None, padding=(0, 2))
        t.add_column("ref", style=GRAY)
        t.add_column("title")
        t.add_column("status")
        for c in cands[:10]:
            t.add_row(
                c.get("slug") or (c.get("id", "?") or "")[:8],
                c.get("title", "?"),
                c.get("status", "?"),
            )
        self.chat.write_status("ambiguous reference — pick one:", kind="warn")
        self.chat.write(t)
        self.chat.write_blank()

    # ── Action bindings ────────────────────────────────────────────────────

    async def action_find(self) -> None:
        from .find import FindScreen
        await self.app.push_screen(FindScreen(initial_query=""))

    def action_clear(self) -> None:
        if self.chat:
            self.chat.clear()
