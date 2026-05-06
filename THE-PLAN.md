# dotyo — build plan

> Companion to `yo-client/THE-PIVOT.md`. That doc is the spec; this doc
> is the build sequence.

> **Status: Phases 0-8 ✅ shipped end-to-end.** Settlement plumbing,
> orchestrator REPL with yo MCP, production-grade worker, Textual
> network monitor, OIDC PyPI workflow, single-binary CI matrix all in.

---

## Phase 0 — scaffolding ✅

- `pyproject.toml` (uv-managed) + `.gitignore` + `README.md` + `TESTING.md`
- Typer CLI at `src/dotyo/cli.py` — subcommands: `doctor`, `login`, `logout`,
  `wallet`, `send`, `worker {start,status}`, `cypher {new,list,wrap,tail}`,
  `watch`, `version`
- Rich-rendered banner with cyan→magenta gradient `.Yo` logo + glyphh ai branding
- Config helpers at `src/dotyo/lib/config.py` with `~/.dotyo/config.json` +
  env-var override (`YO_SERVER_URL`, `YO_TOKEN`)
- CC credential detector at `src/dotyo/lib/cc_creds.py` — handles macOS
  Keychain (`security find-generic-password -s "Claude Code-credentials"`)
  + cross-platform file paths
- `doctor` command working — checks Python, CC creds, server reachability, config

## Phase 1 — auth (paste-a-JWT) ✅

- `yo login --token <jwt>` — saves token, verifies via `/api/auth/me`,
  records user id + email
- `yo logout` — clears auth from config
- `yo wallet` — `GET /api/yos/balance` + `GET /api/yos/ledger`,
  rich-formatted table

## Phase 1.5 — auto-refresh ✅

- `--refresh-token` flag on `login` to save the refresh token alongside
  the access token
- HTTP client at `src/dotyo/lib/api.py` auto-attempts a refresh on the
  first 401 response, retries the original request, and persists the rotated
  tokens. Falls back to surfacing 401 to the caller if refresh fails or no
  refresh token is saved.

**Deferred to Phase 1.6:** browser OAuth (open localhost callback, capture
code, exchange). Manual JWT paste is fine for the dev alpha; add browser flow
once we have non-developer users.

## Phase 2 — routing PoC ✅ proven

The load-bearing test. Worked first run, ~7s round-trip for short prompts.
See `TESTING.md` for the reproducible loop.

**yo-server side** (`yo-server/src/routes/spawn-routes.ts`):
- `GET  /api/worker/stream` — worker's persistent SSE
- `POST /api/worker/register` — optional registration ping
- `POST /api/spawn` — host call (60s default timeout)
- `POST /api/spawn/:request_id/complete` — worker call
- `GET  /api/spawn/workers` — debug list of caller's workers
- All in-memory state, auth-gated

**yo-term side**:
- `worker start` — SSE listener with reconnect backoff
- `send <prompt>` — fire-and-await POC host

## Phase 3 — production worker ✅

- **Concurrency cap** via `asyncio.Semaphore` (server-aware, query-param-advertised)
- **Capability registration** — best-effort POST to `/api/capabilities/register`
  on startup with worker_id + name + capabilities + max_concurrent
- **Prompt filters** — regex allowlist + denylist (CLI flags persist to config)
- **Daily yos earn cap** — resets at UTC midnight
- **Reconnect with exponential backoff** (1s → 30s)
- **Live Rich dashboard** + log file with rotation
  (`~/.dotyo/logs/worker.log`, 10 MB × 5 rotations)
- **Graceful shutdown** drains in-flight requests for up to 30s
- `worker status` — prints persisted config

CLI:
```bash
yo worker start \
  --max-concurrent 3 \
  --capabilities code,research \
  --max-daily-yos 50000 \
  --log-file /var/log/yo-worker.log \
  --allow '^research:' --allow '^code:' \
  --deny 'phishing|malware'
```

## Phase 4 — yo MCP + cypher REPL ✅

- **yo MCP server** at `src/dotyo/mcp/yo_mcp.py` — built with the SDK's
  `create_sdk_mcp_server` + `@tool` decorator. Four tools:
  - `yo.spawn(prompt, model?, capabilities?, cypher_id?, timeout_ms?)`
  - `yo.spawn_parallel(prompts, model?, capabilities?, timeout_ms?)`
  - `yo.balance()`
  - `yo.workers_online(capabilities?)`
- **`yo cypher new <goal>`** — best-effort cypher session creation
  (`POST /api/cyphers`), then loads `ClaudeSDKClient` with the yo MCP attached
  + `system_prompt` describing the orchestrator role. Drops user into a REPL.
  Tool calls render inline (`🛠 yo.spawn → say red`); markdown responses
  rendered with Rich.
