#!/usr/bin/env bash
# End-to-end smoke test through Caddy against a running stack.
# Creates a throwaway operator unless SMOKE_EMAIL/SMOKE_PASSWORD point at an existing one.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a

BASE="${ACC_PUBLIC_ORIGIN:-http://localhost}"
EMAIL="${SMOKE_EMAIL:-smoke-$(date +%s)@example.com}"
PASSWORD="${SMOKE_PASSWORD:-smoke-test-password-$RANDOM}"
JAR="$(mktemp)"

cleanup() {
  rm -f "$JAR"
  # Remove the throwaway operator (admin cleanup of test data, not an application write).
  if [[ -z "${SMOKE_EMAIL:-}" ]]; then
    docker compose exec -T postgres psql -q -U postgres -d acc -v email="$EMAIL" \
      <<< "DELETE FROM users WHERE email = :'email';" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

pass() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$1"; exit 1; }
status() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

echo "Smoke test against $BASE"

[[ "$(status "$BASE/healthz")" == 200 ]] && pass "GET /healthz → 200" || fail "/healthz"
[[ "$(curl -s "$BASE/readyz")" == *'"status":"ready"'* ]] && pass "GET /readyz → ready" || fail "/readyz: $(curl -s "$BASE/readyz")"

loc="$(curl -s -o /dev/null -w '%{redirect_url}' "$BASE/tasks")"
[[ "$loc" == *"/login?next=%2Ftasks" ]] && pass "GET /tasks without session → redirect to login" || fail "redirect: $loc"
[[ "$(status "$BASE/login")" == 200 ]] && pass "GET /login → 200" || fail "/login"

if [[ -z "${SMOKE_EMAIL:-}" ]]; then
  printf '%s\n' "$PASSWORD" | docker compose exec -T api acc create-operator --email "$EMAIL" --name "Smoke Test" --password-stdin >/dev/null
  pass "acc create-operator"
fi

login() {
  local password="$1"; shift
  curl -s -o /dev/null -w '%{http_code}' -c "$JAR" -b "$JAR" -H 'content-type: application/json' "$@" \
    -d "{\"email\":\"$EMAIL\",\"password\":\"$password\"}" "$BASE/api/v1/auth/login"
}

[[ "$(curl -s -o /dev/null -w '%{http_code}' -H 'content-type: application/json' -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" "$BASE/api/v1/auth/login")" == 403 ]] \
  && pass "login without Origin header → 403 (CSRF check)" || fail "origin check"
[[ "$(login "wrong-password-123" -H "origin: $BASE")" == 401 ]] && pass "login with wrong password → 401" || fail "wrong password"
[[ "$(login "$PASSWORD" -H "origin: $BASE")" == 200 ]] && pass "login → 200" || fail "login"
grep -q acc_session "$JAR" && pass "session cookie set" || fail "no cookie"

me="$(curl -s -b "$JAR" "$BASE/api/v1/auth/me")"
[[ "$me" == *"\"email\":\"$EMAIL\""* ]] && pass "GET /api/v1/auth/me → operator" || fail "me: $me"
[[ "$(status -b "$JAR" "$BASE/")" == 200 ]] && pass "GET / (dashboard) with session → 200" || fail "dashboard"

[[ "$(status -X POST -b "$JAR" -c "$JAR" -H "origin: $BASE" "$BASE/api/v1/auth/logout")" == 204 ]] && pass "logout → 204" || fail "logout"
[[ "$(status -b "$JAR" "$BASE/api/v1/auth/me")" == 401 ]] && pass "after logout /me → 401" || fail "session still valid"

echo "All smoke checks passed."
