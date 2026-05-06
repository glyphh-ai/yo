"""ASCII .Yo logo as a Textual widget.

Reuses the hand-crafted block-letter logo from dotyo.banner. Static,
center-aligned, version under the dot. Mounted once at the top of
HomeScreen on first paint.
"""

from __future__ import annotations

from rich.console import Group
from rich.text import Text
from textual.widgets import Static

from ...banner import _LOGO_LINES, _logo_indent, GREEN, GRAY_FAINT
from ... import __version__


class Banner(Static):
    DEFAULT_CSS = """
    Banner {
        height: auto;
        padding: 1 2 1 2;
        content-align: left middle;
    }
    """

    def render(self) -> Group:
        body: list[Text] = [Text("")]
        for line in _LOGO_LINES:
            body.append(Text(line, style=f"bold {GREEN}"))
        # version directly under the .Yo, indented to match the dot
        indent = _logo_indent()
        body.append(Text(" " * indent + f"v{__version__}", style=GRAY_FAINT))
        body.append(Text(""))
        return Group(*body)
