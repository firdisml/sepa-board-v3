#!/usr/bin/env bash
# Nightly logical backup, kept OFF this box.
#
# Contabo's Auto Backup is a paid add-on that is NOT enabled on this instance,
# so without this script one disk failure erases every signal outcome and
# backtest — and core value #3 is that receipts survive forever. Supabase was
# doing this silently for free; it is now our job.
#
# Cron (03:10 UTC daily, after the 22:00 US scan and before Bursa's 12:30):
#   10 3 * * * /opt/sepa/backup.sh >> /var/log/sepa-backup.log 2>&1
set -euo pipefail

BACKUP_DIR=${BACKUP_DIR:-/opt/sepa/backups}
KEEP_DAYS=${KEEP_DAYS:-14}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$BACKUP_DIR/sepa-$STAMP.sql.gz"

mkdir -p "$BACKUP_DIR"

# Dump THROUGH the container, straight to gzip. Direct pg_dump (not via
# PgBouncer): transaction pooling breaks pg_dump's consistent snapshot.
docker compose -f /opt/sepa/docker-compose.yml exec -T db \
  pg_dump -U sepa -d sepa --no-owner --no-acl | gzip -9 > "$OUT"

SIZE=$(stat -c%s "$OUT")
# A dump that small is a failed dump, not a small database. Fail loudly
# rather than rotating good backups out in favour of empty ones.
if [ "$SIZE" -lt 1000000 ]; then
  echo "FATAL: dump is only $SIZE bytes — refusing to treat as valid" >&2
  rm -f "$OUT"; exit 1
fi
echo "$(date -u +%FT%TZ) dumped $OUT ($((SIZE/1024/1024)) MB)"

# OFFSITE is the point. A backup on the same disk as the database is not a
# backup. Configure ONE of these, then delete this guard.
if [ -z "${OFFSITE_TARGET:-}" ]; then
  echo "WARNING: OFFSITE_TARGET unset — backup exists ONLY on this box." >&2
else
  # rclone handles B2/S3/Drive/etc with one config; rsync works for a box you own.
  rclone copy "$OUT" "$OFFSITE_TARGET" && echo "copied offsite -> $OFFSITE_TARGET"
fi

find "$BACKUP_DIR" -name 'sepa-*.sql.gz' -mtime +"$KEEP_DAYS" -delete
