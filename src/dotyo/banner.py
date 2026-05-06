"""The .Yo banner — braille border, block-letter .Yo in yo-client green.

Hand-crafted (not figlet) so the y has a proper tall lowercase descender
and the period block sits cleanly at the baseline. Colored solid green
(#25C75E — yo-client signature accent).
"""

from __future__ import annotations

from rich.console import Console, Group
from rich.text import Text

# ── Brand palette (matches yo-client/src/renderer/styles.css dark theme) ───
GREEN = "#25C75E"          # accent / signature green
GREEN_HOVER = "#2dd868"    # accent-hover
GREEN_DARK = "#1da34d"     # accent-dark
PURPLE = "#a855f7"         # secondary accent
PURPLE_DEEP = "#9333ea"
BLUE = "#4A9EFF"
WHITE = "#ffffff"
TEXT = "#e8e8e8"
GRAY = "#a0a0a0"           # text-2 — readable secondary
GRAY_FAINT = "#707070"     # text-3 — tertiary
BG = "#0a0a0a"
BORDER = "#2a2a2a"

# Backwards-compat aliases
CYAN = GREEN
CYAN_DIM = GREEN_DARK
MAGENTA = PURPLE
MAGENTA_DIM = PURPLE_DEEP

# ── Hand-crafted .Yo (lowercase y with a tall descender, block-letter o) ───
# 9 rows tall. The y stem (3 rows of "║" + 1 foot) extends below the o's
# baseline, giving it a proper lowercase descender.
_LOGO_LINES = [
    "     ██╗   ██╗                 ",
    "     ╚██╗ ██╔╝                 ",
    "      ╚████╔╝   ██████╗        ",
    "       ╚██╔╝   ██╔═══██╗       ",
    "        ██║    ██║   ██║       ",
    "  ██╗   ██║    ╚██████╔╝       ",
    "  ╚═╝   ╚═╝     ╚═════╝        ",
]

def _logo_indent() -> int:
    """Left padding of the logo's leftmost edge (the . dot row)."""
    return min(len(line) - len(line.lstrip()) for line in _LOGO_LINES)


def _logo_line(line: str) -> Text:
    """A logo line as-is, colored green."""
    return Text(line, style=f"bold {GREEN}")


def _left_row(parts: list[tuple[str, str | None]], left_pad: int = 0) -> Text:
    """Left-aligned row with optional indent."""
    text = Text(" " * left_pad)
    for t, style in parts:
        if style:
            text.append(t, style=style)
        else:
            text.append(t)
    return text


def _resolve_version(explicit: str | None) -> str:
    if explicit:
        return explicit
    try:
        from . import __version__  # type: ignore
        return __version__
    except Exception:
        return ""


def render_banner(version: str | None = None) -> Group:
    indent = _logo_indent()
    v = _resolve_version(version)

    body: list[Text] = []
    for line in _LOGO_LINES:
        body.append(_logo_line(line))
    # version directly under the .Yo, indented to match the dot
    body.append(_left_row([(f"v{v}", GRAY_FAINT)], left_pad=indent))
    return Group(Text(""), *body, Text(""))


def print_banner(version: str | None = None, console: Console | None = None) -> None:
    c = console or Console()
    c.print(render_banner(version))


def print_mini(console: Console | None = None) -> None:
    """One-line compact banner for sub-commands that print structured output."""
    c = console or Console()
    line = Text()
    line.append("the cypher engine", style=f"bold {WHITE}")
    line.append(" · ", style=GRAY_FAINT)
    line.append("powered by ", style=GRAY)
    line.append("glyphh ai", style=f"bold {PURPLE}")
    c.print(line)
