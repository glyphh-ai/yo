"""Status bar — single dim row docked at the bottom of every screen.

  ● connected · server api.yosup.dev · mcp ✓ · stack code,research · served 3

Watches reactive attrs on the app and re-renders when they change.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import RenderResult
from textual.widgets import Static

from ...banner import GREEN, GRAY, GRAY_FAINT


class StatusBar(Static):
    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        background: #0a0a0a;
        padding: 0 1;
    }
    """

    def on_mount(self) -> None:
        # Re-render on any state change the bar shows.
        self.watch(self.app, "connected", lambda *_: self.refresh())
        self.watch(self.app, "mcp_registered", lambda *_: self.refresh())
        self.watch(self.app, "served_count", lambda *_: self.refresh())
        self.watch(self.app, "inflight_count", lambda *_: self.refresh())

    def render(self) -> RenderResult:
        app = self.app
        line = Text()

        # connection dot + label
        if getattr(app, "connected", False):
            line.append("●", style=GREEN)
            line.append(" connected", style=GRAY_FAINT)
        else:
            line.append("●", style="#d4a017")
            line.append(" disconnected", style=GRAY_FAINT)

        line.append("  ·  ", style=GRAY_FAINT)
        line.append("server ", style=GRAY_FAINT)
        line.append(getattr(app, "server_host", "?"), style=GRAY)

        line.append("  ·  ", style=GRAY_FAINT)
        line.append("mcp ", style=GRAY_FAINT)
        if getattr(app, "mcp_registered", False):
            line.append("✓", style=GREEN)
        else:
            line.append("✗", style="#d44a4a")

        skills = (getattr(app, "capabilities", {}) or {}).get("skills") or []
        if skills:
            line.append("  ·  ", style=GRAY_FAINT)
            line.append("stack ", style=GRAY_FAINT)
            line.append(",".join(skills), style=GRAY)

        served = getattr(app, "served_count", 0)
        inflight = getattr(app, "inflight_count", 0)
        if served or inflight:
            line.append("  ·  ", style=GRAY_FAINT)
            line.append(f"served {served}", style=GRAY)
            if inflight:
                line.append(f" · {inflight} in-flight", style=GREEN)

        return line
