#!/usr/bin/env bash
# Runs once, when the Postgres data volume is first initialized.
#
#   acc_owner: owns the schema; used only by migrations.
#   acc_app:   runtime role for the API. Data access only (SELECT/INSERT/UPDATE/DELETE),
#              no DDL. Later migrations narrow this further (e.g. no UPDATE/DELETE on events).
#
# Databases: acc (application) and acc_test (backend integration tests).
set -euo pipefail
: "${ACC_OWNER_DB_PASSWORD:?ACC_OWNER_DB_PASSWORD is required}"
: "${ACC_APP_DB_PASSWORD:?ACC_APP_DB_PASSWORD is required}"

psql_admin() { psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" "$@"; }

# Passwords are passed as psql variables, never interpolated by the shell into SQL.
psql_admin --dbname postgres \
  -v owner_pw="$ACC_OWNER_DB_PASSWORD" -v app_pw="$ACC_APP_DB_PASSWORD" <<'SQL'
CREATE ROLE acc_owner LOGIN PASSWORD :'owner_pw';
CREATE ROLE acc_app LOGIN PASSWORD :'app_pw';
SQL

for db in acc acc_test; do
  psql_admin --dbname postgres -v db="$db" <<'SQL'
CREATE DATABASE :"db" OWNER acc_owner;
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO acc_app;
SQL

  psql_admin --dbname "$db" <<'SQL'
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO acc_app;
ALTER DEFAULT PRIVILEGES FOR ROLE acc_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO acc_app;
ALTER DEFAULT PRIVILEGES FOR ROLE acc_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO acc_app;
ALTER DEFAULT PRIVILEGES FOR ROLE acc_owner IN SCHEMA public
  GRANT EXECUTE ON FUNCTIONS TO acc_app;
SQL
done
