"""Postgres writes. Uses DATABASE_URL (Supabase/Neon connection string)."""
from __future__ import annotations

import json
import logging
import os

import psycopg

log = logging.getLogger(__name__)


def connect():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    # Supabase's default statement timeout can kill large jsonb writes (the
    # per-candidate candle payload) when the free-tier DB is cold — give our
    # batch writes a 5-minute budget instead
    return psycopg.connect(url, options="-c statement_timeout=300000")


def save_run(conn, run_date: str, regime: dict, candidates: list[dict], sectors: list[dict],
             sector_news: list[dict] | None = None) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO scan_runs (run_date, regime, status, sector_news)
               VALUES (%s, %s, 'complete', %s)
               ON CONFLICT (run_date) DO UPDATE SET regime = EXCLUDED.regime,
                   status = 'complete', sector_news = EXCLUDED.sector_news, created_at = now()
               RETURNING id""",
            (run_date, json.dumps(regime), json.dumps(sector_news or [])),
        )
        run_id = cur.fetchone()[0]

        cur.execute("DELETE FROM candidates WHERE run_id = %s", (run_id,))
        for c in candidates:
            tgt = c.get("targets") or {}
            cur.execute(
                """INSERT INTO candidates
                   (run_id, ticker, bucket, rs_rank, price, pivot, stop, sector,
                    extended, checks, vcp, extension, earnings, news,
                    target_2r, target_3r, reasoning, candles, levels,
                    adr_pct, quality, industry, group_rs, market, patterns, setup, name,
                    reasoning_sections, fundamentals)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (run_id, c["ticker"], c["bucket"], c["rs_rank"], c["price"],
                 c.get("pivot"), c.get("stop"), c.get("sector"),
                 c.get("extended", False), json.dumps(c.get("checks", {})),
                 json.dumps(c.get("vcp", {})), json.dumps(c.get("extension", {})),
                 json.dumps(c.get("earnings")), json.dumps(c.get("news", [])),
                 tgt.get("target_2r"), tgt.get("target_3r"),
                 c.get("reasoning"), json.dumps(c.get("candles", [])),
                 json.dumps(c.get("levels", {})),
                 c.get("adr_pct"), c.get("quality"), c.get("industry"), c.get("group_rs"),
                 c.get("market", "US"), json.dumps(c.get("patterns", {})),
                 json.dumps(c.get("setup", {})), c.get("name"),
                 json.dumps(c.get("reasoning_sections", [])),
                 json.dumps(c.get("fundamentals"))),
            )

        cur.execute("DELETE FROM sector_ranks WHERE run_id = %s", (run_id,))
        for s in sectors:
            cur.execute(
                """INSERT INTO sector_ranks
                   (run_id, etf, sector, rank, rs_raw, mom_1m_pct, mom_3m_pct,
                    rel_mom_1m_pct, rel_mom_3m_pct, quadrant, rotating_in, rotating_out)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (run_id, s["etf"], s["sector"], s["rank"], s["rs_raw"],
                 s["mom_1m_pct"], s["mom_3m_pct"], s["rel_mom_1m_pct"],
                 s["rel_mom_3m_pct"], s["quadrant"], s["rotating_in"], s["rotating_out"]),
            )
    conn.commit()
    log.info("Saved run %s: %d candidates, %d sectors", run_date, len(candidates), len(sectors))
    return run_id


def load_ticker_meta(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT ticker, industry, sector, name FROM ticker_meta")
        return {t: {"industry": i, "sector": s, "name": n} for t, i, s, n in cur.fetchall()}


def save_ticker_meta(conn, fresh: dict) -> None:
    with conn.cursor() as cur:
        for t, m in fresh.items():
            cur.execute(
                """INSERT INTO ticker_meta (ticker, industry, sector, name)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT (ticker) DO UPDATE SET industry = EXCLUDED.industry,
                       sector = EXCLUDED.sector, name = EXCLUDED.name, updated_at = now()""",
                (t, m.get("industry"), m.get("sector"), m.get("name")),
            )
    conn.commit()


def load_bursa_fundamentals(conn, max_age_days: int = 60) -> dict[str, dict]:
    """Cached Bursa fundamentals (weekly Apify refresh). Entries older than
    max_age_days are ignored — a dead refresh job degrades to Yahoo, never
    serves months-old numbers as current."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT ticker, data FROM bursa_fundamentals
               WHERE updated_at > now() - make_interval(days => %s)""",
            (max_age_days,),
        )
        return {t: d for t, d in cur.fetchall()}


def save_bursa_fundamentals(conn, data: dict[str, dict]) -> None:
    with conn.cursor() as cur:
        for t, d in data.items():
            cur.execute(
                """INSERT INTO bursa_fundamentals (ticker, data)
                   VALUES (%s, %s)
                   ON CONFLICT (ticker) DO UPDATE SET data = EXCLUDED.data,
                       updated_at = now()""",
                (t, json.dumps(d)),
            )
    conn.commit()


def _ts(v):
    """Tolerant ISO parse for feed timestamps; None rather than a guess."""
    if not v:
        return None
    try:
        import datetime as dt
        return dt.datetime.fromisoformat(str(v).replace("T", " ").strip())
    except ValueError:
        return None


def known_news_ids(conn, ticker: str, kind: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT item_id FROM counter_news WHERE ticker = %s AND kind = %s",
                    (ticker, kind))
        return {r[0] for r in cur.fetchall()}


def save_counter_news(conn, ticker: str, kind: str, items: list[dict]) -> int:
    """Per-counter news/announcement history (PLAN §7.2). UPSERT, never
    DELETE. Items without an item_id (no /view/ link) are skipped — there is
    nothing to dedupe them on. Returns rows written."""
    n = 0
    with conn.cursor() as cur:
        for it in items:
            if not it.get("item_id"):
                continue
            cur.execute(
                """INSERT INTO counter_news
                       (ticker, kind, item_id, title, url, source, category,
                        published_at, date_text)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (kind, item_id) DO UPDATE
                       SET title = EXCLUDED.title,
                           category = EXCLUDED.category,
                           published_at = COALESCE(EXCLUDED.published_at,
                                                   counter_news.published_at)""",
                (ticker, kind, it["item_id"], it["title"], it.get("url"),
                 it.get("source") or None, it.get("category"),
                 _ts(it.get("date")), (str(it.get("date") or "")[:40] or None)))
            n += 1
    conn.commit()
    return n


def apply_migrations(conn) -> None:
    """Run all db/migrations/*.sql in order. Safe to re-run (IF NOT EXISTS)."""
    import pathlib
    base = pathlib.Path(__file__).parent.parent / "db"
    mig_dir = base / "migrations"
    files = [base / "schema.sql"] if (base / "schema.sql").exists() else []
    files += sorted(mig_dir.glob("*.sql")) if mig_dir.exists() else []
    for f in files:
        sql = f.read_text()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
            log.info("Migration applied/verified: %s", f.name)
        except Exception as e:
            conn.rollback()
            log.error("Migration %s failed: %s", f.name, e)
            raise
