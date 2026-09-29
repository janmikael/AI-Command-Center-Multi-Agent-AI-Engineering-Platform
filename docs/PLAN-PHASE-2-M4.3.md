# Execution Plan: Phase 2 M4.3 (First Complete Real AI Developer Workflow)

Status: **PAUSED — preparation complete, live validation pending.** Approved and
implemented (role prompt tweak, hermetic-repo seed content, build-script fix; see §1/§2 —
602/602 tests pass, none needing a real key). The one thing this milestone actually exists
to prove — a real task → run → tool calls → file changes → commit → usage → result chain,
using a real Anthropic API key — has **not** happened: no key is configured (centrally
managed, not provided for this milestone as of 2026-09-29), and per instruction nothing
here substitutes a shortcut for that real run. **Do not read this document as M4.3 being
done.** Resume by picking up §6/§7 once a key is available.
Parent documents: [PLAN-PHASE-2-M4.md](PLAN-PHASE-2-M4.md), [PLAN-PHASE-2-M4.2.md](PLAN-PHASE-2-M4.2.md)
Builds on: commit `c79b67c` (M4.2 — tool execution hardening, agent run reliability)
Date: 2026-09-28 (paused 2026-09-29)

## Scope, as given

The first complete real AI developer workflow: real Claude task execution, live tool usage verification, repository modification workflow, test execution, result reporting, human review point. Exclusions: autonomous merge, deployment, multi-agent workflow. Objective: one safe, observable, end-to-end developer task.

## 0. Reading the scope precisely

