"""dotyo doctor — verify the local environment is wired up."""

from __future__ import annotations

import asyncio
import sys

import httpx
from rich.console import Console
from rich.table import Table

from ..banner import CYAN, GRAY, MAGENTA, WHITE
from ..lib.cc_creds import find_cc_credentials
from ..lib.config import config_path, load_config


def _icon(ok: bool) -> str:
    return "[green]✓[/green]" if ok else "[red]✗[/red]"


async def _check_server(url: str) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(url)
            return r.status_code < 500, f"{url} → HTTP {r.status_code}"
    except Exception as e:
        return False, f"{url} — {type(e).__name__}: {e}"


async def _async_doctor() -> int:
    console = Console()
    cfg = load_config()

    rows: list[tuple[bool, str, str]] = []

    # Python version
    pyv = sys.version_info
    py_ok = (pyv.major, pyv.minor) >= (3, 12)
    rows.append((py_ok, "Python", f"{pyv.major}.{pyv.minor}.{pyv.micro} {'(>=3.12 ok)' if py_ok else '(need >=3.12)'}"))

    # CC credentials
    cc = find_cc_credentials()
    rows.append((
        cc.found,
        "Claude Code credentials",
        cc.location if cc.found else "not found — run `claude /login`",
    ))

    # yo-server reachability
    server_ok, server_detail = await _check_server(cfg.server_url)
    rows.append((server_ok, "yo-server reachable", server_detail))

    # Config + auth
    auth_state = "logged in" if cfg.access_token else "not logged in"
    rows.append((
        True,
        "config",
        f"{config_path()} ({auth_state})",
    ))

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(width=2)
    table.add_column(style=f"bold {WHITE}")
    table.add_column(style=f"{GRAY}")
    for ok, label, detail in rows:
        table.add_row(_icon(ok), label, detail)

    console.print(table)
    console.print()
    all_ok = all(ok for ok, _, _ in rows)
    if all_ok:
        console.print(f"[bold {CYAN}]all good[/]  ·  ready to run [bold {MAGENTA}]yo login[/]")
    else:
        console.print(f"[yellow]some checks failed — see above[/]")
    return 0 if all_ok else 1


def doctor_cmd() -> None:
    code = asyncio.run(_async_doctor())
    raise SystemExit(code)
