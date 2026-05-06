"""yo-term in-process MCP + spawn handler.

The yo MCP exposes the cypher network as tools to the orchestrator's
Claude Agent SDK. Same process also runs a background SSE listener that
receives spawn_requests from other users and serves them with a sandboxed,
read-only-by-default SDK invocation.

This is the network plumbing for the REPL — kept here (not under commands/)
because the REPL imports it as a module, not a Typer subcommand.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import httpx
from httpx_sse import aconnect_sse

from .lib.api import ApiError, _try_refresh, get, post
from .lib.config import load_config


# ── Tool surface for the orchestrator (yo MCP) ────────────────────────────
def yo_mcp_config():
    """In-process MCP server with the network's tools, for the orchestrator SDK."""
    from claude_agent_sdk import create_sdk_mcp_server, tool

    @tool(
        name="spawn",
        description=(
            "Dispatch one prompt to a collaborator on the .Yo network. "
            "Returns their response. Use for specialist subtasks or fan-out."
        ),
        input_schema={
            "prompt": str,
            "model": str,
            "capabilities": str,
            "cypher_id": str,
            "timeout_ms": int,
        },
    )
    async def yo_spawn(args: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {"prompt": args["prompt"]}
        for k in ("model", "cypher_id"):
            if args.get(k):
                payload[k] = args[k]
        if args.get("timeout_ms"):
            payload["timeout_ms"] = int(args["timeout_ms"])
        if args.get("capabilities"):
            payload["capabilities"] = [s.strip() for s in str(args["capabilities"]).split(",") if s.strip()]
        try:
            res = await post("/api/spawn", json=payload)
        except ApiError as e:
            return {"content": [{"type": "text", "text": f"yo.spawn failed: [{e.status}] {e.message}"}], "isError": True}
        text = res.get("result", "") if isinstance(res, dict) else str(res)
        in_t = res.get("input_tokens", 0) if isinstance(res, dict) else 0
        out_t = res.get("output_tokens", 0) if isinstance(res, dict) else 0
        worker = res.get("worker_id", "?") if isinstance(res, dict) else "?"
        return {"content": [{"type": "text", "text": f"{text}\n\n---\n[worker={worker[:8]} tokens={in_t}+{out_t}]"}]}

    @tool(
        name="spawn_parallel",
        description=(
            "Fan out N prompts concurrently across the network. `prompts` MUST be "
            "a JSON array of strings. Returns each result tagged. Use for "
            "independent subtasks."
        ),
        input_schema={
            "prompts": str,  # JSON array
            "model": str,
            "capabilities": str,
            "timeout_ms": int,
        },
    )
    async def yo_spawn_parallel(args: dict[str, Any]) -> dict[str, Any]:
        try:
            prompts = json.loads(args.get("prompts", "[]")) if isinstance(args.get("prompts"), str) else list(args.get("prompts") or [])
        except json.JSONDecodeError:
            return {"content": [{"type": "text", "text": "yo.spawn_parallel: 'prompts' must be a JSON array of strings"}], "isError": True}
        if not isinstance(prompts, list) or not all(isinstance(p, str) for p in prompts):
            return {"content": [{"type": "text", "text": "yo.spawn_parallel: 'prompts' must be a JSON array of strings"}], "isError": True}

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

        results = await asyncio.gather(*[one(p) for p in prompts])
        joined = "\n\n---\n\n".join(f"## Result {i+1}\n{r}" for i, r in enumerate(results))
        return {"content": [{"type": "text", "text": joined}]}

    @tool(
        name="workers_online",
        description="List collaborators currently online and their advertised capabilities.",
        input_schema={"capabilities": str},
    )
    async def yo_workers_online(args: dict[str, Any]) -> dict[str, Any]:
        try:
            r = await get("/api/spawn/workers")
        except ApiError as e:
            return {"content": [{"type": "text", "text": f"yo.workers_online failed: [{e.status}] {e.message}"}], "isError": True}
        workers = r.get("workers", []) if isinstance(r, dict) else []
        if not workers:
            return {"content": [{"type": "text", "text": "no collaborators online right now"}]}
        lines = [f"{len(workers)} online"]
        for w in workers:
            caps = ", ".join(w.get("capabilities") or []) or "any"
            lines.append(f"  • {w.get('name', '?')} ({(w.get('worker_id', '?') or '?')[:8]}) — inflight {w.get('inflight', 0)}/{w.get('max_concurrent', 1)} — {caps}")
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    return create_sdk_mcp_server(
        name="yo",
        version="0.2.0",
        tools=[yo_spawn, yo_spawn_parallel, yo_workers_online],
    )


def yo_mcp_allowed_tools() -> list[str]:
    return [
        "mcp__yo__spawn",
        "mcp__yo__spawn_parallel",
        "mcp__yo__workers_online",
    ]


# ── Background worker — receives spawns from the network ─────────────────
SAFE_TOOLS = ["Read", "Grep", "Glob", "WebFetch"]
WORKER_SYSTEM_PROMPT = """\
You are answering a request from another user on the .Yo collaboration
network. They asked their Claude to dispatch this task to you because
your declared capabilities matched. Do the work and return a concise
final answer.

Tools available: Read, Grep, Glob, WebFetch (read-only). You do NOT
have Bash, Write, or Edit. Decline gracefully if the task strictly
needs them.

Be concise — the orchestrator pays input tokens for whatever you return.
"""


def _usage_int(usage: Any, key: str) -> int:
    if usage is None:
        return 0
    if isinstance(usage, dict):
        return int(usage.get(key, 0) or 0)
    return int(getattr(usage, key, 0) or 0)


async def _serve_one(prompt: str, model: str | None) -> dict[str, Any]:
    """Fire CC for one incoming spawn request. Returns completion payload."""
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        ResultMessage,
        TextBlock,
    )

    output_chunks: list[str] = []
    final_result: str | None = None
    input_tokens = 0
    output_tokens = 0
    used_model = model or "claude-sonnet-4-5"

    cwd = str(Path.home() / ".dotyo" / "work" / str(uuid.uuid4()))
    Path(cwd).mkdir(parents=True, exist_ok=True)

    options = ClaudeAgentOptions(
        model=model,
        system_prompt=WORKER_SYSTEM_PROMPT,
        allowed_tools=SAFE_TOOLS,
        permission_mode="default",
        cwd=cwd,
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in (message.content or []):
                    if isinstance(block, TextBlock):
                        output_chunks.append(block.text)
            elif isinstance(message, ResultMessage):
                input_tokens = _usage_int(message.usage, "input_tokens")
                output_tokens = _usage_int(message.usage, "output_tokens")
                if message.result:
                    final_result = message.result

    return {
        "result": final_result if final_result is not None else "".join(output_chunks),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "model": used_model,
    }


async def _worker_listener(
    on_event: Callable[[dict[str, Any]], None],
    capabilities: list[str],
    logger: logging.Logger,
    shutdown: asyncio.Event,
) -> None:
    """Connect to /api/worker/stream, receive spawn_requests, fire SDK, post completions.

    Calls on_event for state updates the REPL can render in its prompt status line.
    """
    backoff = 1.0
    worker_id = str(uuid.uuid4())
    name = socket.gethostname()

    while not shutdown.is_set():
        cfg = load_config()
        if not cfg.access_token:
            await asyncio.sleep(2)
            continue

        headers = {"Authorization": f"Bearer {cfg.access_token}"}
        try:
            async with httpx.AsyncClient(
                base_url=cfg.server_url.rstrip("/"),
                headers=headers,
                timeout=httpx.Timeout(30.0, read=None),
            ) as client:
                # Validate token cheaply; refresh on 401
                me = await client.get("/api/auth/me")
                if me.status_code == 401:
                    if not await _try_refresh():
                        on_event({"kind": "auth_lost"})
                        return
                    continue

                sse_url = (
                    f"/api/worker/stream"
                    f"?worker_id={worker_id}"
                    f"&name={quote(name)}"
                    f"&max_concurrent=2"
                )
                if capabilities:
                    sse_url += "&capabilities=" + quote(",".join(capabilities))

                async with aconnect_sse(client, "GET", sse_url, timeout=httpx.Timeout(30.0, read=None)) as event_source:
                    on_event({"kind": "connected"})
                    backoff = 1.0
                    async for sse in event_source.aiter_sse():
                        if shutdown.is_set():
                            break
                        if sse.event != "spawn_request":
                            continue
                        try:
                            data = json.loads(sse.data)
                        except json.JSONDecodeError:
                            continue
                        request_id = data.get("request_id")
                        prompt = data.get("prompt", "")
                        model = data.get("model")
                        on_event({"kind": "incoming_started", "request_id": request_id, "prompt": prompt[:40]})
                        # Fire-and-forget — handled in a task so we keep reading SSE
                        asyncio.create_task(_handle_incoming(client, request_id, prompt, model, on_event, logger))
        except Exception as e:
            on_event({"kind": "disconnected", "error": type(e).__name__})
            logger.warning("worker listener disconnected: %s", e)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.6, 30.0)


async def _handle_incoming(
    client: httpx.AsyncClient,
    request_id: str,
    prompt: str,
    model: str | None,
    on_event: Callable[[dict[str, Any]], None],
    logger: logging.Logger,
) -> None:
    started = time.time()
    try:
        result = await _serve_one(prompt, model)
        elapsed = time.time() - started
        await client.post(f"/api/spawn/{request_id}/complete", json={**result, "wall_seconds": elapsed})
        on_event({
            "kind": "incoming_done",
            "request_id": request_id,
            "input_tokens": result["input_tokens"],
            "output_tokens": result["output_tokens"],
            "elapsed": elapsed,
        })
        logger.info("served request_id=%s in=%d out=%d wall=%.1fs", request_id, result["input_tokens"], result["output_tokens"], elapsed)
    except Exception as e:
        on_event({"kind": "incoming_failed", "request_id": request_id, "error": str(e)[:60]})
        logger.exception("incoming spawn failed: %s", e)
        try:
            await client.post(f"/api/spawn/{request_id}/complete", json={
                "result": "",
                "error": str(e)[:200],
                "input_tokens": 0,
                "output_tokens": 0,
                "model": model or "unknown",
            })
        except Exception:
            pass
