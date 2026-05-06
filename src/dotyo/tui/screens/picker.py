"""CapabilityPicker — small modal screen for editing the user's stack.

Multi-select list of skill tags + optional one-line blurb. Save → PUT
/api/me/capabilities, dismiss. Esc cancels.
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Header, Input, Label, SelectionList, Static
from textual.widgets.selection_list import Selection

from ...banner import GREEN, GRAY, GRAY_FAINT, WHITE
from ...lib.api import ApiError, put


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


class CapabilityPicker(Screen):
    BINDINGS = [
        Binding("escape", "cancel", "cancel"),
        Binding("ctrl+s", "save", "save"),
    ]

    DEFAULT_CSS = """
    CapabilityPicker {
        align: center middle;
        background: #0a0a0a 80%;
    }

    #picker_box {
        width: 70;
        height: auto;
        max-height: 30;
        background: #0a0a0a;
        border: tall #25C75E;
        padding: 1 2;
    }

    #picker_title {
        color: #ffffff;
        text-style: bold;
        margin-bottom: 1;
    }

    #picker_subtitle {
        color: #a0a0a0;
        margin-bottom: 1;
    }

    SelectionList {
        height: auto;
        max-height: 12;
        background: #0a0a0a;
        margin-bottom: 1;
    }

    #blurb_input {
        margin-top: 1;
        border: tall #707070;
    }

    #blurb_input:focus {
        border: tall #25C75E;
    }

    #picker_hint {
        color: #707070;
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="picker_box"):
            yield Label("edit capability stack", id="picker_title")
            yield Label(
                "pick what your AI is good at — others see this when matching collaborators",
                id="picker_subtitle",
            )
            current = set((self.app.capabilities or {}).get("skills") or [])
            yield SelectionList[str](
                *[Selection(f"{slug}  —  {desc}", slug, slug in current) for slug, desc in CAPABILITIES],
                id="skills_list",
            )
            yield Input(
                placeholder="optional one-line description",
                value=(self.app.capabilities or {}).get("blurb") or "",
                id="blurb_input",
            )
            yield Label("space to toggle  ·  ctrl+s to save  ·  esc to cancel", id="picker_hint")

    def on_mount(self) -> None:
        self.query_one("#skills_list", SelectionList).focus()

    async def action_save(self) -> None:
        skills_widget = self.query_one("#skills_list", SelectionList)
        blurb_widget = self.query_one("#blurb_input", Input)
        skills = list(skills_widget.selected) or ["code"]
        blurb = (blurb_widget.value or "").strip() or None
        try:
            result = await put(
                "/api/me/capabilities",
                json={"skills": skills, "blurb": blurb, "model": "claude-sonnet-4-5"},
            )
            self.app.capabilities = result
        except ApiError:
            self.app.capabilities = {"skills": skills, "blurb": blurb, "model": "claude-sonnet-4-5"}
        self.app.pop_screen()

    def action_cancel(self) -> None:
        self.app.pop_screen()
