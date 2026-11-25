#!/usr/bin/env bash
# Creates the read-only role the agent connects as.
# Even if the LLM writes a destructive query and it slips past the SQL guard,
# Postgres itself refuses it: the role has SELECT only and every transaction
# is read-only with a statement timeout.
set -euo pipefail

: "${ANALYST_RO_PASSWORD:?ANALYST_RO_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 \
     -v ro_password="$ANALYST_RO_PASSWORD" \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'EOSQL'
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analyst_ro') THEN
        CREATE ROLE analyst_ro LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
END
$$;

ALTER ROLE analyst_ro PASSWORD :'ro_password';
ALTER ROLE analyst_ro SET default_transaction_read_only = on;
ALTER ROLE analyst_ro SET statement_timeout = '30s';
ALTER ROLE analyst_ro SET idle_in_transaction_session_timeout = '60s';

REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM analyst_ro;
GRANT USAGE ON SCHEMA public TO analyst_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analyst_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO analyst_ro;
EOSQL

echo "analyst_ro role ready"
