# Execution Plan: Phase 2 M3 (Repository & Workspace Access Layer)

Status: **Awaiting approval. No code written.**
Parent documents: [PLAN-PHASE-2.md](PLAN-PHASE-2.md), [PLAN-PHASE-2-M2.md](PLAN-PHASE-2-M2.md)
Builds on: commit `02a249d` (M2 — runner sandbox, docker-socket-proxy, internal API, orchestrator)
Date: 2026-09-25

## Scope, as given

Repository and workspace access layer:
- connect projects to repositories
- create isolated workspaces/worktrees per run
- branch isolation
- controlled git operations
- collect diffs/file changes
- prepare runner environment for future coding agents

Do not implement yet: Claude Agent SDK, AI prompts, MCP gateway tools, production repository access, deployment.

## 0. Reading the scope precisely

**0.1 This is narrower than the "M3" in PLAN-PHASE-2.md's original milestone table, and that's a genuine, welcome improvement, not a deviation worth resisting.** The original plan bundled "Claude Agent SDK adapter, real execution... git worktree, the SDK's tool loop... commit+push" into one milestone. This session's instructions split that into two: this M3 (git plumbing, no intelligence behind it) and a later milestone — I'll call it **M4** — that adds the actual SDK and lets an agent decide what to change. I'm updating the milestone numbering to reflect that split rather than silently keeping two different "M3"s in two documents. The old M4 (Dashboard) becomes M5, old M5 (Reliability hardening) becomes M6. Flagging this renumbering explicitly rather than letting the docs drift out of sync.

**0.2 "Collect diffs/file changes" without "real code modification" needs one concrete resolution: what does the runner actually change?** M2's runner proved the sandbox round trip with zero capability behind it (a timed heartbeat loop, nothing else). M3's runner needs to prove the *git* round trip the same way: clone, branch, make **one fixed, hardcoded, non-negotiable change** (writing a single marker file with a constant name and constant content — not an LLM decision, not configurable per task), commit, push, and report the diff. This is the direct git-plumbing analogue of M2's timed stub loop. "Real code modification" — an agent *deciding* what to change — stays out until M4, by construction: there is no decision-making code in this milestone at all, only a fixed action.

**0.3 "Controlled git operations" means a fixed, non-extensible set of git subcommands the runner's own code calls — not tools exposed to anything yet.** Clone, create-branch, add, commit, push (refused outside the `acc/<key>-*` namespace by the wrapper itself), diff. This is deliberately the same shape M4's tool surface will eventually expose to an agent, built now against a caller that can't misuse it (there's no agent yet to misuse it), so M4 only has to wire a caller onto plumbing that's already been exercised for real.

**0.4 Repository connection is almost entirely already built — checked, not assumed.** Phase 1 already has `repositories` (id, project_id, name, remote_url, default_branch, **protected_branches**) and `tasks.repository_id` (nullable FK), plus `create_project`/`add_repository` commands and the task-detail query already resolving a task's repository name. "Connect projects to repositories" needs zero new schema — it needs the orchestrator to actually *use* what's already there when launching a run, which it currently doesn't (M2's launcher never looks at `task.repository_id` at all).

**0.5 The repository/credential decision (PLAN-PHASE-2-M2.md §7.1, deferred twice already) is unavoidable now and needs your input before I can finish this plan concretely — see §2.**

## 1. Architecture impact

