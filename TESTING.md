# Testing yo-term

Two paths:

1. **Single-machine PoC** — host + worker on the same box, hits localhost yo-server
2. **Cross-machine** — same yo-server in the middle, different machines for host and worker

This doc covers (1). Cross-machine adds nothing new conceptually — point both
machines at a public yo-server URL and follow the same loop.

## Prerequisites

- yo-server running at `http://localhost:3001` (`docker compose up` in `yo-server/`)
- Postgres reachable (docker compose handles it)
- macOS Keychain has Claude Code credentials (run `claude /login` once if not)
- A user account on yo-server with a JWT (the bootstrap helper below creates one)

## Bootstrap a test user (one-time)

Standard signup needs an OTP via email. For local dev we mark the pending
registration verified directly in Postgres, then complete the profile.

```bash
# 1. Initiate registration (server sends an OTP — we won't read it)
curl -s -X POST http://localhost:3001/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"yoterm-test@example.com","password":"yoterm-test-1234"}'

# 2. Mark verified directly
docker exec yo-server-postgres-1 psql -U yo -d yoserver \
  -c "UPDATE pending_registrations SET verified_at=NOW() WHERE email='yoterm-test@example.com';"

# 3. Complete profile — returns the access JWT and refresh token
curl -s -X POST http://localhost:3001/api/auth/complete-profile \
  -H 'Content-Type: application/json' \
  -d '{"email":"yoterm-test@example.com","firstName":"Yo","lastName":"Term","password":"yoterm-test-1234","confirmPassword":"yoterm-test-1234","orgName":"Yo Term Test"}'

# Subsequent logins (after access token expires) — login endpoint returns both tokens:
curl -s -X POST http://localhost:3001/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"yoterm-test@example.com","password":"yoterm-test-1234"}'
```

Save the `accessToken` and `refreshToken` from the response.

## Install yo-term

```bash
cd yo-term
uv sync --all-extras
```

## Test loop A — `send` (fire-and-await PoC)

**Terminal 1 — login + worker:**

```bash
cd yo-term
uv run yo doctor                                          # all green?
uv run yo login --token "<access>" --refresh-token "<refresh>"
uv run yo worker start --max-concurrent 2 --capabilities code,research
```

You'll see the .Yo banner, then a live status dashboard:

```
worker [your-machine.local]  (uuid…)
server: http://localhost:3001
concurrency: 2  ·  capabilities: code, research
log: /Users/.../yo-term/logs/worker.log
Ctrl-C to stop

  status            connected  up 3s
  worker            your-machine.local  (a1b2c3d4)
  server            http://localhost:3001
  capabilities      code, research
  concurrency       0/2
  jobs              0 handled  (0 failed, 0 filtered)
  tokens (in/out)   0 / 0
  earnings          0 yos earned today
```

**Terminal 2 — send a prompt:**

```bash
cd yo-term
uv run yo login --token "<same access>"
uv run yo send "say hi in 4 words"
```

Expected output:

```
→ say hi in 4 words

╭───── response ─────╮
│ Hi there, friend today! │
╰────────────────────╯
  worker a1b2c3d4  ·  model claude-sonnet-4-5  ·  tokens 6+13  ·  wall 4.00s
  yos 14 (worker +12 · platform +2)  not settled (same-org self-spawn)
```

Single-machine self-spawn won't settle (host org == worker org), but the
routing flow + cost computation are exercised.

## Test loop B — `cypher new` (orchestrator REPL)

```bash
uv run yo cypher new "research: list 3 facts about deep-sea bioluminescence"
```

You're dropped into the orchestrator REPL. The orchestrator's Claude Code
sees the yo MCP tools and may fan out:

```
yo> research: list 3 facts about deep-sea bioluminescence

🛠  mcp__yo__spawn_parallel  → ×3
... [results stream in] ...

[final markdown answer rendered]
  turn complete  ·  142+512 tok  ·  $0.003
yo> /exit
```

## Test loop C — `watch` (network TUI)

```bash
uv run yo watch
```

Live Textual UI showing:
- Header with clock + ".Yo · cypher network · powered by glyphh ai"
- Connection status + yos balance
- Workers table (name, id, inflight/cap, capabilities, uptime)

Hotkeys: `q` quit, `r` refresh now.

## Test loop D — worker filters

```bash
uv run yo worker start \
  --allow '^research:' \
  --deny 'phishing|malware|private[_ ]key'
```

Sends matching `^research:` prompts get processed; matching deny prompts get
rejected with `worker filter: denied by pattern: phishing|malware|...`

## Test loop E — production binary (requires `make binary` first)

```bash
make binary       # ~30s, builds dist/yo-term
./dist/yo doctor
./dist/yo --help
```

The single-file binary should run with no Python required.

## What this proves

✅ Worker registers via SSE, stays connected with reconnect backoff
✅ Prompt filters (allow/deny regex) work
✅ Concurrency cap respected
✅ Daily yos cap respected
✅ Capability registration attempted (currently warns 400 — see open items)
✅ Host POSTs `/api/spawn`, server picks worker, pushes via SSE
✅ Worker fires Claude Agent SDK with macOS Keychain CC creds (no API key)
✅ Worker posts completion with token counts
✅ Server computes yos via model-rate-service, settles atomically when buyer ≠ provider org
✅ Auto-refresh on 401 keeps long-running sessions alive
✅ yo MCP loaded into orchestrator CC; tool calls fan out work
✅ `yo watch` shows live network state
✅ `uv build` produces a valid wheel; PyInstaller produces a single binary

## Cross-machine variant (next milestone)

Same setup, two machines, real yo-server (not localhost). Machine A runs
`worker start`, machine B runs `cypher new` (or `send`). SSE stays connected
over the open internet, settlement actually fires (different orgs), yos
moves between accounts.

## Smoke test extras

```bash
# List your registered workers (debug helper)
JWT=$(python3 -c "import json; print(json.load(open('$HOME/.dotyo/config.json'))['access_token'])")
curl -s -H "Authorization: Bearer $JWT" http://localhost:3001/api/spawn/workers | python3 -m json.tool

# Wallet
uv run yo wallet

# Run with custom model
uv run yo send "say hi" --model claude-haiku-4-5

# Direct hit on the spawn endpoint (skip the CLI)
curl -s -X POST http://localhost:3001/api/spawn \
  -H "Authorization: Bearer $JWT" \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"say hi"}' | python3 -m json.tool
```

## Known limitations (intentional for v0)

- Single worker per machine concurrency capped at 1 by default; raise with `--max-concurrent`
- `yo cypher tail` not yet implemented
- Capability-register endpoint returns 400 (`client_id required`) — non-fatal,
  logged as warning; routing works without it. Server-side payload alignment is on the docket.
- Browser OAuth flow not yet built; pasting JWT is the v0 login UX
- Same-machine self-spawn won't settle (skipped — buyer org == provider org)
- JWT is short-lived (15 min). With a refresh token saved (use `--refresh-token` on login),
  the API client auto-rotates on 401.
