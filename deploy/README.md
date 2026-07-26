# Self-hosted Postgres (Contabo) — runbook

Replaces Supabase as the database. **Plain Postgres, not self-hosted
Supabase**: the app uses no Supabase feature (no `@supabase/supabase-js`
anywhere; `postgres.js` in web/, psycopg in scanner/), and every extension in
the source DB — pgcrypto, uuid-ossp, pg_stat_statements, plpgsql — ships with
stock Postgres. Supabase's ~10 services would add RAM, patching and CVE
surface for nothing.

## Facts about this instance
- Contabo Cloud VPS 4 (2026), 100 GB disk, `169.58.51.186`
- **Lauterbourg, FRANCE** (AS51167) — measured 175 ms from Malaysia
- **Auto Backup is NOT enabled** (paid add-on) → `backup.sh` is mandatory
- Source DB: Supabase `ap-south-1` (Mumbai), PostgreSQL 17.6, 410 MB

## The latency constraint — read before cutover
Moving the DB Mumbai (~50 ms) → France (~175 ms from MY). Serialised across
the 3–5 queries a dashboard page makes, that is 0.7–1 s of dead wait.

**Deploy Vercel to `fra1` (Frankfurt).** ~300 km from Lauterbourg, so
Vercel→Postgres is ~5 ms and a page load costs ONE Malaysia→EU trip
(~250 ms) instead of one trip plus N serialised queries. Leaving Vercel in an
Asian region while the DB sits in France is the one way to make this feel
broken.

GitHub Actions (the scanner) is US/EU, so Actions→France should be no worse
than today's Actions→Mumbai, likely better.

## Order of operations
1. `deploy/.env` with `PG_PASSWORD=` (long, random)
2. TLS certs into `deploy/certs/` as `fullchain.pem` + `privkey.pem`.
   The app connects with `ssl: "require"`, so this port must speak TLS.
   Easiest: `certbot certonly --standalone -d db.yourdomain` (needs a
   hostname; a bare IP cannot get a public cert — self-signed otherwise, and
   then the client must not verify the chain).
3. `docker compose up -d` then `docker compose ps` — db healthy, pgbouncer up
4. `SRC=… DST=… ./migrate.sh` — dumps, restores, **verifies row counts per
   table**. It exits non-zero on any mismatch; do not cut over on a mismatch.
5. Point the scanner at it first: `DATABASE_URL=… SCAN_MARKETS=US
   SCAN_FORCE=1 DRY_RUN=1 python -m scanner.scan`
6. Swap `DATABASE_URL` in Vercel **and** the GitHub secret; redeploy Vercel to
   `fra1`
7. Install the backup cron (below) and **test a restore** before trusting it
8. Keep Supabase alive ~1 week as rollback

## Hardening (do this before step 4 — Contabo ranges are scanned constantly)
```bash
# key-only SSH
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
systemctl reload sshd
# firewall: SSH + Postgres only
ufw default deny incoming && ufw allow 22/tcp && ufw allow 5432/tcp && ufw --force enable
apt-get install -y fail2ban && systemctl enable --now fail2ban
```
Vercel's serverless egress IPs are not static on Hobby/Pro, so IP-allowlisting
5432 is not available — TLS plus a strong password is the practical floor.

## Backups (mandatory — there is no Contabo backup on this box)
```bash
install -Dm755 backup.sh /opt/sepa/backup.sh
# pick an offsite target; a backup on the same disk is not a backup
echo 'OFFSITE_TARGET=b2:my-bucket/sepa' >> /etc/default/sepa-backup
( crontab -l 2>/dev/null; echo '10 3 * * * . /etc/default/sepa-backup; /opt/sepa/backup.sh >> /var/log/sepa-backup.log 2>&1' ) | crontab -
```
Restore drill (do it once, on purpose):
```bash
gunzip -c /opt/sepa/backups/sepa-<stamp>.sql.gz | psql -d sepa_restore_test
```

## Watch after cutover
- `docker compose exec db psql -U sepa -d sepa -c "select count(*) from pg_stat_activity"`
  — if it approaches `DEFAULT_POOL_SIZE`, raise it, not `max_connections`
- Disk: 100 GB vs 410 MB is ~240x headroom, but `candles` grows with markets
- `WAREHOUSE_WINDOW_DAYS=320` remains the cheap lever (PLAN §14)
