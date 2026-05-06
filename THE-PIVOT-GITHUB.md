# THE-PIVOT-GITHUB — yo on GitHub Projects

> **Status: planning. Iterating before build.**
>
> Companion to `THE-PLAN.md` (which documents shipped phases 0–8 of the
> peer-to-peer SSE-bus model). This doc describes the *next* architecture:
> cyphers as GitHub Projects, work as PRs, identity as GitHub, perimeter
> as GitHub's perimeter.

---

## The pivot in one paragraph

Today yo coordinates work over a custom SSE worker bus, with a custom
identity (device-flow JWT), a custom credential model (`YO_SPAWN_TOKEN`),
and a fragile trust story (peer-controlled prompts running inside a peer's
Claude Code session). GitHub already solves identity, permissions, sandbox,
audit trail, and attribution — better than we ever will. So we pivot:

- **A cypher is a GitHub Project (v2)**, owned by the host.
- **Work is GitHub items** — issues across linked repos, draft items for
  non-code work — with capability custom fields.
- **Spawn is `assign`** — yo's matchmaker assigns Project items to jammers.
- **Wrap is "PR merged" / item closed.**
- **A jammer is anyone with a GitHub account** — fully autonomous AI agent,
  human pair-programming with Claude Code, or human typing by hand. The
  host doesn't care; they care that the PR is good.
- **Identity is GitHub.** No yo passwords, no device flow, no minted
  cross-machine tokens.
- **yo-server becomes a thin coordination/discovery layer** over a GitHub
  App. It never holds a credential that works on someone else's machine.

The whole untrusted-prompt-inside-your-Claude attack surface narrows to
"untrusted issue body in a repo your jammer agent has explicitly opted
into." Permissions, branch protection, CODEOWNERS, required reviewers are
all enforced server-side by GitHub. yo can never push to anyone's main.