**0.1 This is chiefly a verification-and-close-the-gaps milestone, not a new-feature milestone, and I want to say that plainly before the six sections below make it look bigger than it is.** Every mechanism this milestone needs already exists in code: the SDK adapter (M4.1), the git clone/branch/commit/push workflow (M3, wired into the SDK path in M4.1), tool governance (M4.1/M4.2), usage/failure tracking (M4.1/M4.2), and the task landing in REVIEW on success (M1's own state machine, unchanged since). What has never happened is running all of it together, for real, against a real repository, with a real agent actually doing development work — M4.1 and M4.2's own live verification both deliberately used an invalid API key specifically to prove the plumbing without spending anything (PLAN-PHASE-2-M4.md §12, restated in both milestones' final reports). This milestone is where that key finally gets used for what it's for. I expect small, targeted fixes surfacing from real verification (M4.1 found two this way, M4.2 found three), not a large new build.

**0.2 The one thing this milestone does need that doesn't exist yet: a repository to actually work against.** Every prior milestone since M2 has left "Repository: [add test repository URL]" unfilled and worked around it — M3 built a hermetic dev-only git server specifically so it never had to ask; M4.1/M4.2's live tasks had no repository at all. This milestone's whole point is a repository modification workflow, so it can't route around this again. §2 below recommends continuing with the same hermetic server rather than asking for a real GitHub repository now — reasoning there.

**0.3 "Test execution" and "human review point" are the two items on your list that name something this system doesn't structurally distinguish today, and I want to flag that precisely rather than quietly deciding it.** The developer agent already has Bash access and an advisory governance rule ("Run tests before completing tasks") reaches it as prompt text — so it *can* run tests today, and doing so shows up as ordinary `tool_calls` rows, same as any other Bash command. There is no dedicated "did tests run, did they pass" signal separate from that. Similarly, "human review point" already exists structurally (a successful run moves its task to REVIEW, same as every prior milestone), but ACC stores diff *stats* (path/change-type/±lines), not diff *content* — a reviewer sees what changed, not the actual code, unless they go look at the pushed branch themselves. §4 and §6 lay out what I'd add and what I'd deliberately leave alone, and why.

## 1. Real Claude task execution

**What exists**: `runner/agent_sdk.py::run_agent` — one `query()` session, the four-layer context, `can_use_tool` governance, usage/failure reporting (M4.1/M4.2). Never run against a task with a real repository and a real key.

**What this milestone verifies, live**: a real task — title, description, and **acceptance criteria** (the `tasks.acceptance_criteria` field, already rendered into the task prompt by `render_task_prompt`, but never populated in any live verification task so far — every M4.1/M4.2 test task used an empty description and no criteria) — assigned to the `developer` agent, moved through PLANNING → WORKING, producing a real `agent_runs` row that reaches `RUNNING`, real `agent.message` events as the model works, and a real `ResultMessage` at the end.

**Small, likely fix**: the role prompt (`acc.queries.context.AGENT_ROLE_PROMPTS["claude_agent_sdk"]`) currently says "implement, test, and prepare a small, reviewable change" but never explicitly instructs *running* whatever tests it writes before finishing — it relies entirely on the governance rule's advisory text reaching the model as a separate paragraph. Recommend folding an explicit instruction into the role prompt itself ("run any tests you write before finishing; a task is not done until they pass") — a one-line prompt change, not a new mechanism.

## 2. Repository modification workflow

**What exists**: `runner/main.py::run_claude_agent_sdk` already clones, branches, and — after the agent runs — commits/pushes/reports the diff, unconditionally when a repository is linked (M4.1 §3, unchanged by M4.2). `git_ops.py`'s branch-name allowlist and credential-injection discipline (M3) are unchanged and apply here without modification.

**What repository to use**: recommend continuing with `infra/git-test-server` (M3's hermetic, dev-only git server) rather than asking for a real GitHub repository now. It's real git — a real clone, a real push, a real branch landing on a real remote, verifiable independently (`git ls-remote`, exactly as M3's own tests already do) — the only thing "hermetic" about it is that it isn't reachable from outside this Docker network and needs no credential. A real external repository is a reasonable *next* milestone once this pipeline is proven end to end here; asking for one now would gate this milestone on a decision (which repo, whose account, what permissions) that isn't actually required to prove the workflow. Flagged as a recommendation, not a decision made for you — see §7.

**What content to seed**: the hermetic repo currently has exactly one file, `README.md` (`infra/git-test-server/entrypoint.sh`). For a real task to be "repository modification" rather than "repository creation," I'd add a couple more real files before running this milestone's live task — a small Python module and its test file, so the agent is genuinely editing something, not just authoring from nothing. This is a one-time change to the seed script's content, not new application code.

**What this verifies, live**: the agent's `Write`/`Edit` calls land in the actual cloned workspace (not just the SDK's own sandboxed understanding of it); `git_ops.commit` produces a real commit with the bot identity (M3); `git_ops.push` lands a real `acc/<task>-<id>` branch on the remote; `git_ops.diff_stat` correctly reports what changed; `POST /internal/runs/{id}/file-changes` records it; `GET /api/v1/runs/{id}/file-changes` shows it to an operator.

## 3. Live tool usage verification

This is where M4.1's and M4.2's own flagged unknowns finally get resolved against a real call, not left as "confirmed only at live-verification time":

- **`PostToolUse`'s `tool_response` shape** (PLAN-PHASE-2-M4.2.md §1.1, still unconfirmed): the hook's best-effort extractor (`_extract_tool_response`) degrades gracefully today, but I don't yet know if it extracts anything *useful* from a real Bash/Read/Write/Edit response. This milestone is where I find out, and adjust the extractor if the real shape doesn't match what it currently guesses.
- **`ModelUsage`'s exact key names** (confirmed via SDK package introspection in M4.1, never exercised by a real API response) — a real successful session's usage record is the first real confirmation that `costUSD`/`inputTokens`/etc. actually populate as expected, not just that the TypedDict declares them.
- **Governance enforcement against real tool calls**: M4.1/M4.2 verified the *mechanism* (unit/integration tests with synthetic rules), never a real agent naturally attempting something a real rule would catch. I'd deliberately shape the test task's acceptance criteria to make at least one governance-relevant tool call likely (e.g., asking the agent to run the test suite via a plain `pytest` invocation, which is allowed, while a seeded `deny` rule's pattern — e.g. "Destructive commands require approval"'s `rm -rf`/`dd` pattern — stays untriggered by a normal dev task; I am *not* proposing to deliberately provoke a denial by instructing the agent to do something destructive, since that's an artificial test, not real usage).

**What this verifies, live**: `tool_calls` rows for every Read/Write/Edit/Bash/Glob/Grep the agent actually makes, each completed (`duration_ms`/`exit_code`/`result_excerpt`) by a real hook firing; redaction (M4.2) applied to real arguments/output, not just synthetic secret-shaped test strings; `GET /api/v1/runs/{id}/tool-calls` showing the real sequence to an operator.

## 4. Test execution

Deliberately **not** adding a dedicated "tests ran / tests passed" column or event this milestone (§0.3). Reasoning: the agent running tests is just another Bash tool call — `pytest`, `python -m unittest`, whatever the task's language calls for — and it already gets recorded, completed, and redacted like any other. A dedicated pass/fail signal would need ACC to *parse* the agent's test-runner output to know what "passed" means across arbitrary test frameworks and languages, which is a real, separate feature (test-result parsing) that the task list's exclusions (no multi-agent workflow, keep this to one safe end-to-end task) argue against building speculatively right now. What this milestone does instead: shape the test task so running tests is a natural part of completing it (an acceptance criterion literally says so, and the role-prompt tweak in §1 reinforces it), and verify live that the resulting `tool_calls` sequence and the model's own `agent.message` narration make it obvious to a human reviewer whether tests were run and what they reported — visibility through the general mechanism, not a bespoke one.

## 5. Result reporting

**What exists**: `result_summary` (agent_runs, surfaced via `RunOut`), `file_changes` (M3), `branch_name` (M3), token/cost totals (M1/M4.1/M4.2), the full event trail. All exercised only by the M3 marker-file stub or M4.1/M4.2's auth-failure paths so far — never by a real multi-file, real-content agent-authored change.

**What this verifies, live**: `result_summary` is `ResultMessage.result` — a real model's own closing summary, not a hardcoded string; confirm it's coherent and within the existing 4000-char bound rather than getting cut off mid-sentence for a longer real response. `file_changes` correctly reflects a multi-file diff, not the single-file marker stub every prior test exercised. No code changes anticipated here — this section is pure verification of already-built plumbing against real output for the first time.

## 6. Human review point

**What exists and stays exactly as-is**: `complete_run` moves a successful run's task to `REVIEW` (M1, unchanged); nothing in this codebase ever moves a task past `REVIEW` on its own — the next transition (`review` → `completed`/`working`/back) is an operator API call requiring a session, same as every other transition. This milestone adds no new transition, no auto-advance, no merge.

**What I'd confirm, live, and what I'd explicitly leave alone**: after a real successful run, a human (me, standing in for you, since this is verification not a UI) should be able to look at the task in REVIEW and have enough to actually judge the work — title/description/acceptance criteria (already there), `result_summary` (§5), `branch_name` + `file_changes` stats (§2), the full `tool_calls`/`agent.message` trail (§3). What's **not** there and what I'm **not** proposing to add: the actual diff *content* (hunks, not just stats) inside ACC itself — a reviewer today has to look at the pushed branch directly (via `git diff` against the hermetic remote, or a real PR view once/if a real GitHub repository is in the picture) to see the real code. Building an in-app diff viewer is a dashboard/frontend concern, and a genuinely separate piece of work from "prove the backend pipeline is safe and observable end to end" — flagging it as a known, deliberate gap rather than silently declining to mention it.

## 7. Open decisions needing your input before I code

- **A real Anthropic API key**, same requirement as M4.1/M4.2, but for the first time actually load-bearing for this milestone's success criterion (a real successful task), not just its live-verification nicety.
- **Repository**: recommend the existing hermetic `git-test-server` (§2) over a real external repository for this milestone. If you'd rather use a real throwaway GitHub repo now, I need its URL and, per M3's established model, a scoped credential (push-only, to that one repo) — same "no credentials in git config/remote URLs, GIT_ASKPASS only" discipline as always.
- **Test task shape**: recommend a small, concrete task against the hermetic repo — e.g. "add a `slugify(text: str) -> str` function to a new module, with a passing test" — specific enough to have a clear, checkable acceptance criterion, small enough to stay well under the existing cost ceiling. Open to a different task if you'd rather specify one.
- **Role-prompt tweak** (§1): a one-line addition instructing the agent to run any tests it writes before finishing. Recommend yes; it's the cheapest way to make "test execution" observable without new infrastructure.

Waiting for your approval on this plan, and the decisions above, before writing any code.
