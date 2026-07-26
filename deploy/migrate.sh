#!/usr/bin/env bash
# One-shot Supabase -> self-hosted migration, with verification.
#
# Usage: SRC="postgresql://...supabase..." DST="postgresql://...contabo..." ./migrate.sh
set -euo pipefail
: "${SRC:?set SRC to the Supabase connection string}"
: "${DST:?set DST to the new Postgres connection string}"

DUMP=${DUMP:-/tmp/sepa-migrate.sql}

# -n public ONLY: Supabase's auth/storage/realtime/vault schemas are its own
# scaffolding, unused by this app (verified — zero Supabase SDK calls), and
# restoring them would fail on missing roles.
# --no-owner/--no-acl: the source roles (supabase_admin et al) do not exist here.
echo "== dumping public schema from source =="
pg_dump --no-owner --no-acl -n public --clean --if-exists "$SRC" > "$DUMP"
echo "dump: $(du -h "$DUMP" | cut -f1)"

echo "== restoring =="
psql -v ON_ERROR_STOP=1 -d "$DST" -f "$DUMP"

# VERIFY, table by table. "It restored without erroring" is not the test —
# a silently truncated table would pass that and lose history forever.
echo "== verifying row counts =="
Q="select table_name from information_schema.tables where table_schema='public' and table_type='BASE TABLE' order by 1"
FAIL=0
for t in $(psql -At -d "$SRC" -c "$Q"); do
  a=$(psql -At -d "$SRC" -c "select count(*) from public.\"$t\"")
  b=$(psql -At -d "$DST" -c "select count(*) from public.\"$t\"")
  if [ "$a" = "$b" ]; then printf '  OK   %-22s %s\n' "$t" "$a"
  else printf '  DIFF %-22s src=%s dst=%s\n' "$t" "$a" "$b"; FAIL=1; fi
done
[ "$FAIL" = 0 ] && echo "ALL TABLES MATCH" || { echo "MISMATCH — do not cut over"; exit 1; }