The work-package primitive — clone → task → assign → PR — is auditable
(git history forever), verifiable (CI runs, tests pass), composable
(a PR review is itself a cypher), bountyable (host attaches $ to merge),
and self-onboarding (every developer alive already knows what "open a
PR" means).

## Concept mapping

| yo (today) | GitHub-native equivalent |
|---|---|
| cypher | Project (v2) |
| cypher goal | root draft item / pinned issue |
| sub-tasks / spawn fan-out | items (issues across any repo, or draft items) |
| capabilities per task | Project custom field |
| state: lobby / live / wrap | Project status field with automation |
| host | Project owner |
| `/join` | added to Project as collaborator |
| spawn → worker | `item.assignees += @user` |
| jammer accepts | picks up assigned item, opens PR in linked repo |
| reputation | merged-PR history, prior cypher participation |
| audit | Project activity + PR/issue trail |
| cancellation | close PR / unassign item |

## Cypher modes

A cypher's `Mode` is a Project custom field set at creation. Three modes
in v1:

| Mode | Slots | Semantics | Use case |
|---|---|---|---|
| `solo` | 1 jammer assigned | Standard single-assignee work | "agent fixes bug" / "human writes feature" |
| `tournament` | N submit, M win | Multiple jammers each open a PR; host picks winner(s) | Competitive / benchmark / bounty |
| `pipeline` | sequential hand-off | Item-N's output becomes Item-(N+1)'s input | Research → spec → code → review chain |

`solo` is the default. The other two are the strategic unlocks:

**`tournament`** turns yo into a *live benchmark substrate for AI dev
agents*. Whose agent has the highest tournament-merge-rate is real-world
performance data, not a static leaderboard. Losing PRs are still public,
producing a corpus of "N different approaches to the same task" — gold
for training, evals, post-mortems. Sponsored tournaments ("Anthropic
seeds $5K of cyphers in TS/Python; winners get API credits") are a
clean revenue rail: brand → pool, not user → user.

**`pipeline`** is the recursive-cypher case. A research item's output
seeds a spec item; the spec seeds a code item; the code seeds a review
item. Each hand-off is an item assignment with the prior item's output
in the body. This composes into long-horizon multi-agent work without
any new coordination primitive — it's just sequenced item creation.

## The MCP layer is the contract

The yo MCP isn't just a tool surface — it's the contract that makes
autonomous matchmaking, tournaments, and auto-merge work safely.

Three things ship together inside the MCP:

1. **Tools** — `mcp__yo__spawn`, `spawn_parallel`, `workers_online`.
2. **The bundled `dotyo-network` skill** — auto-installed to
   `~/.claude/skills/` on first launch. Teaches both hosts and jammers:
   - How to write a verifiable task: clear acceptance criteria, scope
     guards (don't refactor unrelated code), required tests.
   - What a good PR includes: passing CI, scope-bounded diff,
     conventional commit messages, comment that links task → PR → result.
   - How to behave inside a cypher: don't escalate, don't push to other
     branches, don't read repo files outside the task scope.
3. **The acceptance-criteria block** — every `spawn` emits a task body
   with a structured `ACCEPTANCE:` section. This is what `first-passing-ci`
   review mode validates against; without it the cypher falls back to
   `host` review.

This is what makes the work-package primitive *trustworthy*: hosts and
jammers share a common spec for what "done" means, enforced at the MCP
boundary.

## Architecture

Three components, each thinner than today.

### 1. The `yo-cypher` GitHub App

The linchpin. Owned by `glyphh-ai` org. Narrow scopes:

- **Repository** — Issues r/w, Pull requests r/w, Metadata r, Contents r
- **Organization** — Projects admin, Members r
- **Webhooks** — `project_v2_item`, `issues`, `pull_request`, `installation`,
  `installation_repositories`
- **No** admin, no settings, no merge-to-main, no delete. The App can route
  and assign; it cannot change repo policy. Branch protection / CODEOWNERS /
  required reviewers remain the host's humans-only domain.

Two flavors of auth from the App:

- **Installation tokens** — yo-server acts on the host's behalf within
  installed-org scope. Used for Project ops, assignments, comments.
- **User-to-server tokens (OAuth)** — used to identify a person ("Sign in
  with GitHub"). yo-server fetches profile + orgs and upserts a yo user
  record keyed on `gh_user_id`.

### 2. yo-server (coordination + discovery)

After the pivot, yo-server's job shrinks to four things:

1. **GitHub App broker** — installation flow, token rotation, webhook
   intake, signature verification.
2. **User profile + subscription registry** — capability profile (skills,
   blurb, model), Stripe customer, plan/trial state, concurrency limits.
   yo's user table is now a *profile overlay* keyed on `gh_user_id`, not a
   primary identity store.
3. **Routing / matchmaking** — when a Project item is created with a
   `capability` field, yo decides which jammer(s) it gets assigned to.
   Uses the App to apply the assignment.
4. **Discovery index** — searchable list of public/opt-in cyphers. Thin
   layer over GitHub search + a yo-side index for FTS.

What goes away:

- Device-flow auth, magic links, custom JWT minting for cross-machine
  trust (the App's installation tokens replace the latter).
- The in-memory SSE worker bus. Replaced by GitHub webhook delivery
  (retried, signed, audited by GitHub).
- The `YO_SPAWN_TOKEN` env-var problem. There is no such token anymore.
- `spawn-routes.ts` recursion + cycle + budget plumbing. Recursion is
  "an item creates a child item"; GitHub Projects already model this.
- Settlement on token usage. Reputation = merged-PR count is enough for
  v1; real money settlement (if ever) goes through GitHub Sponsors or a
  separate Stripe Connect rail.

### 3. dotyo (CLI / TUI / MCP / jammer daemon)

The TUI surface stays roughly the same — same slash commands, same
cockpit feel. Underneath, every operation translates to GitHub API calls
through yo-server's App:

- `yo /host "<goal>"` → yo-server creates a Project on the host's installed
  org via the App, returns URL + slug
- `yo /find [query]` → searches yo's discovery index (FTS over public
  cyphers' titles + descriptions)
- `yo /drop <ref>` → cockpit hydrated from Project board view + custom
  fields + recent items + comments
- `yo /join <ref>` → added to Project as collaborator (App applies)
- `yo /start` → flips Project status from `lobby` to `live`
- `yo /wrap` → archives Project, closes open items
- `mcp__yo__spawn(prompt, capabilities, ...)` → yo-server creates a child
  Project item (issue or draft) with `prompt` as body and `capability` set;
  matchmaker assigns to a jammer; jammer's daemon picks up via webhook

The local agent (jammer daemon) becomes:

1. Subscribe to GH App webhooks for `issues.assigned` / `project_v2_item.assigned`
   where `assignee = me`
2. On assignment: clone repo (if linked) or read draft item content,
   launch Claude Code with the item context as the prompt
3. When done: open PR (using the jammer's *own* GH PAT — yo never holds
   this credential) or post the result back as an item comment
4. Mark the item as done via the App

No yo-minted tokens, no env-var credential threading, no peer-to-peer
SSE. Webhooks → assignment → cloned repo → Claude Code → PR. The whole
loop is auditable in the host's GitHub history.

## Phase plan

Each phase has a concrete demoable outcome. Plan-only until checked off.

### Phase 9 — GitHub App scaffolding (~2 days)

- Create the `yo-cypher` App under `glyphh-ai` (manifest-defined so it's
  versioned in the repo)
- yo-server: `POST /api/github/webhook` — signature verification, ack 200,
  log payloads to a dev table for replay
- yo-server: `GET /api/auth/github/install` — kick off install flow
- yo-server: `GET /api/auth/github/callback` — capture `installation_id`,
  link to user/org, redirect to TUI sign-in success
- Manual install on a test org, verify webhook fires, verify `installation_id`
  captured

**Demo:** install the App on `glyphh-ai/yo-cypher-test`, see the install
event arrive in yo-server logs.

### Phase 10 — GH OAuth identity (~2 days)

- yo-server: GH OAuth login flow via the App's user-to-server tokens.
  Replaces device flow.
- Migrate `users` table: add `gh_user_id` as the natural key alongside
  existing `id`. Backfill existing dev users by email match where possible.
- dotyo: `yo login` opens a browser to the GH OAuth flow, captures
  redirect, persists the resulting yo session JWT (yo still mints session
  JWTs for its own API; only the *origin of trust* moves to GH).
- Org-level installations: when an org installs the App, create an
  `org` row tied to a yo subscription seat pool.

**Demo:** fresh user runs `yo login` → browser → "Continue with GitHub"
→ TUI confirms identity. No password ever set.

### Phase 11 — first Project ops + cypher modes (~3 days)

- `/host "<goal>"` defaults to the user's personal account. On first run,
  prompt: "host this cypher under your personal account `@<user>` or an
  org you've installed yo on?" Save the choice per-user; switchable later.
- Creates a Project on the chosen account via the App.
- Project custom fields:
  - `Status` — `Lobby` / `Live` / `Wrap`
  - `Capability` — select from yo's capability vocabulary
  - `Mode` — `solo` (default) / `tournament` / `pipeline`
  - `ReviewMode` — `host` (default) / `first-passing-ci`
  - For `tournament`: also `Slots` (N submitters) and `Winners` (M picked).
- **MCP spawn signature:**
  `mcp__yo__spawn(prompt, *, repo?, capabilities?, mode?, model?, timeout_ms?)`.
  - `repo` present → code task (clone, expect PR). Item gets `Repo` custom
    field set; jammer's daemon clones and works on a feature branch.
  - `repo` absent → draft item (expect comment with artifact). Useful for
    research summaries, design docs, analysis — including non-code
    tournaments where "submission" = comment.
  - The bundled `dotyo-network` skill teaches the orchestrator to format
    `prompt` with a structured `ACCEPTANCE:` block (required tests, scope
    guards, success criteria) — required for `ReviewMode = first-passing-ci`.
- `mcp__yo__spawn` reads the parent cypher's `Mode` and behaves accordingly:
  - `solo` → create item, matchmaker assigns one jammer (host never picks)
  - `tournament` → create item, matchmaker assigns N jammers in parallel;
    each opens their own PR (or comment, for draft items); winner picked
    per `ReviewMode` (host clicks, or first PR with all CI green merges
    automatically); losing PRs auto-close with feedback comment
  - `pipeline` → create item with prior item's output in body, matchmaker
    assigns next jammer in the chain
- **Matchmaker v1 algorithm:** round-robin among capability-matched +
  online + under-concurrency-cap jammers. Reputation-weighted in Phase 16.
  The host *never* picks the assignee — assignment is always algorithmic.
- yo-server stores a `cyphers` row as a thin pointer to
  `(installation_id, project_id)` — not a copy of Project state. Mode is
  read from the Project's custom field, not duplicated server-side.
- `/cyphers` lists Projects yo knows about (filtered to those visible to
  the user); columns include `mode` so `tournament` cyphers stand out.

**Demo:** `yo /host "build a CLI for X" --mode tournament --slots 3` produces
a real GitHub Project URL on the user's personal account. Project board
renders with `Mode = tournament` field set, three jammers assigned, three
parallel PRs in flight.

### Phase 12 — webhook → routing (~3 days)

- Item created on a Project with `Capability = research` and `assignee = null`
  triggers yo-server's matchmaker
- Matchmaker picks a jammer (capability ∈ stack, online, under concurrency
  cap) and uses the App to set `item.assignees`
- Jammer daemon receives the webhook, surfaces the assignment in the TUI

**Demo:** host creates an item via `mcp__yo__spawn`, the item appears
assigned to a matched jammer in <2s. Jammer's TUI shows incoming task.

### Phase 13 — jammer daemon: pick up → PR (~3 days)

- On assignment in a repo-linked item, jammer:
  - clones the repo (if not present locally)
  - launches Claude Code with the issue body + repo context
  - works in a feature branch
  - opens a PR via the jammer's own GH PAT
  - posts a comment on the source item linking the PR
- For draft items (no repo): result posted as item comment

**Demo:** end-to-end: host opens cypher → spawns "fix typo in README" →
matched jammer's daemon picks up → PR appears in the host's repo →
host merges → reputation tick.

### Phase 14 — TUI cockpit hydration (~2 days)

- `yo drop <ref>` cockpit fetches the live Project board via the App
- Renders columns (Lobby / Live / Wrap), items, capabilities, assignees
- Subscribes to `cypher_event_stream` (existing yo-server SSE) for live
  updates; under the hood that's now a GitHub-webhook fanout

**Demo:** two terminals on two machines watching the same cypher's
cockpit; an action on one reflects on the other in real time.

### Phase 15 — discovery + FTS (~2 days)

- yo-server indexes opt-in public cyphers (host marks Project
  `Visibility = public` via custom field)
- `yo find` queries the index; ranking by match score + recency
- Search joins on yo-side capability profile to default-filter to a
  user's stack

**Demo:** `yo find "TypeScript SDK help"` returns a ranked list of public
cyphers needing TS work.

### Phase 16 — reputation + settlement seed (~3 days)

- yo-server computes per-user reputation: merged PRs in cyphers, items
  completed, average time-to-merge, host satisfaction (later)
- Reputation surfaces in `/online` and `/find` results
- Settlement is *out of scope for v1* — but instrument the data so we
  can settle later if desired

**Demo:** `yo /online` shows each jammer with capability + reputation.
Matchmaker prefers higher-reputation jammers within a capability.

## What we're NOT building in this pivot

- Custom token economy, wallets, ledgers.
- Recursive-spawn-with-budget machinery (no budget — see THE-PLAN's
  earlier yos pivot; GH already prevents runaway via webhook delivery
  budgets and human merge gates).
- A peer-to-peer SSE worker bus. Webhooks replace it. (We may keep the
  legacy bus during transition, but new work goes through GH.)
- GitLab / Forgejo support. Tiny minority of devs; abstract over forges
  in v2 if demand shows up.
- yo-side OAuth for non-GitHub identity providers. The constraint
  "jammer must have a GitHub account" is a *feature* (Sybil resistance,
  reputation, real identity), not a tax.
- A hard binding to Claude Code as the jammer's runtime. **A jammer is
  anyone running a Claude-Code-compatible MCP-aware harness.** Claude
  Code is the reference implementation; OpenClaw, Aider-with-MCP, and
  future tools are first-class. The MCP layer + bundled skill define
  the contract — the runtime is pluggable.

## Open questions (iterate before build)

### Resolved before Phase 11 (2026-05-05)

- **Matchmaker algorithm.** Algorithmic, never host-picks. Round-robin v1
  among capability-matched + online + under-cap jammers. Reputation
  weighting in Phase 16.
- **Repo linking.** Optional `repo` parameter on the MCP `spawn` tool.
  With `repo` → code task, expect PR. Without → draft item, expect
  comment with artifact (works for non-code tournaments too).
- **Tournament reviewer.** `ReviewMode = host` (default) or
  `first-passing-ci`. Both ship in v1. Auto-merge requires the task body
  to include a structured `ACCEPTANCE:` block, taught by the bundled
  `dotyo-network` skill.
- **Tournament + draft items.** Yes, supported. "Submission" = comment
  on the item containing the artifact. Host (or auto rule) picks best
  comment; comment-author is the winner.
- **Runtime concentration.** A jammer is anyone running a
  Claude-Code-compatible MCP-aware harness. Claude Code is the reference
  implementation; OpenClaw, Aider-with-MCP, and future tools are
  first-class. Contract lives in the MCP layer + bundled skill, not in
  any specific runtime.

### Resolved before Phase 9

1. ~~**Org vs. user installation default.**~~ **Resolved 2026-05-05:**
   default to the user's personal account, then prompt once: "host this
   cypher under `@<user>` or an org you've installed yo on?" Save the
   choice per-user; switchable later. Architecture supports both
   identically.
2. ~~**Discovery: pull or push?**~~ **Resolved 2026-05-05:** webhook +
   cache. Index public cyphers via `project_v2_item` webhooks; fall back
   to live GH search only if the cache is stale.
3. ~~**Capability vocabulary fixed or open?**~~ **Resolved 2026-05-05:**
   slugs only. Fixed vocabulary, no free-form tags. Simpler matching,
   simpler UI, simpler skill instructions. Add tags later if a real use
   case demands it.
4. ~~**Item source-of-truth: Project or yo-side?**~~ **Resolved
   2026-05-05:** cache in yo-server, reconcile via webhooks. Periodic
   full-resync to recover from missed deliveries. Required for FTS on
   `/find` and sub-100ms cockpit hydration; standard SaaS pattern.
5. ~~**What about non-code jammers running yo without ever using
   Claude Code?**~~ **Resolved 2026-05-05:** redundant with the runtime
   concentration resolution above. Runtime is pluggable; CC is the
   reference; non-code work runs on the same harness with `repo` omitted.
6. ~~**Migration of existing dev users.**~~ **Resolved 2026-05-05:** N/A
   — there are no production device-flow users to migrate. GH OAuth is
   the only login path from day one; the device-flow code can be removed
   without a transition window.
7. ~~**Pricing for org installs.**~~ **Resolved 2026-05-05:** per-seat.
   When a GH org installs the yo App, the org is the customer; yo bills
   per active member-user, not a flat installation fee. Mirrors the
   standard B2B GH-App SaaS pattern (Linear, Vercel). Personal subs
   remain a separate path for individual jammers.
8. ~~**The legacy yo-server cypher-build branch.**~~ **Resolved
   2026-05-05:** no deprecation concerns. Remove the legacy SSE worker
   bus, device-flow auth, and yos wallet code as they become obsolete
   during the build — no compatibility window required.

## What changes for `yo-server`

`yo-server/cypher-build` keeps most of its routes during transition.
New additions for the pivot:

- `src/routes/github-app-routes.ts` — install flow, callback, webhook
- `src/routes/github-oauth-routes.ts` — sign-in-with-GitHub
- `src/services/github-app-service.ts` — App JWT signing, installation
  token rotation, REST/GraphQL clients
- `src/services/matchmaker-service.ts` — capability + reputation routing
  (replaces in-memory worker bus over time)
- `src/migrations/03X_github_app.sql` — `installations` table, `gh_user_id`
  on users, `cyphers.project_id` + `cyphers.installation_id`

What gets *deleted* (no compatibility window — there are no production
users to break):

- Device-flow auth routes
- In-memory SSE worker bus + `spawn-routes.ts`
- Yos balance / ledger (already deprecated in `cypher-build`; finish removal)
- `capabilities-routes.ts` device-flow companion endpoints

## First step

Phase 9 is the unblocker. Nothing else can be built until the App exists,
the install flow works, and webhooks land at yo-server. Estimated 2 days.
Demoable: install the App on a test org, see webhook fire.

---

*Last updated: 2026-05-05. Plan-only — iterate before build.*
