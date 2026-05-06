"""dotyo REPL — `yo` with no arguments.

Single conversational surface. Type prompts to talk to your Claude
(loaded with the yo MCP so it can fan work out across the network).
Slash commands handle meta operations:

  /help                    list of commands
  /quit, /q, exit          leave the REPL
  /clear                   clear screen
  /me                      your profile
  /me edit                 re-run capability wizard
  /online                  collaborators online right now
  /host "<goal>"           create a cypher
  /find [query]            discover public cyphers
  /join <id>               offer your AI to a cypher
  /leave <id>              stop offering
  /cyphers                 cyphers you're in / hosting
  /wrap <id>               wrap a cypher you host

Background: the REPL also runs a worker SSE listener so other users'
spawn calls land at your CC while you're online. Heads-up status lines
fire when an incoming spawn starts/completes.
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import shlex
import signal
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from ..banner import GRAY, GRAY_FAINT, GREEN, MAGENTA, PURPLE, WHITE, print_banner
from ..lib.api import ApiError, get, post, put
from ..lib.cc_creds import find_cc_credentials
from ..lib.config import load_config, save_config
from ..network import yo_mcp_allowed_tools, yo_mcp_config, _worker_listener


# Curated capability list — first-run wizard pulls from this
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


ORCHESTRATOR_SYSTEM_PROMPT = """\
You are running inside a user's `yo` REPL — a terminal companion for
the .Yo collaboration network. Behave like Claude Code: helpful,
concise, lean on your tools, output that fits a terminal.

# Tools available to you

## Local (this machine)
Your normal Claude Code toolkit: Read, Write, Edit, Bash, WebFetch,
Grep, Glob, TodoWrite, etc. Plus any user-installed agent skills.

## .Yo network (other people's Claudes are reachable through it)
  • mcp__yo__spawn(prompt, capabilities?, cypher_id?, timeout_ms?)
      — dispatch one prompt to a connected collaborator. Returns
        their response.
  • mcp__yo__spawn_parallel(prompts, capabilities?, ...)
      — fan out concurrently. `prompts` MUST be a JSON array of
        strings.
  • mcp__yo__workers_online(capabilities?)
      — see who's connected and what they're advertising.

# REPL slash commands (the user types these directly — handled by yo, not by you)

When the user asks "what can I do?" or "help" or "what is yo", mention
these alongside your tools so they see the full surface:

  /help              show the slash-command list
  /quit, /q          exit the REPL
  /clear             clear the screen
  /me                show profile (capabilities, cyphers)
  /me edit           re-run the capability wizard
  /online            list collaborators online right now
  /host "<goal>"     create a discoverable cypher
  /find [query]      browse public cyphers seeking your skills
  /join <id>         offer your AI to a cypher
  /leave <id>        stop offering
  /cyphers           cyphers you're in / hosting
  /wrap <id>         wrap a cypher you host

# When to use the network
  • Independent subtasks → spawn_parallel.
  • Specialist work matching a capability tag → spawn with `capabilities`
    (one of: code, research, writing, design, data, planning, review, ops).
  • Quick one-shot answers → just reply directly.

# Style
Concise, terminal-friendly markdown. Code blocks for code. Don't pad.
Don't editorialize. The user is typing — give them signal.

