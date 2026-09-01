#!/usr/bin/env bash
# Nightly pg_dump of the realestate database. Keeps the 7 most recent dumps.
set -euo pipefail

BACKUP_DIR="${RE_BACKUP_DIR:-/opt/realestate/backups}"
KEEP=7

mkdir -p "$BACKUP_DIR"

# RE_DATABASE_URL is a SQLAlchemy URL (postgresql+psycopg://...); pg_dump wants
# a plain libpq URI.
pg_uri="${RE_DATABASE_URL/+psycopg/}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="$BACKUP_DIR/realestate-$stamp.dump"

pg_dump --format=custom "$pg_uri" >"$out"
echo "wrote $out ($(du -h "$out" | cut -f1))"

# prune old dumps (names are realestate-<utc-timestamp>.dump; ls -t is fine here)
# shellcheck disable=SC2012
ls -1t "$BACKUP_DIR"/realestate-*.dump 2>/dev/null | tail -n "+$((KEEP + 1))" | xargs -r rm --
