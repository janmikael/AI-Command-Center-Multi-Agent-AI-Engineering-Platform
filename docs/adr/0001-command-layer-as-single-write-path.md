# ADR 0001: The command layer is the single write path

- Status: Accepted
- Date: 2026-09-24

## Context

The architecture requires three things:
- the database owns state
- agents never update status directly
- every change goes through the orchestrator

In Phases 0 and 1 there is no orchestrator process and there are no agents yet, but the API already mutates state (sessions now; tasks and assignments in Phase 1). If routers write through the ORM directly, those writes would have to be found and moved later, and the rules would be conventions nobody checks.

## Decision

1. **All writes live in `acc.commands`.** Each command is a function that runs in one transaction (`acc.db.session.transaction`). From Phase 1 on, each command also appends its event in that same transaction.
2. **Reads live in `acc.queries`**, which returns plain objects. Queries never write.
3. **Callers are thin.** API routers, the CLI and later the orchestrator worker call commands. They never touch ORM models themselves.
4. **Machine-enforced** by import-linter contracts in `backend/pyproject.toml` (run in CI):
   - `acc.api` may not import `acc.db.models`
   - `acc.domain` may not import frameworks, the database or outer layers
   - layering: `cli → api → commands | queries → db → domain`
5. **Business rules are pure functions in `acc.domain`**, for example the password policy, session expiry and (Phase 1) the task state machine. They are unit-tested without I/O.

## Consequences

- When the orchestrator arrives (Phase 2), it becomes one more caller of the same commands. Agent *requests* are validated by the same state machine and recorded with a different actor.
- A little ceremony: even a trivial update needs a command function. That is accepted in exchange for a single place to audit writes.
- import-linter checks imports, not behavior. A query that issues an `UPDATE` would pass the linter, so code review still matters there.