- **The runner image gains its first real dependency beyond the Python stdlib: `git`.** This is a deliberate, narrow expansion of the trust boundary (M2's runner had literally nothing installed beyond the base image). `git` itself is not scriptable by anything an attacker controls here — the runner's own code decides every argument; no shell string ever includes untrusted input (task titles, agent output, etc. — none of that exists yet, so this is easy to keep true in M3 and worth keeping true from the start).
- **The launcher gains repository awareness.** `handle_start_run` currently only reads `run.task_id`/`run.agent_id`. It now also resolves `task.repository_id → repositories` (a new small query), and — if the task has no repository — the run proceeds exactly as M2 already does today (no git, no workspace change): repository access stays *additive*, not required, so plain non-code tasks keep working unchanged.
- **New env vars injected per run** (alongside the existing `RUN_ID`/`AGENT_ID`/`RUN_TOKEN`/`API_BASE_URL`): `GIT_REMOTE_URL`, `GIT_DEFAULT_BRANCH`, `GIT_BRANCH_NAME` (computed by the launcher, not the runner — see §3), and the injected credential (§2). A task with no repository gets none of these; the runner's git step is skipped entirely when they're absent.
- **`/workspace`, already reserved as a tmpfs mount since M2, is where the clone happens.** No new mount, no new container config beyond what M2 already set up — this was sized and isolated for exactly this from the start.
- **New internal endpoint**: `POST /internal/runs/{id}/file-changes` (batch), writing to `file_changes` — a table M1 built and explicitly left unused, with a comment saying it would be wired up "alongside the real adapter." This is that moment, just without the adapter behind it yet.
- **A gap in M1/M2 worth closing here, not carried forward again**: `complete_run` already writes `agent_runs.branch_name` but never copies it onto `tasks.branch_name` — a column that's existed since M1 specifically for this ("populated once the agent pushes a branch") and has never been set. Fixing this is in scope for M3 since it's the same commit that starts producing real branch names.

## 2. Repository security model

**2.1 The open decision this plan can't finish without.** Same question as PLAN-PHASE-2-M2.md §7.1, now load-bearing: which repository does the runner actually clone in M3, and how is push access provisioned? My recommendation, unchanged from M2's plan and worth restating concretely now:
- A single **throwaway test repository** (not a real project's codebase) — something you create specifically for this, expendable, so a bug in this milestone's git code can't damage anything that matters.
- A **fine-grained, repo-scoped credential** (a GitHub/GitLab fine-grained PAT or deploy key limited to that one repo), with host-side branch protection on its default branch so a direct push there is rejected by the *host*, not just by our own convention.
- You create the repo and the credential; hand me the remote URL and the token/key. I don't need repo contents ahead of time — the runner clones it itself.

**2.2 Credential storage for M3 is deliberately the simplest correct thing, not the final design.** One credential, one repository, held as a new `Settings` field (`git_credential`, a `SecretStr`, following the exact pattern `runner_token_secret` already established in M2) — **not** a new column on `repositories` holding a raw secret. Storing per-repository credentials properly (a reference to an external secret, not the secret itself, in Postgres) is real work that "production repository access" — explicitly excluded here — would actually require; building that storage now for a single test repository would be speculative. The `repositories` table itself needs no schema change for this reason: which repo a task uses is already modeled (§0.4); which *credential* authenticates against it is, for M3, a single configured value, not a per-row one.
- **A second repository can still be connected and even cloned read-only in M3** (no credential needed for that) — the credential is only reached at push time. What M3 does *not* support is two different repositories both needing *push* access with different credentials; that's the real gap "production repository access" would close.

**2.3 How the credential reaches the runner, and how it doesn't leak.** Injected as a container env var (`GIT_CREDENTIAL`) at launch, same lifetime and handling as the run token already gets. Git itself is told about it via `GIT_ASKPASS` pointing at a tiny script (baked into the runner image) that echoes the env var — **never** written into `.git/config`, **never** embedded in the remote URL (which would otherwise leak it into `git log`, error messages, and the reflog). HTTPS, not SSH, for the remote — one fewer network protocol to allowlist on `runner-egress` (matches the DNS/firewall egress-allowlisting approach from PLAN-PHASE-2-M2.md's future-work note), and no host-key handling to get right.

**2.4 Branch isolation is enforced at two layers, honestly described as different strengths, same as PLAN-PHASE-2-M2.md §4.3's tool-enforcement was:**
- **Soft, but real (this milestone's own code):** the runner's git wrapper has exactly one function capable of pushing, and it refuses any branch name not matching `acc/<task_key>-*` before it ever shells out. There is no code path in the runner that can push anywhere else — not a policy check that could be wrong, an absence of the capability.
- **Hard (requires your action, outside this codebase):** host-side branch protection on the repository's protected branches (already modeled in `repositories.protected_branches`, unused by any enforcement yet) is what stops a *compromised* runner from pushing to `main` directly, since a compromised process could in principle ignore its own wrapper. This is exactly the same "defense in depth, not equivalent to the target design" honesty PLAN-PHASE-2-M2.md §4.3 already applied to tool-call enforcement — I'm not claiming M3's code alone makes force-pushing `main` impossible, only that it never attempts to.

**2.5 Read access (clone, fetch, diff, log) is unrestricted within the one connected repository** — there's no sensitive-path denylist here the way the filesystem MCP server will eventually have (ARCHITECTURE.md §4.2, still not built); a throwaway test repo has nothing in it worth restricting reads to. Worth a note for when this moves toward production repos, not a gap to close now.

## 3. Workspace lifecycle

```
handle_start_run (extended)
    │
    ├─ task has no repository_id → unchanged M2 behavior, no git, no new env vars
    │
    └─ task has a repository_id:
        1. Look up the repository (remote_url, default_branch)
        2. Compute the target branch name deterministically:
              acc/<task_key>-<short run id>
           (the launcher decides this, not the runner — see §2.4/§5: the runner is
           never in a position to claim it pushed somewhere other than what we told it)
        3. Inject GIT_REMOTE_URL, GIT_DEFAULT_BRANCH, GIT_BRANCH_NAME, GIT_CREDENTIAL
        4. mark_run_starting also now records workspace_ref (§6) and the computed
           branch name on the run row, before the container is even started —
           authoritative from our side, not self-reported

Inside the container (runner's extended entrypoint, still just heartbeat + one fixed
action, per §0.2):
    1. git clone --depth 1 --branch <default_branch> <remote_url> /workspace/repo
    2. git checkout -b <branch_name>
    3. Write the fixed marker file (constant name, constant content)
    4. git add -A && git commit -m "<fixed message>"
    5. git push origin <branch_name>   (wrapper refuses anything else, §2.4)
    6. git diff <default_branch>...<branch_name> --numstat  → POST .../file-changes
    7. POST .../complete { result_summary, branch_name }
```

- **Workspace teardown needs no new code.** `/workspace` is the tmpfs mount M2 already configured; it disappears with the container, whether the run succeeds, fails, is cancelled, or is reconciled away after an unexpected exit — all four paths already remove the container (M2's `_stop_and_remove` / `cleanup_finished_containers` / `reconcile_active_runs`).
- **A cancelled run mid-push is a known, accepted edge case, not fully solved here.** If `handle_cancel_run` stops the container between step 5 and step 6/7, the branch may exist on the remote with no `file_changes` row and no `complete` call — the run correctly lands in `CANCELLED` either way (M1's state machine doesn't care about git), but the operator sees a branch with no recorded diff. Worth a note in the live-verification pass; not worth new machinery in M3 for a stub change that's the same three bytes every time.

## 4. Git permissions model

The runner's git wrapper (new, small, part of the runner image — not the `acc` backend package, matching M2's decision to keep the runner's own code dependency-light and outside the layered import graph) exposes exactly five operations, each a thin, fixed-argument wrapper around one git subcommand:

| Operation | What it's allowed to do | What it refuses |
|---|---|---|
| `clone` | Shallow, single-branch clone of the one configured remote | Any other remote |
| `branch` | Create and check out exactly one new branch, from the default branch | Checking out or modifying an existing branch |
| `commit` | Stage everything, commit with a fixed message, fixed bot identity | Nothing configurable here — no message injection surface |
| `push` | Push the current branch, only if its name matches `acc/<task_key>-*` | Any other branch name, force-push, push to `protected_branches` |
| `diff` | Read-only stat/numstat against the default branch | N/A (read-only) |

No merge, no rebase onto the default branch, no arbitrary remote add, no `push --force`. Nothing here is a policy table an agent could later talk its way around — the capability to do anything else simply isn't code that exists yet, consistent with ADR-7 ("privileged actions are platform-executed only") applied one level down: even *unprivileged* git actions are capability-limited by construction, not by a rule the runner could be tricked into skipping, because M3 has no decision-making component to trick.

## 5. APIs/services added

**Internal** (extends `acc/api/internal/`, same auth as M2 — §2.4/2.5 of that plan, unchanged):
```
POST /internal/runs/{run_id}/file-changes   (batch: [{path, change_type, additions, deletions}])
```
`POST .../complete`'s existing `branch_name` field (already accepted by the schema and by `commands.runs.complete_run` since M1/M2 — just never populated by a real caller until now) starts actually being used.

**Operator-facing** (extends `acc/api/routers/runs.py`):
```
GET /api/v1/runs/{id}/file-changes
```
A thin read over the new table, same pattern as every other runs endpoint. Not built: `/tasks/{key}/diff` (ARCHITECTURE.md's aggregated-branch-diff endpoint) — that's a rendering/aggregation concern better sized alongside M5's dashboard work, once there's a UI consuming it.

**No new services.** No MCP gateway, no new container, no new compose entry beyond what the runner image's `Dockerfile` needs (adding `git`) — confirmed nothing here requires docker-socket-proxy scope changes (still container lifecycle only) or new network topology.

## 6. Database impact

**No new migration for repository connection** — `repositories` and `tasks.repository_id` already exist and are already sufficient (§0.4).

**One new migration**, small and additive:
```sql
-- agent_runs already has workspace_ref (nullable, unused since M1) and branch_name
-- (unused since M1) — both get populated starting this milestone, no schema change.
-- The one real gap: complete_run sets agent_runs.branch_name but never copies it to
-- tasks.branch_name, the column M1 added specifically for this. Not a migration —
-- a one-line addition to acc.commands.runs._finish_run's success path.
```
Actually: **zero new migrations.** Every column this milestone needs (`agent_runs.workspace_ref`, `agent_runs.branch_name`, `tasks.branch_name`, `file_changes.*`) was already built in M1, specifically anticipating this moment, and is only now getting a real writer. The one code gap (`tasks.branch_name` never being copied over) is a command-layer fix, not a schema change — flagged here because it's a real, findable bug, not because it needs a migration.

**New `Settings` field**: `git_credential: SecretStr` (§2.2), following the `runner_token_secret` pattern exactly — separate secret, same production-placeholder guard, same "never logged" discipline.

## 7. Testing strategy

**Unit (no Docker, no git binary needed):** branch-name computation (`acc/<key>-<id>` — deterministic, collision behavior if a task is retried), the git wrapper's refusal logic tested as pure argument-validation (given a branch name, does `push` refuse it — testable without actually invoking git), `file-changes` schema validation.

**Integration, against a real git remote — hermetic, not GitHub.** Mirroring M2's "real infra, not mocks" standard while keeping the *test* remote independent of the real credential decision in §2.1: a small dev-only bare git repository, served over HTTP (`git http-backend` or an equivalent minimal container) on the existing `runner-egress`-reachable network, exactly parallel to how `docker-socket-proxy` got a dev-only port mapping for M2's test suite. Tests clone from it, push to it, and inspect the resulting refs directly (`git ls-remote`) — real git, real HTTP, no dependency on your actual test repository or its credential.
- End to end: `handle_start_run` on a repository-linked task → real container → real clone → real branch → real push to the test remote → `file_changes` rows land correctly → `tasks.branch_name` gets set.
- Wrapper refusal: attempt (inside a test harness calling the wrapper directly, not through a full container) to push to `main` or to force-push — refused before any git process runs.
- No-repository task: unchanged M2 behavior confirmed still works (regression guard on §0.4's "additive, not required" claim).
- Cancelled-mid-push: confirmed to land in `CANCELLED` regardless of git state (§3's accepted edge case), not crash or hang.
- Credential hygiene: the credential never appears in the container's own git config, remote URL, or anything `file_changes`/event payloads would persist (a redaction-adjacent check, cheap to add now while it's still easy to verify).

**Live verification** (same bar as M1/M2): once §2.1 is answered, one real end-to-end run against your actual throwaway repository — confirm the branch really appears on the real host, with the real diff, and that a direct push attempt to its default branch is rejected by the host's own branch protection, not just by our wrapper.

## Open items

**§2.1 needs your answer before I write code**: the throwaway repository's remote URL and how you'd like to provision push credentials (a token/key you create and hand me a reference to, or something you'd like help setting up).

Waiting for your approval on this M3 plan, and the §2.1 decision, before implementation.
