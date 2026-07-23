"""Deep news/announcement backfill from KLSE Screener's paginated feeds
(PLAN §7.2). Dispatch-only: at the 8s throttle a 50-page history is ~7 min
of fetching per feed, which has no place inside the nightly scan — the scan
gets its increments free from the dossier's embedded items.

Safe to re-run: known item_ids short-circuit the page walk, and every write
is an UPSERT. Zero new items on a counter with an empty archive exits 1 —
that is a parse failure to look at, not an empty history.

Run: python -m scanner.backfill_news --ticker 5326.KL --max-pages 60
     python -m scanner.backfill_news --ticker 5326.KL --dump-html feed_pages
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib

import requests

from . import db, klse_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill_news")

FEEDS = [
    ("news", klse_client.news_feed, klse_client.NEWS_FEED_PATH),
    ("announcement", klse_client.announcements_feed, klse_client.ANN_FEED_PATH),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", required=True, help="internal ticker, e.g. 5326.KL")
    ap.add_argument("--max-pages", type=int, default=60, help="pages per feed")
    ap.add_argument("--dump-html", metavar="DIR",
                    help="also save each feed's page-1 raw HTML here "
                         "(parser-fixture capture)")
    args = ap.parse_args()

    code = klse_client.code_of(args.ticker)
    conn = db.connect()
    db.apply_migrations(conn)
    session = requests.Session()

    wrote = {}
    for kind, feed, path in FEEDS:
        if args.dump_html:
            out = pathlib.Path(args.dump_html)
            out.mkdir(parents=True, exist_ok=True)
            html = klse_client._get(klse_client._feed_url(path, code, 1),
                                    session=session)
            (out / f"{kind}_{code}_p1.html").write_text(html)
        known = db.known_news_ids(conn, args.ticker, kind)
        items = feed(code, max_pages=args.max_pages, session=session,
                     known_ids=known)
        wrote[kind] = db.save_counter_news(conn, args.ticker, kind, items)
        log.info("%s %s: %d new rows (archive had %d). sample: %s",
                 args.ticker, kind, wrote[kind], len(known),
                 json.dumps(items[:3], ensure_ascii=False))

    if not any(wrote.values()) and not db.known_news_ids(conn, args.ticker, "news"):
        log.error("no items parsed and archive is empty — parser or feed "
                  "layout problem, not a quiet counter")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