If the user asks "help" / "what can I do" / similar, give them a
short overview that covers (a) your local tools, (b) the .Yo network
tools, and (c) the REPL slash commands above.
"""


def repl_cmd() -> None:
    raise SystemExit(asyncio.run(_async_repl()))


def _setup_logger() -> logging.Logger:
    log_path = Path.home() / ".dotyo" / "logs" / "repl.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("dotyo.repl")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    handler = logging.handlers.RotatingFileHandler(log_path, maxBytes=10 * 1024 * 1024, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


# ── Pre-flight & first-run ────────────────────────────────────────────────

async def _ensure_authed_and_set_up(console: Console) -> dict[str, Any] | None:
    cfg = load_config()
    if not cfg.access_token:
        console.print(f"[yellow]not signed in[/] — run [bold]yo login[/] first")
        return None

    cc = find_cc_credentials()
    if not cc.found:
        console.print(f"[red]✗ Claude Code credentials not found[/]")
        console.print(f"[{GRAY}]run `claude /login` first so the SDK can authenticate[/]")
        return None

    # Verify session + fetch user
    try:
        me = await get("/api/auth/me")
    except ApiError as e:
        if e.status == 401:
            console.print(f"[yellow]session expired[/] — run [bold]yo login[/] again")
            return None
        console.print(f"[red]✗ couldn't reach server[/] — {e}")
        return None

    # Fetch capability profile; first-run if not yet set
    try:
        caps = await get("/api/me/capabilities")
    except ApiError:
        caps = {"skills": [], "blurb": None, "model": None}

    if not caps.get("skills"):
        caps = await _capability_wizard(console)

    return {"user": me, "capabilities": caps}


async def _capability_wizard(console: Console) -> dict[str, Any]:
    console.print()
    console.print(f"[bold {WHITE}]quick setup[/]  [{GRAY}](you can edit this later with /me edit)[/]")
    console.print(f"[{GRAY}]pick what your AI is good at — others will see this when matching collaborators[/]")
    console.print()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style=f"bold {GREEN}", width=12)
    table.add_column(style=f"{GRAY}")
    for slug, blurb in CAPABILITIES:
        table.add_row(slug, blurb)
    console.print(table)
    console.print()

    raw = Prompt.ask("pick 1–5 (comma-separated)", default="code,research")
    picked = [s.strip().lower() for s in raw.split(",") if s.strip().lower() in {c[0] for c in CAPABILITIES}][:5]
    if not picked:
        picked = ["code"]

    blurb = Prompt.ask("one-line description (optional)", default="").strip() or None

    try:
        result = await put("/api/me/capabilities", json={"skills": picked, "blurb": blurb, "model": "claude-sonnet-4-5"})
        console.print()
        console.print(f"[green]✓[/] saved — your stack: [bold {GREEN}]{', '.join(picked)}[/]")
        console.print()
        return result
    except ApiError as e:
        console.print(f"[yellow]couldn't save capabilities ({e})[/] — using locally only")
        return {"skills": picked, "blurb": blurb, "model": "claude-sonnet-4-5"}


# ── REPL main loop ────────────────────────────────────────────────────────

class ReplState:
    def __init__(self, user: dict[str, Any], caps: dict[str, Any]) -> None:
        self.user = user
        self.capabilities = caps
        self.connected = False
        self.connection_error: str | None = None
        self.incoming_count = 0
        self.incoming_inflight = 0
        self.last_incoming_summary: str | None = None
        self.current_cypher_id: str | None = None
        self.shutdown_event = asyncio.Event()


async def _async_repl() -> int:
    console = Console()
    print_banner()

    setup = await _ensure_authed_and_set_up(console)
    if setup is None:
        return 1

    state = ReplState(setup["user"], setup["capabilities"])
    logger = _setup_logger()

    # Background worker listener — your CC is now available to others.
    def on_event(evt: dict[str, Any]) -> None:
        kind = evt.get("kind")
        if kind == "connected":
            state.connected = True
            state.connection_error = None
        elif kind == "disconnected":
            state.connected = False
            state.connection_error = evt.get("error")
        elif kind == "auth_lost":
            state.shutdown_event.set()
        elif kind == "incoming_started":
            state.incoming_inflight += 1
            console.print(f"\n[{GRAY_FAINT}]→ incoming spawn: {evt.get('prompt', '')[:60]}…[/]")
        elif kind == "incoming_done":
            state.incoming_inflight = max(0, state.incoming_inflight - 1)
            state.incoming_count += 1
            elapsed = evt.get("elapsed", 0)
            in_t = evt.get("input_tokens", 0)
            out_t = evt.get("output_tokens", 0)
            console.print(f"[{GRAY_FAINT}]✓ served ({in_t}+{out_t} tok, {elapsed:.1f}s)[/]")
        elif kind == "incoming_failed":
            state.incoming_inflight = max(0, state.incoming_inflight - 1)
            console.print(f"[red]✗ spawn failed: {evt.get('error', '?')}[/]")

    listener_task = asyncio.create_task(_worker_listener(
        on_event,
        state.capabilities.get("skills", []),
        logger,
        state.shutdown_event,
    ))

    # Set up the orchestrator SDK
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

    yo_server = yo_mcp_config()
    options = ClaudeAgentOptions(
        system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
        mcp_servers={"yo": yo_server},
        allowed_tools=yo_mcp_allowed_tools() + [
            "Read", "Write", "Edit", "Bash", "WebFetch", "Grep", "Glob",
        ],
        permission_mode="bypassPermissions",
    )

    # Print connection / status header
    skills = ", ".join(state.capabilities.get("skills") or []) or "—"
    console.print(f"[{GRAY}]signed in as[/] [bold {MAGENTA}]{state.user.get('email', '?')}[/]  [{GRAY}]· stack: {skills}[/]")
    console.print(f"[{GRAY}]type [bold]/help[/{GRAY}][{GRAY}] for commands · [bold]/quit[/{GRAY}][{GRAY}] to exit[/]")
    console.print()

    try:
        async with ClaudeSDKClient(options=options) as orchestrator:
            loop = asyncio.get_event_loop()
            while not state.shutdown_event.is_set():
                try:
                    line = await loop.run_in_executor(None, _read_input, state)
                except (EOFError, KeyboardInterrupt):
                    console.print("\n[dim]bye[/]")
                    break
                if not line.strip():
                    continue
                if line.strip().lower() in ("exit", "quit"):
                    line = "/quit"

                if line.startswith("/"):
                    if not await _handle_slash(line, state, console):
                        break
                    continue

                # Send to orchestrator
                await orchestrator.query(line)
                await _stream_response(orchestrator, console)
    finally:
        state.shutdown_event.set()
        try:
            await asyncio.wait_for(listener_task, timeout=2)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    return 0


def _read_input(state: ReplState) -> str:
    """Render the prompt with status line + read one line."""
    status = []
    if state.connected:
        status.append("\033[32m●\033[0m")
    else:
        status.append("\033[33m●\033[0m")
    if state.incoming_count or state.incoming_inflight:
        status.append(f"\033[2mserved {state.incoming_count}{f' · {state.incoming_inflight} in-flight' if state.incoming_inflight else ''}\033[0m")
    suffix = "  ".join(status)
    return input(f"{suffix}\n\033[1;36myo>\033[0m ")


# ── Slash command dispatcher ──────────────────────────────────────────────

async def _handle_slash(line: str, state: ReplState, console: Console) -> bool:
    """Returns False if the REPL should exit, True otherwise."""
    parts = shlex.split(line.strip())
    cmd = parts[0].lstrip("/").lower()
    args = parts[1:]

    if cmd in ("quit", "q", "exit"):
        console.print("[dim]bye[/]")
        return False

    if cmd == "help":
        _print_help(console)
        return True

    if cmd == "clear":
        console.clear()
        return True

    if cmd == "me":
        if args and args[0] == "edit":
            new_caps = await _capability_wizard(console)
            state.capabilities = new_caps
        else:
            await _show_me(state, console)
        return True

    if cmd == "online":
        await _slash_online(console)
        return True

    if cmd == "host":
        if not args:
            console.print(f"[yellow]usage:[/] /host \"<goal>\" [public|unlisted]")
            return True
        goal = args[0]
        visibility = args[1] if len(args) > 1 and args[1] in ("public", "unlisted", "invite") else "public"
        await _slash_host(state, console, goal, visibility)
        return True

    if cmd == "find":
        q = " ".join(args) if args else None
        await _slash_find(console, q, state)
        return True

    if cmd in ("join", "leave"):
        if not args:
            console.print(f"[yellow]usage:[/] /{cmd} <cypher_id>")
            return True
        await _slash_join_leave(state, console, cmd, args[0])
        return True

    if cmd == "cyphers":
        await _slash_cyphers(console)
        return True

    if cmd == "wrap":
        if not args:
            console.print(f"[yellow]usage:[/] /wrap <cypher_id>")
            return True
        await _slash_wrap(console, args[0])
        return True

    console.print(f"[yellow]unknown command:[/] {line}  ([bold]/help[/])")
    return True


def _print_help(console: Console) -> None:
    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_column(style=f"bold {GREEN}", width=20)
    t.add_column(style=f"{GRAY}")
    rows = [
        ("/help", "this list"),
        ("/quit, /q", "leave the REPL"),
        ("/clear", "clear screen"),
        ("", ""),
        ("/me", "your profile"),
        ("/me edit", "re-run capability wizard"),
        ("/online", "collaborators online right now"),
        ("", ""),
        ("/host \"<goal>\"", "create a cypher"),
        ("/find [query]", "discover public cyphers"),
        ("/join <id>", "offer your AI to a cypher"),
        ("/leave <id>", "stop offering"),
        ("/cyphers", "cyphers you're in / hosting"),
        ("/wrap <id>", "wrap a cypher you host"),
    ]
    for left, right in rows:
        t.add_row(left, right)
    console.print()
    console.print(t)
    console.print()


async def _show_me(state: ReplState, console: Console) -> None:
    user = state.user
    caps = state.capabilities
    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_column(style=f"{GRAY}", width=14)
    t.add_column(style=f"bold {WHITE}")
    t.add_row("email", user.get("email", "?"))
    t.add_row("tier", user.get("tier", "free"))
    t.add_row("stack", ", ".join(caps.get("skills") or []) or "—")
    if caps.get("blurb"):
        t.add_row("blurb", caps["blurb"])
    t.add_row("model", caps.get("model") or "claude-sonnet-4-5")
    console.print()
    console.print(t)
    console.print()


async def _slash_online(console: Console) -> None:
    try:
        r = await get("/api/spawn/workers")
    except ApiError as e:
        console.print(f"[red]✗[/] {e}")
        return
    workers = r.get("workers", [])
    if not workers:
        console.print(f"[{GRAY}]no collaborators online right now — invite a friend to run [bold]yo[/{GRAY}]")
        return
    t = Table(show_header=True, header_style=f"bold {GREEN}", box=None, padding=(0, 2))
    t.add_column("name")
    t.add_column("id", style=f"{GRAY}")
    t.add_column("inflight")
    t.add_column("capabilities", style=f"{GRAY}")
    for w in workers:
        t.add_row(
            w.get("name", "?"),
            (w.get("worker_id") or "?")[:8],
            f"{w.get('inflight', 0)}/{w.get('max_concurrent', 1)}",
            ", ".join(w.get("capabilities") or []) or "any",
        )
    console.print()
    console.print(t)
    console.print()


async def _slash_host(state: ReplState, console: Console, goal: str, visibility: str) -> None:
    title = goal[:80]
    body: dict[str, Any] = {"title": title, "type": "open", "goal": goal, "visibility": visibility}
    if state.capabilities.get("skills"):
        body["requirements_skills"] = state.capabilities["skills"]
    try:
        r = await post("/api/cyphers", json=body)
    except ApiError as e:
        console.print(f"[red]✗ couldn't create cypher[/] — {e}")
        return
    c = r.get("cypher") if isinstance(r, dict) else None
    if not c:
        console.print(f"[red]✗ unexpected response[/]")
        return
    state.current_cypher_id = c.get("id")
    console.print()
    console.print(f"[green]✓[/] cypher [bold {GREEN}]{c.get('id', '')[:8]}[/] created — [{GRAY}]{visibility}[/]")
    console.print(f"  [{GRAY}]share:[/] yo://cypher/{c.get('id', '')[:8]}")
    console.print()


async def _slash_find(console: Console, q: str | None, state: ReplState) -> None:
    params = []
    if q:
        params.append(f"q={q}")
    if state.capabilities.get("skills"):
        params.append(f"capabilities={','.join(state.capabilities['skills'])}")
    qs = "?" + "&".join(params) if params else ""
    try:
        r = await get(f"/api/cyphers/discover{qs}")
    except ApiError as e:
        console.print(f"[red]✗[/] {e}")
        return
    items = r.get("cyphers", []) if isinstance(r, dict) else []
    if not items:
        console.print(f"[{GRAY}]no public cyphers match — start one with [bold]/host[/{GRAY}]")
        return
    console.print()
    for c in items[:15]:
        seeks = ", ".join(c.get("requirements_skills") or []) or "any"
        jc = c.get("jammer_count", 0)
        console.print(f"  [bold {GREEN}]{c.get('title', '(untitled)')}[/]  [{GRAY}]({jc} jammers · seeks {seeks})[/]")
        console.print(f"    [{GRAY}]/join {(c.get('id') or '')[:8]}  ·  status: {c.get('status', '?')}[/]")
    console.print()


async def _slash_join_leave(state: ReplState, console: Console, action: str, cid: str) -> None:
    path = f"/api/cyphers/{cid}/{action}"
    try:
        r = await post(path)
        if action == "join":
            state.current_cypher_id = cid
            console.print(f"[green]✓[/] joined cypher [bold]{cid[:8]}[/] — your AI is now available there")
        else:
            console.print(f"[green]✓[/] left cypher [bold]{cid[:8]}[/]")
            if state.current_cypher_id == cid:
                state.current_cypher_id = None
    except ApiError as e:
        console.print(f"[red]✗[/] {e}")


async def _slash_cyphers(console: Console) -> None:
    try:
        r = await get("/api/cyphers/mine")
    except ApiError as e:
        console.print(f"[red]✗[/] {e}")
        return
    items = r.get("cyphers", []) if isinstance(r, dict) else []
    if not items:
        console.print(f"[{GRAY}]you're not in any cyphers — try [bold]/find[/{GRAY}][{GRAY}] or [bold]/host[/{GRAY}][{GRAY}][/]")
        return
    t = Table(show_header=True, header_style=f"bold {GREEN}", box=None, padding=(0, 2))
    t.add_column("title")
    t.add_column("id", style=f"{GRAY}")
    t.add_column("status")
    t.add_column("role")
    for c in items:
        t.add_row(
            c.get("title", "?"),
            (c.get("id") or "?")[:8],
            c.get("status", "?"),
            c.get("role", "jammer"),
        )
    console.print()
    console.print(t)
    console.print()


async def _slash_wrap(console: Console, cid: str) -> None:
    try:
        await post(f"/api/cyphers/{cid}/wrap")
        console.print(f"[green]✓[/] cypher [bold]{cid[:8]}[/] wrapped")
    except ApiError as e:
        console.print(f"[red]✗[/] {e}")


# ── Streaming render ──────────────────────────────────────────────────────

async def _stream_response(client: Any, console: Console) -> None:
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        SystemMessage,
        TextBlock,
        ThinkingBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )

    text_buf: list[str] = []
    # Braille-spinner status line. Stays on while we wait for the first
    # text/tool block; stops the moment the orchestrator says anything.
    status = console.status(f"[{GREEN}].Yo[/] [{GRAY}]thinking…[/]", spinner="dots", spinner_style=GREEN)
    status.start()
    spinner_active = True

    def _stop_spinner() -> None:
        nonlocal spinner_active
        if spinner_active:
            try:
                status.stop()
            except Exception:
                pass
            spinner_active = False

    def _flush() -> None:
        if text_buf:
            _stop_spinner()
            console.print(Markdown("".join(text_buf).strip()))
            text_buf.clear()

    try:
        async for msg in client.receive_response():
            if isinstance(msg, SystemMessage):
                continue
            if isinstance(msg, AssistantMessage):
                for block in (msg.content or []):
                    if isinstance(block, TextBlock):
                        text_buf.append(block.text)
                    elif isinstance(block, ThinkingBlock):
                        pass
                    elif isinstance(block, ToolUseBlock):
                        _flush()
                        _stop_spinner()
                        name = getattr(block, "name", "tool")
                        inp = getattr(block, "input", {})
                        label = f"🛠  [{GREEN}]{name}[/]"
                        if isinstance(inp, dict):
                            if name.endswith("__spawn") and "prompt" in inp:
                                p = str(inp["prompt"])[:60]
                                label += f"  [{GRAY}]→ {p}{'…' if len(str(inp['prompt'])) > 60 else ''}[/]"
                            elif name.endswith("__spawn_parallel") and "prompts" in inp:
                                try:
                                    import json as _j
                                    ps = _j.loads(inp["prompts"]) if isinstance(inp["prompts"], str) else inp["prompts"]
                                    label += f"  [{GRAY}]→ ×{len(ps)}[/]"
                                except Exception:
                                    pass
                        console.print(f"  {label}")
                    elif isinstance(block, ToolResultBlock):
                        _flush()
            elif isinstance(msg, UserMessage):
                pass
            elif isinstance(msg, ResultMessage):
                _flush()
                _stop_spinner()
                in_t = 0
                out_t = 0
                try:
                    if isinstance(msg.usage, dict):
                        in_t = int(msg.usage.get("input_tokens", 0) or 0)
                        out_t = int(msg.usage.get("output_tokens", 0) or 0)
                    else:
                        in_t = int(getattr(msg.usage, "input_tokens", 0) or 0)
                        out_t = int(getattr(msg.usage, "output_tokens", 0) or 0)
                except Exception:
                    pass
                console.print(f"  [{GRAY_FAINT}]· {in_t}+{out_t} tok[/]")
                return
        _flush()
    finally:
        _stop_spinner()
