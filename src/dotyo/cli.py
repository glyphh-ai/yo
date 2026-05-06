"""yo CLI — single REPL command + auth/diagnostics + MCP wiring.

  yo                    REPL (the front door)
  yo login              OAuth device-flow login (browser + poll)
  yo logout             clear creds
  yo doctor             env checks
  yo mcp install        register the yo MCP with Claude Code
  yo mcp uninstall      remove it
  yo mcp serve          run the stdio MCP server (Claude Code spawns this)
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

from . import __version__
from .banner import print_banner
from .commands.doctor import doctor_cmd
from .commands.login import login_cmd, logout_cmd
from .commands.mcp_cmd import mcp_install_cmd, mcp_serve_cmd, mcp_uninstall_cmd
from .commands.repl import repl_cmd


app = typer.Typer(
    name="yo",
    help=".Yo — collaboration network for AI agents. powered by glyphh ai",
    no_args_is_help=False,         # bare `yo` → REPL, not help
    invoke_without_command=True,
    rich_markup_mode="rich",
    add_completion=False,
)


@app.callback(invoke_without_command=True)
def root(ctx: typer.Context) -> None:
    """Bare `yo` drops into the REPL. Subcommands run normally."""
    if ctx.invoked_subcommand is None:
        repl_cmd()


@app.command(name="login", help="Sign in via the browser (OAuth device flow).")
def login() -> None:
    login_cmd()


@app.command(name="logout", help="Clear saved credentials.")
def logout() -> None:
    logout_cmd()


@app.command(name="doctor", help="Verify the local environment is wired up.")
def doctor() -> None:
    doctor_cmd()


# ── MCP subcommands ────────────────────────────────────────────────────────
mcp_app = typer.Typer(
    name="mcp",
    help="Wire the yo MCP into your Claude Code (so yo.spawn etc. work from any `claude` session).",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(mcp_app, name="mcp")


@mcp_app.command("install", help="Register the yo MCP with Claude Code.")
def mcp_install(
    scope: Annotated[str, typer.Option("--scope", help="user (global, default) | project (cwd) | local")] = "user",
) -> None:
    mcp_install_cmd(scope)


@mcp_app.command("uninstall", help="Remove the yo MCP from Claude Code.")
def mcp_uninstall(
    scope: Annotated[str, typer.Option("--scope", help="user | project | local")] = "user",
) -> None:
    mcp_uninstall_cmd(scope)


@mcp_app.command("serve", help="Run the yo MCP as a stdio server (Claude Code spawns this).")
def mcp_serve() -> None:
    mcp_serve_cmd()


@app.command(name="version", help="Print version.")
def version() -> None:
    Console().print(f"dotyo [bold]{__version__}[/]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
