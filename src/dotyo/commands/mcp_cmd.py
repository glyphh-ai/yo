"""yo mcp — install + serve the yo MCP for use from regular Claude Code.

The REPL auto-registers the MCP with Claude Code on first launch. After
that, any `claude` session has `mcp__yo__spawn` and friends available —
CC spawns `yo mcp serve` as a subprocess on demand and talks to it over
stdin/stdout. Tool handlers proxy to yo-server via REST using the access
token saved at ~/.dotyo/config.json. Data stays on the user's machine.

Public surface:
  • `register_yo_mcp(scope)` / `unregister_yo_mcp(scope)` — silent helpers
    used by the REPL on startup and by the `/mcp` slash commands.
  • `is_yo_mcp_registered(scope)` — fast check (parses `claude mcp list`).
  • `mcp_serve_cmd()` — the stdio server CC spawns. Wired as
    `yo mcp serve` so the registration target is stable.
  • `mcp_install_cmd` / `mcp_uninstall_cmd` — kept for users who prefer
    running the install from outside the REPL.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from ..banner import GRAY, GREEN, WHITE


# ── Resolution helpers ─────────────────────────────────────────────────────

def _yo_command() -> list[str]:
    """The argv that Claude Code should spawn to run `yo mcp serve`.

    Prefers the `yo`/`dotyo` console script on PATH (works when installed
    via pipx/uv/pip). Falls back to `python -m dotyo` for source checkouts.
    """
    yo_path = shutil.which("yo") or shutil.which("dotyo")
    if yo_path:
        return [yo_path]
    return [sys.executable, "-m", "dotyo"]


def _claude_path() -> str | None:
    return shutil.which("claude")


# ── Status / install / uninstall (silent helpers) ──────────────────────────

def is_yo_mcp_registered(scope: str = "user") -> bool:
    """True iff Claude Code already has a `yo` MCP entry at this scope.

    We parse `claude mcp list`. Format varies by CC version, so we just
    look for the literal name `yo:` at the start of a line.
    """
    claude = _claude_path()
    if not claude:
        return False
    try:
        result = subprocess.run(
            [claude, "mcp", "list"],
            capture_output=True, text=True, timeout=8,
        )
    except Exception:
        return False
    if result.returncode != 0:
        return False
    for line in (result.stdout or "").splitlines():
        s = line.strip()
        # Common formats: "yo: <command>", "yo (user)", "  yo  …"
        if s.startswith("yo:") or s.startswith("yo ") or s == "yo":
            return True
    return False


def register_yo_mcp(scope: str = "user") -> tuple[bool, str]:
    """Run `claude mcp add yo …`. Returns (ok, message)."""
    claude = _claude_path()
    if not claude:
        return False, "claude CLI not found on PATH"
    cmd = [
        claude, "mcp", "add", "yo",
        "--scope", scope,
        "--", *_yo_command(), "mcp", "serve",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return False, "`claude mcp add` timed out"
    except Exception as e:
        return False, f"`claude mcp add` errored: {e}"
    if result.returncode == 0:
        return True, "registered"
    err = (result.stderr or result.stdout or "").strip()
    return False, f"claude mcp add returned {result.returncode}: {err[:200]}"


def unregister_yo_mcp(scope: str = "user") -> tuple[bool, str]:
    claude = _claude_path()
    if not claude:
        return False, "claude CLI not found on PATH"
    try:
        result = subprocess.run(
            [claude, "mcp", "remove", "yo", "--scope", scope],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as e:
        return False, f"`claude mcp remove` errored: {e}"
    if result.returncode == 0:
        return True, "removed"
    err = (result.stderr or result.stdout or "").strip()
    return False, f"claude mcp remove returned {result.returncode}: {err[:200]}"


# ── CLI subcommand entry points ────────────────────────────────────────────

def mcp_install_cmd(scope: str = "user") -> None:
    console = Console()

    if not _claude_path():
        console.print(f"[red]✗ claude CLI not found on PATH[/]")
        console.print(f"[{GRAY}]install Claude Code first, then run [bold]claude /login[/{GRAY}][{GRAY}] and try again[/]")
        raise SystemExit(1)

    ok, msg = register_yo_mcp(scope)
    if ok:
        console.print()
        console.print(f"[green]✓ yo MCP registered with Claude Code ({scope} scope)[/]")
        console.print()
        console.print(Panel(
            f"From any [bold]claude[/] session you now have:\n"
            f"  • [bold {GREEN}]mcp__yo__spawn[/](prompt, capabilities?)         dispatch one prompt\n"
            f"  • [bold {GREEN}]mcp__yo__spawn_parallel[/](prompts)              fan out concurrently\n"
            f"  • [bold {GREEN}]mcp__yo__workers_online[/]()                     who's connected\n"
            f"\n"
            f"[{GRAY}]Try it:[/]\n"
            f"  [bold]claude[/]\n"
            f"  [{GRAY}]> spawn 3 collaborators to summarize this README[/]",
            border_style=GREEN,
            title=f"[bold {WHITE}]ready[/]",
        ))
        return

    console.print(f"[yellow]{msg}[/]")
    console.print()
    console.print(f"[{GRAY}]falling back to manual config — paste this into your CC mcp config:[/]")
    console.print()
    argv = _yo_command()
    snippet = {
        "yo": {
            "command": argv[0],
            "args": argv[1:] + ["mcp", "serve"],
        }
    }
    console.print(Syntax(json.dumps(snippet, indent=2), "json", theme="ansi_dark", line_numbers=False))


def mcp_uninstall_cmd(scope: str = "user") -> None:
    console = Console()
    if not _claude_path():
        console.print(f"[red]✗ claude CLI not found on PATH[/]")
        raise SystemExit(1)
    ok, msg = unregister_yo_mcp(scope)
    if ok:
        console.print(f"[green]✓[/] yo MCP removed from Claude Code ({scope} scope)")
    else:
        console.print(f"[yellow]{msg}[/]")


# ── Stdio server (Claude Code spawns this) ─────────────────────────────────

def mcp_serve_cmd() -> None:
    """Stdio MCP server. Claude Code spawns this and talks via stdin/stdout."""
    import asyncio
    asyncio.run(_async_serve())


async def _async_serve() -> None:
    from mcp.server.lowlevel import Server, NotificationOptions
    from mcp.server.models import InitializationOptions
    from mcp.server.stdio import stdio_server
    import mcp.types as types

    server = Server("yo")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="spawn",
                description=(
                    "Dispatch a single prompt to one collaborator on the .Yo network. "
                    "Returns the collaborator's response."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string"},
                        "model": {"type": "string"},
                        "capabilities": {"type": "string", "description": "comma-separated, e.g. 'code,research'"},
                        "cypher_id": {"type": "string"},
                        "timeout_ms": {"type": "integer"},
                    },
                    "required": ["prompt"],
                },
            ),
            types.Tool(
                name="spawn_parallel",
                description=(
                    "Fan out N prompts concurrently across the .Yo network. "
                    "`prompts` MUST be a JSON array of strings."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "prompts": {"type": "string", "description": "JSON array of prompt strings"},
                        "model": {"type": "string"},
                        "capabilities": {"type": "string"},
                        "timeout_ms": {"type": "integer"},
                    },
                    "required": ["prompts"],
                },
            ),
            types.Tool(
                name="workers_online",
                description="List collaborators connected to the .Yo network and their advertised capabilities.",
                inputSchema={
                    "type": "object",
                    "properties": {"capabilities": {"type": "string"}},
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
        from ..lib.api import ApiError, get, post
        args = arguments or {}

        try:
            if name == "spawn":
                payload: dict[str, Any] = {"prompt": args["prompt"]}
                for k in ("model", "cypher_id"):
                    if args.get(k):
                        payload[k] = args[k]
                if args.get("timeout_ms"):
                    payload["timeout_ms"] = int(args["timeout_ms"])
                if args.get("capabilities"):
                    payload["capabilities"] = [s.strip() for s in str(args["capabilities"]).split(",") if s.strip()]
                res = await post("/api/spawn", json=payload)
                text = res.get("result", "") if isinstance(res, dict) else str(res)
                in_t = res.get("input_tokens", 0) if isinstance(res, dict) else 0
                out_t = res.get("output_tokens", 0) if isinstance(res, dict) else 0
                worker = res.get("worker_id", "?") if isinstance(res, dict) else "?"
                return [types.TextContent(type="text", text=f"{text}\n\n---\n[worker={worker[:8]} tokens={in_t}+{out_t}]")]

            if name == "spawn_parallel":
                import asyncio as _asyncio
                import json as _json
                raw = args.get("prompts", "[]")
                try:
                    prompts = _json.loads(raw) if isinstance(raw, str) else list(raw)
                except _json.JSONDecodeError:
                    return [types.TextContent(type="text", text="ERROR: 'prompts' must be a JSON array of strings")]
                base: dict[str, Any] = {}
                if args.get("model"):
                    base["model"] = args["model"]
                if args.get("timeout_ms"):
                    base["timeout_ms"] = int(args["timeout_ms"])
                if args.get("capabilities"):
                    base["capabilities"] = [s.strip() for s in str(args["capabilities"]).split(",") if s.strip()]

                async def one(p: str) -> str:
                    try:
                        r = await post("/api/spawn", json={**base, "prompt": p})
                    except ApiError as e:
                        return f"[ERROR {e.status}] {e.message}"
                    text = r.get("result", "") if isinstance(r, dict) else str(r)
                    in_t = r.get("input_tokens", 0) if isinstance(r, dict) else 0
                    out_t = r.get("output_tokens", 0) if isinstance(r, dict) else 0
                    worker = (r.get("worker_id", "?") or "?")[:8] if isinstance(r, dict) else "?"
                    return f"[{worker} tok={in_t}+{out_t}]\n{text}"

                results = await _asyncio.gather(*[one(p) for p in prompts])
                joined = "\n\n---\n\n".join(f"## Result {i+1}\n{r}" for i, r in enumerate(results))
                return [types.TextContent(type="text", text=joined)]

            if name == "workers_online":
                r = await get("/api/spawn/workers")
                workers = r.get("workers", []) if isinstance(r, dict) else []
                if not workers:
                    return [types.TextContent(type="text", text="no collaborators online (your scope)")]
                lines = [f"connected workers: {len(workers)}"]
                filter_caps = []
                if args.get("capabilities"):
                    filter_caps = [c.strip() for c in str(args["capabilities"]).split(",") if c.strip()]
                for w in workers:
                    caps = w.get("capabilities", []) or []
                    if filter_caps and not any(fc in caps for fc in filter_caps):
                        continue
                    cap_str = ", ".join(caps) if caps else "any"
                    lines.append(
                        f"  • {w.get('name', '?')} ({(w.get('worker_id', '?') or '?')[:8]}) "
                        f"— inflight {w.get('inflight', 0)}/{w.get('max_concurrent', 1)} "
                        f"— capabilities: {cap_str}"
                    )
                return [types.TextContent(type="text", text="\n".join(lines))]

            return [types.TextContent(type="text", text=f"unknown tool: {name}")]
        except ApiError as e:
            return [types.TextContent(type="text", text=f"yo.{name} failed: [{e.status}] {e.message}")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"yo.{name} error: {type(e).__name__}: {e}")]

    async with stdio_server() as (reader, writer):
        await server.run(
            reader,
            writer,
            InitializationOptions(
                server_name="yo",
                server_version="0.2.4",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )
