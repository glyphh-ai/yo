"""Textual-based TUI for `yo`.

Architecture:
  • DotyoApp — owns lifecycle, orchestrator client, worker SSE task,
    cross-screen state (user, capabilities, server_host, mcp_registered,
    connected, served_count, ...).
  • HomeScreen — chat REPL. Looks like the old console REPL but lives
    inside Textual so we can layer in the rich modes below.
  • FindScreen — search-as-you-type, ranked results, preview pane.
    Pushed by `/find`. Esc pops back home.
  • DropScreen — cypher cockpit. Live event stream + jammer roster.
    Pushed by `/drop <ref>`. Esc pops back home.

Slash commands are dispatched by HomeScreen.run_slash() and may push
screens. Other screens have their own narrow keybindings.
"""

from .app import DotyoApp, run_tui

__all__ = ["DotyoApp", "run_tui"]
