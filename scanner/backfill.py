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
    args = ap.parse_args()

    markets = ["US", "MY"] if args.market == "ALL" else [args.market]
    conn = db.connect()
    warehouse.ensure_schema(conn)

    for market in markets:
        tickers = list(warehouse.eodhd_symbols(market)["ticker"])
        if args.limit:
            tickers = tickers[: args.limit]
        log.info("backfilling %s: %d symbols x %dy", market, len(tickers), args.years)
        warehouse.backfill(conn, market, years=args.years, tickers=tickers)

    log.info("warehouse: %s", warehouse.size_report(conn))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
