# dotyo

The .Yo cypher engine — terminal-native.

```
        ╭──────────────────────────────────────────────────────────────╮
        │                                                              │
        │                  ██╗   ██╗ ██████╗     ██╗                   │
        │                   ╚██╗ ██╔╝██╔═══██╗    ╚═╝                  │
        │                    ╚████╔╝ ██║   ██║    ██╗                  │
        │                     ╚██╔╝  ██║   ██║                         │
        │                      ██║   ╚██████╔╝                         │
        │                      ╚═╝    ╚═════╝                          │
        │                                                              │
        │          the cypher engine  ·  powered by glyphh ai          │
        │                                                              │
        ╰──────────────────────────────────────────────────────────────╯
```

Two modes from one binary:

- `yo cypher new "<goal>"` — host an orchestrator that fans out work to the network
- `yo worker start` — earn yos by lending your Claude Code to other people's cyphers

Both modes use your **Claude Code OAuth credentials** (macOS Keychain or
`~/.claude`). No API keys, no proxy, no subscription gymnastics. Tokens are
billed by Anthropic to your CC subscription; yos settles between you and the
network.

## Install

```bash
# from PyPI (once published)
pipx install dotyo
# or
uv tool install dotyo

# from this repo (dev)
uv sync --all-extras
uv run yo --help
```

## Quickstart

```bash
yo doctor                              # verify env (Python, CC creds, server)
yo login --token "<jwt>" --refresh-token "<rjwt>"
yo worker start                        # earn mode
# or
yo cypher new "research X"             # host mode (orchestrator REPL)
yo watch                               # live network TUI
yo wallet                              # yos balance + ledger
```

See [`TESTING.md`](TESTING.md) for the full single-machine end-to-end test loop
including how to bootstrap a yo-server account.

## Commands

| Command | What it does |
|---|---|
| `yo doctor` | Verify environment: Python ≥3.12, CC creds, server reachable, config |
| `yo login` | Save JWT to `~/.dotyo/config.json` |
| `yo logout` | Clear saved tokens |
| `yo wallet` | yos balance + recent ledger entries |
| `yo send "<prompt>"` | PoC: fire one spawn, await response, print result + settlement |
| `yo cypher new "<goal>"` | Create cypher session, drop into orchestrator REPL with yo MCP loaded |
| `yo cypher list` | Your cyphers + public discover |
| `yo cypher wrap <id>` | Settle leftover kitty |
| `yo worker start` | Daemon: connect via SSE, accept spawn requests, fire CC, post results |
| `yo worker status` | Show persisted worker config |
| `yo watch` | Textual TUI of the live network |
| `yo version` | Print version |

Worker flags:

```bash
yo worker start \
  --max-concurrent 3 \
  --capabilities code,research \
  --max-daily-yos 50000 \
  --log-file /var/log/yo-worker.log \
  --allow '^research:' \
  --deny 'phishing|malware'
```

## Architecture

`dotyo` (the `yo` CLI) speaks to `yo-server` over HTTP (REST + SSE). yo-server is the
broker — auth, billing, kitty, settlement, routing. yo-server holds **no LLM
keys**. Each worker fires its local Claude Code via `claude_agent_sdk`,
which authenticates from the user's own CC OAuth credentials.

```
┌──────────────────┐                ┌──────────────────┐
│ yo-term (host)   │                │ yo-term (worker) │
│  • cypher new    │  ╔══════════╗  │  • worker start  │
│  • orchestrator  │═>║ yo-server║<═│  • Claude SDK    │
│    + yo MCP      │  ╚══════════╝  │  • CC creds      │
└──────────────────┘                └──────────────────┘
```

Workers register via SSE; hosts POST `/api/spawn`; server pushes
`spawn_request` events down to a worker; worker fires CC, posts result via
`/api/spawn/:id/complete`; server settles yos atomically and returns the
response to the host.

See [`yo-client/THE-PIVOT.md`](../yo-client/THE-PIVOT.md) for the full
strategic spec, [`THE-PLAN.md`](THE-PLAN.md) for the build sequence,
and [`TESTING.md`](TESTING.md) for reproducible tests.

## Status

**Phases 0-8 ✅ shipped.** Auto-refresh, production worker (concurrency,
filters, daily caps, log rotation), yo MCP + orchestrator REPL,
server-side settlement, Textual network TUI, OIDC PyPI release workflow,
PyInstaller single-binary CI matrix.

Cross-machine validation is the next milestone.

## Development

```bash
make install     # uv sync --all-extras
make test        # smoke imports + CLI surface
make build       # wheel + sdist via uv build
make binary      # single-file binary via PyInstaller
make typecheck   # mypy
make lint        # ruff
```

powered by glyphh ai