- **`yo cypher list/wrap/tail`** — basic helpers

The orchestrator's CC sees the yo tools alongside its built-in tools and
decides when to fan out. We don't model the work pattern.

## Phase 5 — settlement ✅ (server-side)

`yo-server/src/routes/spawn-routes.ts` settlement logic:
- On successful `spawn_complete`, computes total yos via
  `model-rate-service.computeYos(provider, model, in_tok, out_tok)`
- 15% platform cut (basis points config), worker gets the remainder
- `yos-service.settleSession` handles the atomic transfer (advisory locks
  + transactional debit/credit)
- Same-org / same-user self-tests skip settlement and record a reason
  ("same-org self-spawn") so the routing-only loop still works
- Response includes `yos_total / yos_to_worker / yos_platform_cut / settled / settle_reason`

`yo send` now displays settlement: e.g.
> `yos 2 (worker +2 · platform +0)  not settled (same-org self-spawn)`

## Phase 6 — PyPI publish ✅

- `pyproject.toml` — proper metadata, classifiers, scripts (`yo-term`, `yo`),
  hatchling build backend, `dependency-groups.dev`
- `LICENSE` — Proprietary (placeholder; swap as needed)
- `.github/workflows/ci.yml` — tests on Python 3.12 + 3.13, smoke imports,
  CLI surface, ruff + mypy (continue-on-error for v0)
- `.github/workflows/release.yml` — fires on `v*` tag:
  1. **pre-release-checks** — Python 3.12+3.13 matrix, smoke imports
  2. **publish-pypi** — OIDC Trusted Publisher (no API token in repo). Mirrors
     `glyphh-runtime`'s pattern: `id-token: write` + `pypa/gh-action-pypi-publish@release/v1`.
     Uses the `pypi` GitHub Environment.
  3. **build-binaries** — PyInstaller matrix on macos-latest (arm64), macos-13 (x86_64),
     ubuntu-latest, windows-latest. Bundles `claude_agent_sdk` via `--collect-all`.
  4. **upload-release** — attaches binaries to the GitHub release alongside install instructions
- Token names match the existing pattern (`RUNTIME_RELEASES_TOKEN` for cross-repo
  checkout where needed)
- `Makefile` for common dev tasks (`install`, `test`, `build`, `binary`,
  `publish-test`, `publish`, `clean`)

**Pre-publish setup checklist** (one-time on PyPI):
1. Push the repo to `glyphh-ai/yo` (or wherever)
2. PyPI → Account Settings → Trusted Publishers → Add: owner `glyphh-ai`,
   repo `yo-term`, workflow `release.yml`, environment `pypi`
3. Tag a release: `git tag v0.1.0 && git push origin v0.1.0`
4. The workflow does the rest

## Phase 7 — Textual TUI ✅

`src/dotyo/commands/watch.py` — `yo watch` command opens a
Textual app with:
- Header (clock, title, subtitle)
- Status bar — connection state, yos balance, server URL
- Workers DataTable — name, id, inflight/cap, capabilities, uptime, connected_at
- Auto-refresh every 3s
- Hotkeys: `q` quit, `r` refresh now

Useful for orchestrators wanting to see what's online before fanning out,
and for workers wanting to see their state alongside the rest of the network.

**Deferred:** 3-column live workspace view (per-worker focus pane) — wait
until cyphers actually have many simultaneous workers in real use.

## Phase 8 — single-binary distribution ✅

- `pyinstaller>=6.10` in dev deps
- `scripts/build-binary.sh` — local one-command build for the current platform
- `Makefile` target `binary` runs the script
- `.github/workflows/release.yml` matrix builds for macOS arm64 + x86_64,
  Linux x86_64, Windows x86_64. Uploads to GitHub release assets.

**Deferred:**
- macOS code signing (Developer ID + notarization) — needs a cert
- Windows Authenticode — needs a cert
- Auto-update (compare GH releases version with `__version__`)
- Linux arm64 + macOS universal binary — add when there's demand

---

## What's pending (non-phase-blocking)

- Browser OAuth flow (Phase 1.6)
- Capability-register endpoint payload — server expects `client_id` + capability
  blob; current call returns 400 (non-fatal, logged warning). Align next pass.
- Cross-machine validation (Phase 6 of the spec — needs a second machine + a
  yo-server reachable from both)
- Real worker-host pair where settlement actually fires (different orgs)

---

*Last updated: 2026-05-05. Phases 0-8 ✅ shipped.*
