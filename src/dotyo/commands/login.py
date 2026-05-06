"""dotyo login / logout — OAuth 2.0 device authorization grant.

`yo login`:
  1. POST /api/auth/device/start → get device_code + user_code + verification_uri
  2. open browser to verification_uri (with user_code prefilled)
  3. show user_code prominently in the terminal in case the browser
     doesn't auto-prefill or the user is on a remote shell
  4. poll POST /api/auth/device/poll until tokens come back
  5. save tokens, drop into REPL (caller handles)

No email/password. No paste-a-JWT. The platform handles signup, login,
and subscription onboarding in the browser.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import time
import webbrowser
from typing import Any

import httpx
from rich.console import Console

from ..banner import GRAY, GRAY_FAINT, GREEN, MAGENTA, PURPLE, WHITE
from ..lib.config import clear_auth, load_config, save_config


def login_cmd() -> None:
    """Synchronous entry point for `yo login`. On success drops straight
    into the REPL — no need to re-launch."""
    code = asyncio.run(_async_login())
    if code != 0:
        raise SystemExit(code)
    # Successful login → hand off to REPL. Don't SystemExit; let the REPL run.
    from .repl import repl_cmd
    repl_cmd()  # has its own SystemExit


async def _async_login() -> int:
    console = Console()
    cfg = load_config()
    server = cfg.server_url.rstrip("/")

    console.print()
    console.print(f"[bold {GREEN}].Yo[/]  [{GRAY}]· logging you in[/]")
    console.print(f"[{GRAY}]server: {server}[/]")
    console.print()

    # Step 1: start device flow
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{server}/api/auth/device/start", json={
                "client_info": {
                    "hostname": socket.gethostname(),
                    "platform": sys.platform,
                    "yo_version": "0.2.0",
                },
            })
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        console.print(f"[red]✗ couldn't start device auth[/] — {type(e).__name__}: {e}")
        return 1

    device_code = data["device_code"]
    user_code = data["user_code"]
    verification_uri = data.get("verification_uri_complete") or data["verification_uri"]
    interval = max(2, int(data.get("interval", 3)))
    expires_in = int(data.get("expires_in", 600))

    # Step 2: show code + open browser
    console.print(f"  visit:  [bold {GREEN}]{data['verification_uri']}[/]")
    console.print(f"  enter:  [bold {WHITE}]{user_code}[/]")
    console.print()
    try:
        webbrowser.open(verification_uri)
    except Exception:
        pass
    console.print(f"[{GRAY}]opening your browser…[/]")
    console.print(f"[{GRAY_FAINT}](if it doesn't open, paste the URL above into your browser)[/]")
    console.print()

    # Step 3: poll
    deadline = time.time() + expires_in
    spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    sp_i = 0

    async with httpx.AsyncClient(timeout=15) as c:
        with console.status(f"[{GRAY}]waiting for confirmation…[/]", spinner="dots") as status:
            while time.time() < deadline:
                await asyncio.sleep(interval)
                try:
                    r = await c.post(f"{server}/api/auth/device/poll", json={"device_code": device_code})
                    body = r.json()
                except Exception as e:
                    status.update(f"[yellow]network hiccup, retrying… ({type(e).__name__})[/]")
                    continue

                if r.is_success and body.get("ok"):
                    return _finish_login(console, body)
                if body.get("pending"):
                    sp_i = (sp_i + 1) % len(spinner)
                    continue
                # any other error = abort
                console.print()
                console.print(f"[red]✗ auth failed[/] — {body.get('error', 'unknown')}: {body.get('message', '')}")
                return 1

    console.print()
    console.print(f"[yellow]✗ timed out after {expires_in}s — try again[/]")
    return 1


def _finish_login(console: Console, body: dict[str, Any]) -> int:
    user = body.get("user") or {}
    sub = body.get("subscription") or {}

    save_config({
        "access_token": body.get("accessToken"),
        "refresh_token": body.get("refreshToken"),
        "user_id": user.get("id"),
        "email": user.get("email"),
    })

    console.print()
    console.print(f"[green]✓[/] signed in as [bold {MAGENTA}]{user.get('email', '?')}[/]")

    # Show subscription state
    if sub.get("entitled"):
        days_left = sub.get("trial_days_remaining")
        if days_left is not None:
            console.print(f"  [{GRAY}]trial: {days_left} day{'s' if days_left != 1 else ''} remaining[/]")
        else:
            console.print(f"  [{GRAY}]subscription: active[/]")
    else:
        console.print(f"[yellow]  trial expired — subscribe to continue hosting cyphers[/]")
        sub_url = sub.get("subscribe_url")
        if sub_url:
            console.print(f"  [{GRAY}]→ {sub_url}[/]")

    console.print()
    return 0


def logout_cmd() -> None:
    clear_auth()
    Console().print("[green]✓[/] signed out")
