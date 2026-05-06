"""yo CLI — single REPL command + auth/diagnostics.

Four commands. That's it.

  yo            REPL (the front door — opens after login)
  yo login      OAuth device-flow login (browser + poll)
  yo logout     clear creds
  yo doctor     env checks
"""

from __future__ import annotations

import typer
from rich.console import Console

from . import __version__
from .banner import print_banner
from .commands.doctor import doctor_cmd
from .commands.login import login_cmd, logout_cmd
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


@app.command(name="version", help="Print version.")
def version() -> None:
    Console().print(f"dotyo [bold]{__version__}[/]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
