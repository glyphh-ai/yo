"""yo mcp — install + serve the yo MCP for use from regular Claude Code.

`yo mcp install` registers the yo MCP server with the user's local
Claude Code config so `yo.spawn`, `yo.spawn_parallel`, and
`yo.workers_online` become available tools in any `claude` session
(not just our orchestrator REPL).

`yo mcp serve` runs a stdio MCP server. Claude Code spawns this and
talks to it over stdin/stdout. Each tool handler proxies through to
yo-server via REST, using the access token saved at
~/.dotyo/config.json.

Flow:
  1. `yo login` — saves your token
  2. `yo mcp install` — adds an entry to Claude Code's MCP config
  3. From any `claude` session, the yo tools are available
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


def mcp_install_cmd(scope: str = "user") -> None:
    """Register the yo MCP with Claude Code.
    scope: 'user' (global) | 'project' (cwd's .mcp.json) | 'local'."""
    console = Console()

    yo_path = shutil.which("yo") or shutil.which("dotyo")
    if not yo_path:
        yo_path = f"{sys.executable} -m dotyo"

    claude_path = shutil.which("claude")
    if not claude_path:
        console.print(f"[red]✗ claude CLI not found on PATH[/]")
        console.print(f"[{GRAY}]install Claude Code first, then run [bold]claude /login[/{GRAY}][{GRAY}] and try again[/]")
        raise SystemExit(1)

    cmd = [
        claude_path, "mcp", "add", "yo",
        "--scope", scope,
        "--", *yo_path.split(), "mcp", "serve",
    ]
    console.print(f"[{GRAY}]→ {' '.join(cmd)}[/]")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            console.print()
            console.print(f"[green]✓ yo MCP installed for Claude Code ({scope} scope)[/]")
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
        else:
            console.print(f"[yellow]`claude mcp add` returned {result.returncode}[/]")
            if result.stderr:
                console.print(f"[{GRAY}]{result.stderr.strip()}[/]")
    except subprocess.TimeoutExpired:
        console.print(f"[yellow]`claude mcp add` timed out[/]")
    except Exception as e:
        console.print(f"[yellow]`claude mcp add` errored: {e}[/]")

    console.print()
    console.print(f"[{GRAY}]falling back to manual config — paste this into your CC mcp config:[/]")
    console.print()
    snippet = {
        "yo": {
            "command": yo_path.split()[0],
            "args": yo_path.split()[1:] + ["mcp", "serve"],
        }
    }
    console.print(Syntax(json.dumps(snippet, indent=2), "json", theme="ansi_dark", line_numbers=False))


def mcp_uninstall_cmd(scope: str = "user") -> None:
    console = Console()
    claude_path = shutil.which("claude")
    if not claude_path:
        console.print(f"[red]✗ claude CLI not found on PATH[/]")
        raise SystemExit(1)
    try:
        result = subprocess.run(
            [claude_path, "mcp", "remove", "yo", "--scope", scope],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            console.print(f"[green]✓[/] yo MCP removed from Claude Code ({scope} scope)")
        else:
            console.print(f"[yellow]`claude mcp remove` returned {result.returncode}[/]")
            if result.stderr:
                console.print(f"[{GRAY}]{result.stderr.strip()}[/]")
    except Exception as e:
        console.print(f"[red]✗ uninstall failed[/] — {e}")


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
                server_version="0.2.2",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )
