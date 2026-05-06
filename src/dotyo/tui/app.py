"""DotyoApp — the Textual app shell.

Owns long-lived state used by every screen:
  • orchestrator (Claude Agent SDK client) — opened on mount, closed on
    unmount. Wrapped in a worker task that holds the async-context-manager
    open for the app lifetime.
  • worker SSE listener — incoming spawn requests from other users.
  • connection / mcp / served counters, surfaced to the status bar via
    Textual reactive attrs.
  • user + capabilities + server_host snapshots from yo-server.

Screens reach app state via `self.app` (DotyoApp).
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from textual.app import App
from textual.reactive import reactive

from .. import __version__
from ..commands.mcp_cmd import is_yo_mcp_registered, register_yo_mcp
from ..lib.api import ApiError, get
from ..lib.cc_creds import find_cc_credentials
from ..lib.config import load_config
from ..lib.skill_install import install_skill
from ..network import _worker_listener, yo_mcp_allowed_tools, yo_mcp_config


def _host_of(url: str) -> str:
    try:
        h = urlparse(url).netloc or url
        return h or url
    except Exception:
        return url


def _setup_logger() -> logging.Logger:
    log_path = Path.home() / ".dotyo" / "logs" / "tui.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("dotyo.tui")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    handler = logging.handlers.RotatingFileHandler(log_path, maxBytes=10 * 1024 * 1024, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


# Shared TCSS — pulled from banner.py palette.
# Keep this short. Per-screen styles live in their own *.tcss-style strings.
APP_CSS = """
Screen {
    background: #0a0a0a;
    color: #e8e8e8;
}

#status_bar {
    dock: bottom;
    height: 1;
    background: #0a0a0a;
    color: #707070;
    padding: 0 1;
}

#status_bar .--ok    { color: #25C75E; }
#status_bar .--warn  { color: #d4a017; }
#status_bar .--err   { color: #d44a4a; }
#status_bar .--label { color: #a0a0a0; }
"""


class DotyoApp(App):
    """The .Yo TUI app."""

    CSS = APP_CSS
    TITLE = "yo"
    SUB_TITLE = ".Yo collaboration network"

    BINDINGS = [
        ("ctrl+q", "quit_app", "quit"),
        ("ctrl+c", "quit_app", "quit"),
    ]

    # ── Reactive state — status bar + screens watch these ──────────────────
    connected: reactive[bool] = reactive(False)
    mcp_registered: reactive[bool] = reactive(False)
    served_count: reactive[int] = reactive(0)
    inflight_count: reactive[int] = reactive(0)

    def __init__(self, *, deep_link: tuple[str, ...] | None = None) -> None:
        """deep_link is an optional ('drop', '<ref>') / ('find', '<q>') tuple
        that pushes a screen on top of HomeScreen at boot, so `yo drop foo`
        lands the user directly in the cockpit."""
        super().__init__()
        self.user: dict[str, Any] = {}
        self.capabilities: dict[str, Any] = {"skills": [], "blurb": None, "model": None}
        self.server_host: str = ""
        self.shutdown_event = asyncio.Event()
        self.orchestrator: Any = None  # set by _orchestrator_lifetime
        self._orchestrator_ready = asyncio.Event()
        self.logger = _setup_logger()
        self._listener_task: asyncio.Task | None = None
        self._orch_task: asyncio.Task | None = None
        self._deep_link = deep_link

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def on_mount(self) -> None:
        # 1. Install the dotyo-network skill into ~/.claude/skills/ (idempotent)
        install_skill()

        # 2. Auth + capabilities + server host
        cfg = load_config()
        self.server_host = _host_of(cfg.server_url)
        if not cfg.access_token:
            self.exit(message="not signed in — run `yo login` first")
            return
        cc = find_cc_credentials()
        if not cc.found:
            self.exit(message="Claude Code credentials not found — run `claude /login` first")
            return

        try:
            self.user = await get("/api/auth/me")
        except ApiError as e:
            self.exit(message=f"auth check failed: {e}")
            return

        try:
            caps = await get("/api/me/capabilities")
            if caps.get("skills"):
                self.capabilities = caps
        except ApiError:
            pass

        # 3. Auto-register the yo MCP with Claude Code (silent, idempotent)
        try:
            if is_yo_mcp_registered("user"):
                self.mcp_registered = True
            else:
                ok, _ = register_yo_mcp("user")
                self.mcp_registered = ok
        except Exception:
            self.mcp_registered = False

        # 4. Worker SSE listener — your CC available to others
        self._listener_task = asyncio.create_task(self._run_worker_listener())

        # 5. Orchestrator SDK client (long-lived)
        self._orch_task = asyncio.create_task(self._orchestrator_lifetime())

        # 6. If user has no capability profile, save a default silently.
        #    They can re-run the picker via `/me edit` from the chat.
        if not self.capabilities.get("skills"):
            try:
                from ..lib.api import put as _put
                self.capabilities = await _put(
                    "/api/me/capabilities",
                    json={"skills": ["code"], "blurb": None, "model": "claude-sonnet-4-5"},
                )
            except ApiError:
                self.capabilities = {"skills": ["code"], "blurb": None, "model": "claude-sonnet-4-5"}

        # 7. Show home screen — and any deep-link target on top.
        from .screens.home import HomeScreen
        await self.push_screen(HomeScreen())
        if self._deep_link:
            cmd, *rest = self._deep_link
            if cmd == "drop" and rest:
                from .screens.drop import DropScreen
                await self.push_screen(DropScreen(ref=rest[0]))
            elif cmd == "find":
                from .screens.find import FindScreen
                await self.push_screen(FindScreen(initial_query=" ".join(rest)))

    async def on_unmount(self) -> None:
        self.shutdown_event.set()
        for task in (self._listener_task, self._orch_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    # ── Background tasks ───────────────────────────────────────────────────

    async def _run_worker_listener(self) -> None:
        def on_event(evt: dict[str, Any]) -> None:
            kind = evt.get("kind")
            if kind == "connected":
                self.connected = True
            elif kind == "disconnected":
                self.connected = False
            elif kind == "auth_lost":
                self.shutdown_event.set()
                self.exit(message="session expired — run `yo login` again")
            elif kind == "incoming_started":
                self.inflight_count = self.inflight_count + 1
                self._notify_screens("incoming_started", evt)
            elif kind == "incoming_done":
                self.inflight_count = max(0, self.inflight_count - 1)
                self.served_count = self.served_count + 1
                self._notify_screens("incoming_done", evt)
            elif kind == "incoming_failed":
                self.inflight_count = max(0, self.inflight_count - 1)
                self._notify_screens("incoming_failed", evt)

        try:
            await _worker_listener(
                on_event,
                self.capabilities.get("skills") or [],
                self.logger,
                self.shutdown_event,
            )
        except asyncio.CancelledError:
            return
        except Exception as e:
            self.logger.error("worker listener crashed: %s", e)

    async def _orchestrator_lifetime(self) -> None:
        """Hold the Claude Agent SDK session open for the app's lifetime."""
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        from ..commands.repl import ORCHESTRATOR_SYSTEM_PROMPT  # reuse the prompt

        yo_server = yo_mcp_config()
        options = ClaudeAgentOptions(
            system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
            mcp_servers={"yo": yo_server},
            allowed_tools=yo_mcp_allowed_tools() + [
                "Read", "Write", "Edit", "Bash", "WebFetch", "Grep", "Glob",
            ],
            permission_mode="bypassPermissions",
        )

        try:
            async with ClaudeSDKClient(options=options) as client:
                self.orchestrator = client
                self._orchestrator_ready.set()
                await self.shutdown_event.wait()
        except asyncio.CancelledError:
            return
        except Exception as e:
            self.logger.error("orchestrator session crashed: %s", e)
            self._orchestrator_ready.set()  # unblock waiters; they'll see None

    async def wait_for_orchestrator(self, timeout: float = 30.0) -> Any:
        """Block until the orchestrator client is up. Returns it (or None on failure)."""
        try:
            await asyncio.wait_for(self._orchestrator_ready.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        return self.orchestrator

    # ── Cross-screen messaging ─────────────────────────────────────────────

    def _notify_screens(self, kind: str, evt: dict[str, Any]) -> None:
        """Forward worker-SSE state changes to whichever screen wants them.

        Any screen with `on_app_event(kind, evt)` will get called. Cheap;
        only HomeScreen subscribes today (incoming-spawn ticker).
        """
        try:
            scr = self.screen
            handler = getattr(scr, "on_app_event", None)
            if handler:
                handler(kind, evt)
        except Exception:
            pass

    # ── Actions ────────────────────────────────────────────────────────────

    def action_quit_app(self) -> None:
        self.exit()


def run_tui(deep_link: tuple[str, ...] | None = None) -> None:
    """Entry point — launches the TUI. Optional deep_link pushes a screen
    on top of HomeScreen at boot. Used by `yo drop <ref>` / `yo find <q>`."""
    DotyoApp(deep_link=deep_link).run()
