#!/usr/bin/env bash
# Creates .env from .env.example with freshly generated secrets.
# Refuses to overwrite an existing .env unless --force is given (that would orphan the
# database volume, whose role passwords were set on first start).
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env && "${1:-}" != "--force" ]]; then
  echo ".env already exists (use --force to regenerate; then also: docker compose down -v)"
  exit 0
fi

secret() { python3 -c "import secrets; print(secrets.token_hex($1))"; }

sed \
  -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(secret 24)|" \
  -e "s|^ACC_OWNER_DB_PASSWORD=.*|ACC_OWNER_DB_PASSWORD=$(secret 24)|" \
  -e "s|^ACC_APP_DB_PASSWORD=.*|ACC_APP_DB_PASSWORD=$(secret 24)|" \
  -e "s|^REDIS_PASSWORD=.*|REDIS_PASSWORD=$(secret 24)|" \
  -e "s|^ACC_SESSION_SECRET=.*|ACC_SESSION_SECRET=$(secret 32)|" \
  -e "s|^ACC_RUNNER_TOKEN_SECRET=.*|ACC_RUNNER_TOKEN_SECRET=$(secret 32)|" \
  .env.example > .env
chmod 600 .env
echo "Created .env with random secrets."
