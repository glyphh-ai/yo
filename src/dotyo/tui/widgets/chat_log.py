"""Chat scrollback for HomeScreen.

Wraps Textual's RichLog with helpers tailored to:
  • user prompts (echo with `yo>` prefix, dim color)
  • orchestrator markdown chunks (rendered via rich.markdown.Markdown)
  • tool-use blocks (single-line green badge, like the old REPL)
  • result-message tail (token counter)

Scrolls to bottom on every write so the user always sees the newest line.
"""

from __future__ import annotations

from rich.markdown import Markdown
from rich.text import Text
from textual.widgets import RichLog

from ...banner import GREEN, GRAY, GRAY_FAINT, MAGENTA


class ChatLog(RichLog):
    DEFAULT_CSS = """
    ChatLog {
        height: 1fr;
        background: #0a0a0a;
        border: none;
        padding: 0 2;
    }
    """

    def __init__(self) -> None:
        super().__init__(wrap=True, markup=True, highlight=False, auto_scroll=True)

    def write_user(self, text: str) -> None:
        line = Text()
        line.append("yo> ", style=f"bold {GREEN}")
        line.append(text, style="#e8e8e8")
        self.write(line)

    def write_assistant_md(self, md_text: str) -> None:
        if not md_text.strip():
            return
        try:
            self.write(Markdown(md_text.strip()))
        except Exception:
            self.write(Text(md_text.strip()))

    def write_tool(self, name: str, summary: str | None = None) -> None:
        line = Text("  ")
        line.append("🛠 ", style=GREEN)
        line.append(name, style=f"bold {GREEN}")
        if summary:
            line.append("  ", style=GRAY_FAINT)
            line.append(summary, style=GRAY)
        self.write(line)

    def write_result_tail(self, in_t: int, out_t: int) -> None:
        line = Text("  ")
        line.append(f"· {in_t}+{out_t} tok", style=GRAY_FAINT)
        self.write(line)

    def write_status(self, text: str, *, kind: str = "info") -> None:
        """Inline status line: 'kind' picks the color (info/ok/warn/err)."""
        color = {
            "info": GRAY,
            "ok": GREEN,
            "warn": "#d4a017",
            "err": "#d44a4a",
        }.get(kind, GRAY)
        self.write(Text(text, style=color))

    def write_blank(self) -> None:
        self.write("")

    def write_dim(self, text: str) -> None:
        self.write(Text(text, style=GRAY_FAINT))
