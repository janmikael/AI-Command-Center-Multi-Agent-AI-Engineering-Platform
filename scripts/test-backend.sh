#!/usr/bin/env bash
# Runs the backend test suite against the dev Postgres/Redis (acc_test database, Redis db 15).
# Usage: scripts/test-backend.sh [pytest args...]
set -euo pipefail
cd "$(dirname "$0")/.."

[[ -f .env ]] || scripts/init-env.sh
set -a
# shellcheck disable=SC1091
. ./.env
set +a

docker compose -f compose.yml -f compose.dev.yml up -d --wait postgres redis docker-socket-proxy >/dev/null
docker compose -f compose.yml -f compose.dev.yml build runner >/dev/null

export ACC_TEST_DATABASE_URL="postgresql+asyncpg://acc_app:${ACC_APP_DB_PASSWORD}@127.0.0.1:${ACC_PG_PORT:-5432}/acc_test"
export ACC_TEST_MIGRATIONS_DATABASE_URL="postgresql+asyncpg://acc_owner:${ACC_OWNER_DB_PASSWORD}@127.0.0.1:${ACC_PG_PORT:-5432}/acc_test"
export ACC_TEST_REDIS_URL="redis://:${REDIS_PASSWORD}@127.0.0.1:${ACC_REDIS_PORT:-6379}/15"
# The launcher's own integration tests (tests/integration/test_launcher_docker.py) create
# real containers through this — the same restricted proxy the orchestrator uses.
export ACC_TEST_DOCKER_PROXY_URL="tcp://127.0.0.1:${ACC_DOCKER_PROXY_PORT:-2375}"

cd backend
exec uv run pytest "$@"
