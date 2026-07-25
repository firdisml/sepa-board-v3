"""One-time warehouse seed (PLAN §3.3, Phase 1).

Pulls `history()` for every live common stock on both exchanges — roughly
9,000 calls against a 100,000/day budget — and writes the rolling window.
Safe to re-run: every write is an upsert, and `--market`/`--limit` let you
resume or rehearse without redoing the whole thing.

Run: python -m scanner.backfill --market US --years 2
     python -m scanner.backfill --market MY --limit 50   # rehearsal
"""
from __future__ import annotations

import argparse
import logging

from . import db, warehouse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill")


def main() -> int:
    ap = argparse.ArgumentParser()
    # MY-only in v3.0 (PLAN §1 scope change). US is parked, not deleted — the
    # engine stays multi-market, so reactivation is `--market US`, not a rebuild.
    ap.add_argument("--market", choices=["US", "MY", "ALL"], default="MY")
    ap.add_argument("--years", type=int, default=2)
    ap.add_argument("--limit", type=int, help="first N symbols only (rehearsal)")
    ap.add_argument("--offset", type=int, default=0,
                    help="skip the first N symbols — resume a long seed after "
                         "a timeout without re-pulling what already landed")
    args = ap.parse_args()

    markets = ["US", "MY"] if args.market == "ALL" else [args.market]
    conn = db.connect()
    warehouse.ensure_schema(conn)

    for market in markets:
        directory = warehouse.eodhd_symbols(market)
        tickers = list(directory["ticker"])
        if args.offset:
            tickers = tickers[args.offset:]
        if args.limit:
            tickers = tickers[: args.limit]
        # Seed NAMES from the vendor directory. MY names come from KLSE
        # Screener's universe table at scan time; US has no equivalent source,
        # so without this every US candidate renders nameless. COALESCE keeps
        # any richer name/industry/sector already stored (never NULL them —
        # that's the save_ticker_meta trap).
        seed = directory[directory["ticker"].isin(set(tickers))]
        with conn.cursor() as cur:
            for _, r in seed.iterrows():
                if not r["name"] or r["name"] == "nan":
                    continue
                cur.execute(
                    """INSERT INTO ticker_meta (ticker, name)
                       VALUES (%s, %s)
                       ON CONFLICT (ticker) DO UPDATE
                           SET name = COALESCE(ticker_meta.name, EXCLUDED.name),
                               updated_at = now()""",
                    (r["ticker"], str(r["name"])[:120]))
        conn.commit()
        log.info("seeded %d %s names into ticker_meta", len(seed), market)

        log.info("backfilling %s: %d symbols x %dy", market, len(tickers), args.years)
        warehouse.backfill(conn, market, years=args.years, tickers=tickers)

    log.info("warehouse: %s", warehouse.size_report(conn))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
