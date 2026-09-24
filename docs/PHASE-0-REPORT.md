# Phase 0 Completion Report: Foundation

- Status: **Complete. Approved 2026-09-24.**
- Verified against: commit `0f60a33`, with the stack running the production-like images from `docker compose up -d --build --wait`.
- Scope: [PLAN-PHASE-0-1.md §4 Phase 0](PLAN-PHASE-0-1.md)

## Summary

Phase 0 delivers a deployable skeleton:
- operator authentication
- an API with health and readiness endpoints, and RFC 9457 errors
- structured logging with secret redaction
- least-privilege database roles
- a Docker stack with a single entry point through Caddy
- the dashboard shell
- the architecture rules, enforced in CI

Verification was run fresh, with scripted checks against the running stack plus real-browser tests in Microsoft Edge. The run produced 100 checks with no product defects. Its 4 initial misses were all traced to the verification scripts themselves and re-checked.

| Area | Checks | Result |
|---|---|---|
| Docker and database | 6 groups | Pass |
| Security | 35 | Pass |
| Architecture | 3 contracts + negative tests | Pass |
| CI pipeline (all 13 steps, run locally) | 13 | Pass |
| Frontend (Edge, headless) | 29 | Pass |

## 1. Docker

- **Services:** caddy, web, api, postgres and redis are all `running` and `healthy`, with 0 restarts. The one-shot `migrate` service exited with code 0.
- **Health endpoints:** `/healthz` returns `{"status":"ok"}`. `/readyz` returns 200 `ready`, with database, migrations and redis all ok.
- **Migration state:** `alembic_version` = `0002`, which equals the image head (`alembic heads` / `alembic current` → `0002 (head)`). Tables: `users`, `sessions`, `alembic_version`. Extension: `citext`.
- **Database roles:**
  - Tables are owned by `acc_owner`.
  - `acc_app` has only SELECT, INSERT, UPDATE and DELETE. `CREATE TABLE` as `acc_app` fails with *permission denied*.
  - `acc_app` has no superuser, create-role or create-database rights.
- **Exposure:** Only Caddy publishes host ports (80 and 443). The API (8000), Postgres (5432) and Redis (6379) can't be reached from the host.
- **Container hardening:** api runs as uid 10001 and web as uid 1000. Both have a read-only root filesystem, `cap_drop: ALL` and `no-new-privileges`.

## 2. Security

**Configuration guards** (tested with the real API image and `ACC_ENV=production`):

| Input | Result |
|---|---|
| Placeholder `ACC_SESSION_SECRET` | refuses to start (exit 1) |
| Placeholder password inside `ACC_DATABASE_URL` | refuses to start (exit 1) |
| `http://` public origin | refuses to start (exit 1) |
| Session secret shorter than 32 characters | refuses to start (exit 1) |
| Generated secrets and an `https://` origin | starts (exit 0) |

`.env` holds generated secrets and is git-ignored.

**Sessions:**
- Cookie flags: `HttpOnly; SameSite=Strict; Path=/; Max-Age=604800`.
- The database stores a 64-character HMAC of the token, never the token itself.
- Passwords are stored as `$argon2id$` hashes.
- A forged cookie, an idle-expired session, and a replayed cookie after logout all get 401. Logout sets `revoked_at`.

**CSRF protection:**
- Mutating requests with a missing Origin, `https://evil.example` or the lookalike `http://localhost.evil.example` get 403.
- A cross-site logout or password change sent with a valid cookie gets 403, and the session survives.
- Safe (GET) requests are unaffected.

**Brute force:**
- The 6th failed attempt gets 429 with `Retry-After: 900`, even when the password is correct.
- An unknown email and a wrong password return the same 401 message.

**Headers:** `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, `Cache-Control: no-store` on the API, `X-Request-ID`. No `Server` header.

## 3. Architecture

- All import-linter contracts are kept: *domain is pure*, *API never touches ORM models*, *layering*.
- **Static scan:** zero ORM writes outside `acc.commands`. The only match in `acc.api` was a Python `dict.update`. Only `acc.commands` and `acc.queries` import `acc.db.models`.
- **Negative tests:** violations were injected into a throwaway copy of the backend, never the repo.

| Injected violation | Contract result | Exit code |
|---|---|---|
| API imports ORM models | *API never touches ORM models* BROKEN | 1 |
| Domain imports `sqlalchemy` | *domain is pure* BROKEN | 1 |
| Domain imports `acc.commands` | *domain is pure* and *layering* BROKEN | 1 |

- **CI:** every step of `.github/workflows/ci.yml` passed locally:
  - ruff check, ruff format, mypy, lint-imports
  - 41 backend tests, with integration tests required rather than skippable
  - OpenAPI and typed-client freshness
  - eslint, typecheck, 21 web tests, `next build`
  - The workflow file parses as valid YAML (jobs: backend, web, smoke).

## 4. Frontend (Microsoft Edge, headless)

- **Login:**
  - `/tasks` while signed out redirects to `/login?next=%2Ftasks`.
  - A wrong password shows "Invalid email or password." and re-enables the button.
  - A correct password lands on `/tasks`.
  - The session cookie is invisible to JavaScript.
  - `?next=//evil.example` redirects to `/` (open-redirect blocked).
- **Shell:** the operator name comes from the server-side session check. There are 4 nav links, and the status indicator shows "All systems ready".
- **Navigation:** Overview, Agents, Tasks and Activity each show the correct heading and `aria-current="page"`. Back, Forward and reload keep the session. Unknown routes return 404.
- **Responsive and theme:** at 390px the nav moves to a top bar with no horizontal overflow. Dark mode follows the OS preference.
- **Logout:** goes to `/login`, the cookie is cleared, and protected pages redirect to login.

## Findings and follow-ups

| # | Finding | Severity | Resolution |
|---|---|---|---|
| 1 | A settings validation error could echo part of the raw configuration | Low | Fixed after Phase 0: `hide_input_in_errors=True`, with a regression test verified red then green |
| 2 | The system-status indicator was hidden on mobile | Low | Fixed after Phase 0: always visible, dot-only on narrow screens |
| 3 | Host port 8000 is used by another local Docker project (`emega-sync-service`) | Info | No impact, because the API is never published. Documented in the README. |
| 4 | CI has not yet run on GitHub (no remote configured) | Open | Add the GitHub remote. The same steps pass locally. |
| 5 | TOTP second factor | Deferred | Deliberately deferred until after Phase 1 |

## Reproducing the checks

```bash
docker compose up -d --build --wait
scripts/smoke.sh              # end-to-end through Caddy
scripts/test-backend.sh       # backend unit + integration
cd apps/web && pnpm test      # web unit tests
cd backend && uv run lint-imports
```
