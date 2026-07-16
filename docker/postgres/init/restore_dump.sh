#!/usr/bin/env bash
set -euo pipefail

# Restore only reviewed cryptocurrency market data from the mixed Callscore archive.
# The PostgreSQL entrypoint invokes this script only for a fresh data directory.

DUMP_PATH="/docker-entrypoint-initdb.d/callscore.dump"
RESTORE_LIST="/usr/local/share/market-structure-lab/crypto_only_restore.list"

: "${POSTGRES_DB:?POSTGRES_DB must be configured}"

if [ ! -f "${DUMP_PATH}" ]; then
  echo "[restore_dump] required dump not found at ${DUMP_PATH}" >&2
  exit 1
fi

if [ ! -f "${RESTORE_LIST}" ]; then
  echo "[restore_dump] crypto restore manifest not found at ${RESTORE_LIST}" >&2
  exit 1
fi

expected_sha256="$(sed -n 's/^; dump_sha256=//p' "${RESTORE_LIST}")"
expected_size="$(sed -n 's/^; dump_size_bytes=//p' "${RESTORE_LIST}")"
actual_sha256="$(sha256sum "${DUMP_PATH}" | awk '{print $1}')"
actual_size="$(stat -c '%s' "${DUMP_PATH}")"

if [ -z "${expected_sha256}" ] || [ -z "${expected_size}" ]; then
  echo "[restore_dump] crypto restore manifest is missing dump identity metadata" >&2
  exit 1
fi

if [ "${actual_sha256}" != "${expected_sha256}" ] || [ "${actual_size}" != "${expected_size}" ]; then
  echo "[restore_dump] dump identity does not match the reviewed crypto restore manifest" >&2
  exit 1
fi

echo "[restore_dump] restoring reviewed crypto objects into database ${POSTGRES_DB}"

pg_restore \
  --verbose \
  --exit-on-error \
  --no-owner \
  --no-acl \
  --use-list="${RESTORE_LIST}" \
  --dbname="${POSTGRES_DB}" \
  "${DUMP_PATH}"

# Keep only source market fields and isolate them from the archive's application schema.
psql --set=ON_ERROR_STOP=1 --dbname="${POSTGRES_DB}" <<'SQL'
BEGIN;
CREATE SCHEMA market_data;
ALTER TABLE public.candles SET SCHEMA market_data;
ALTER TABLE public.ticks SET SCHEMA market_data;
-- PostgreSQL moves each owned serial sequence with its table. Moving it again by
-- its old public-schema name fails after a successful table move.
ALTER TABLE market_data.candles
  DROP COLUMN regime,
  DROP COLUMN confidence,
  DROP COLUMN returns,
  DROP COLUMN volatility,
  DROP COLUMN volume_ratio;
COMMIT;
SQL

relations="$(
  psql --set=ON_ERROR_STOP=1 --dbname="${POSTGRES_DB}" --tuples-only --no-align --command "
    SELECT n.nspname || '.' || c.relname
    FROM pg_catalog.pg_class AS c
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r', 'p', 'm', 'v', 'f', 'S')
      AND n.nspname NOT IN ('information_schema')
      AND n.nspname NOT LIKE 'pg_%'
    ORDER BY 1
  "
)"
expected_relations="$(printf '%s\n' \
  'market_data.candles' \
  'market_data.candles_id_seq' \
  'market_data.ticks' \
  'market_data.ticks_id_seq')"

if [ "${relations}" != "${expected_relations}" ]; then
  echo "[restore_dump] unexpected relation set after crypto-only restore:" >&2
  printf '%s\n' "${relations}" >&2
  exit 1
fi

echo "[restore_dump] crypto-only restore finished"
