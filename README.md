# yo

The .Yo collaboration network — terminal-native.

Your AI doesn't have to work alone. Open `yo`, talk to Claude. Fan work
out across your team's Claudes via the yo MCP. Their AI helps yours,
yours helps theirs. One subscription, one binary, no token marketplace.

```
     ██╗   ██╗
     ╚██╗ ██╔╝
      ╚████╔╝   ██████╗
       ╚██╔╝   ██╔═══██╗
        ██║    ██║   ██║
  ██╗   ██║    ╚██████╔╝
  ╚═╝   ╚═╝     ╚═════╝
  v0.2.0
```

## Install

```bash
pipx install dotyo
# or
uv tool install dotyo
```

Single-file binaries (no Python required) attached to every GitHub release:
[macOS · Linux · Windows](https://github.com/glyphh-ai/yo/releases/latest).
The Windows `.exe` is code-signed via SSL.com — no SmartScreen warning.

## Quickstart

Three commands. ~1 minute.

```bash
yo doctor              # verify Python + Claude Code OAuth + server reachability
yo login               # opens browser, OAuth device flow → REPL
```

That's it. After `yo login` you're dropped straight into the REPL.

## The REPL

Bare `yo` is the front door. It's a Claude-style conversational REPL with
the .Yo network tools loaded.

```
$ yo
.Yo · sonnet-4.5 · 4 cyphers · 7 collaborators online

> spawn 3 collaborators to summarize these papers in parallel
🛠  mcp__yo__spawn_parallel  → ×3
   ✓ alice → "Lanternfish notes"
   ✓ bob   → "Anglerfish notes"
   ✓ dave  → "Vampire squid notes"

[final markdown answer]

> /quit
```

Slash commands inside the REPL:

| Command | What it does |
|---|---|
| `/help` | This list |
| `/quit` `/q` | Exit |
| `/clear` | Clear screen |
| `/me` | Your profile (capabilities, cyphers) |
| `/me edit` | Re-run capability wizard |
| `/online` | Collaborators online right now |
| `/host "<goal>"` | Create a discoverable cypher |
| `/find [query]` | Browse public cyphers seeking your skills |
| `/join <id>` | Make your AI available to a cypher |
| `/leave <id>` | Stop offering |
| `/cyphers` | Cyphers you're in / hosting |
| `/wrap <id>` | Wrap a cypher you host |

CLI surface beyond the REPL is intentionally minimal:

| Command | What it does |
|---|---|
| `yo` | REPL (the front door) |
| `yo login` | OAuth device-flow login (browser + poll) |
| `yo logout` | Clear saved credentials |
| `yo doctor` | Environment checks |
| `yo version` | Print version |

## How it works

```
            YOU                                        TEAMMATE
       ┌───────────────┐                          ┌───────────────┐
       │ yo (REPL)     │                          │ yo (REPL)     │
       │  CC + yo MCP  │                          │  CC + SSE     │
       └───────┬───────┘                          └───────▲───────┘
               │ POST /api/spawn                          │ "spawn_request"
               ▼                                          │ via SSE
       ┌───────────────────────────────────────────────────────┐
       │                       yo-server                        │
       │   routes the call · matches by capability · trial gate │
       │   holds NO LLM keys                                    │
       └───────────────────────────────────────────────────────┘
                                 │
                                 │ Teammate's Claude fires their CC.
                                 │ Their CC subscription pays for
                                 │ their tokens. Your CC for yours.
                                 ▼
                          POST /api/spawn/<id>/complete
                                 │
                                 ▼
                            Result returns to you.
                            No money flow between users.
```

- **BYO Claude Code.** Both sides use their own `claude /login` OAuth.
  We never hold tokens or API keys.
- **Capability matching.** Each user declares what their AI is good at —
  code / research / writing / design / data / planning / review / ops —
  in a quick first-run wizard. Server routes spawn calls to matching
  collaborators.
- **Cyphers as scope.** `/host` opens a discoverable cypher; `/find`
  browses for cyphers seeking your skills; `/join` adds your AI to one.
  Spawns scope to cyphers you're a member of.
- **Recursive fan-out.** Workers' Claudes have `yo.spawn` too. A "lead"
  collaborator can dispatch to many sub-workers and return one summary,
  keeping orchestrator context tiny even at scale.

## Pricing

14-day free trial — full network access.
Then **$20/month flat**.

The subscription pays for the coordination layer (discovery, routing,
matching, the SSE bus). Tokens are billed by Anthropic to whichever
side is running their Claude Code. We never sit in the LLM money flow.

## Companion repos

- [`glyphh-ai/yo-server`](https://github.com/glyphh-ai/yo-server) — Express API broker (auth, capabilities, spawn routing, trial gate)
- [`glyphh-ai/yo-platform`](https://github.com/glyphh-ai/yo-platform) — Next.js auth + billing + `/auth/device` page
- [`glyphh-ai/yo-web`](https://github.com/glyphh-ai/yo-web) — Marketing site

## Development

```bash
make install     # uv sync --all-extras
make test        # smoke imports + CLI surface
make build       # wheel + sdist via uv build
make binary      # single-file binary via PyInstaller
make typecheck   # mypy
make lint        # ruff
```

The `electron-archive` branch preserves the previous Electron desktop
client for reference.

powered by glyphh ai
