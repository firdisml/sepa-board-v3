"""Candle warehouse — the rolling price store both scans read from.

v2 fetched history per ticker at scan time and was therefore capped at
whatever the vendor's quota allowed (~280 names through the moomoo funnel),
which quietly broke RS: ranking survivors of a pre-filter inflates every rank
(PLAN §3.4). Here the whole exchange lands in Postgres once a night for two
bulk calls, so RS is a true full-universe percentile again and the scan does
no network I/O for prices at all.

Window: ~420 trading days (PLAN §3.3) — 260 bars for the Trend Template plus
252 for RS plus buffer. Rows are pruned past that; anything deeper (backtests)
is pulled on demand via `eodhd_client.history`, never stored.

SIZE WATCH: ~9k tickers x 420 rows is 3-4M rows. That is inside the Supabase
free tier but not comfortably — PLAN §14's mitigation is to shrink the window
to 320d or prune sub-liquidity tickers. Check `size_report()` monthly.
"""
from __future__ import annotations

import datetime as dt
import logging

import pandas as pd

from . import eodhd_client as eod
from .eodhd_client import DataUnavailable

log = logging.getLogger(__name__)

WINDOW_DAYS = 420          # trading days retained per ticker
MIN_BARS_FOR_SCAN = 200    # below this a ticker cannot be trend-templated
MAX_STALE_SESSIONS = 3     # a ticker silent longer than this is suspended or dead

# Regime instruments. These are NOT common stock, so `symbols()` filters them
# out and a universe backfill would silently omit them — leaving market_regime
# with nothing to read and the exposure ladder dead. MY uses the KLCI ETF for
# PRICE only; its volume (~700 units/day) is unusable, so distribution days
# come from aggregate exchange turnover instead (PLAN §12 Phase 0 result (e)).
BENCHMARKS = {"MY": ["0820EA.KL"], "US": ["SPY", "QQQ"]}

DDL = """
CREATE TABLE IF NOT EXISTS candles (
    ticker text    NOT NULL,
    d      date    NOT NULL,
    o      numeric,
    h      numeric,
    l      numeric,
    c      numeric,
    v      bigint,
    PRIMARY KEY (ticker, d)
);
CREATE INDEX IF NOT EXISTS candles_d_idx ON candles (d);
CREATE TABLE IF NOT EXISTS candles_meta (
    exchange       text PRIMARY KEY,
    last_ingested  date,
    symbols_seen   int,
    updated_at     timestamptz DEFAULT now()
);
"""


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()


# ---------------------------------------------------------------- ingest

def _upsert(conn, rows: list[tuple]) -> int:
    """Upsert, never delete-then-insert: a re-run mid-session must not leave
    the warehouse briefly empty for a ticker the scan is about to read."""
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO candles (ticker, d, o, h, l, c, v)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (ticker, d) DO UPDATE SET
                 o = EXCLUDED.o, h = EXCLUDED.h, l = EXCLUDED.l,
                 c = EXCLUDED.c, v = EXCLUDED.v""",
            rows,
        )
    conn.commit()
    return len(rows)


def ingest_bulk(conn, market: str, date: str | None = None) -> dict:
    """Pull one session for a whole exchange and record what arrived.

    Returns a report dict; the caller logs it. Coverage is reported loudly
    because PLAN §12 bans the v1 behaviour of silently skipping symbols:
    a >2% drop against the directory count must be visible in the scan log.
    """
    exchange = eod.EXCHANGES[market]
    df = eod.bulk_eod(exchange, date)
    bar_date = df["d"].mode().iat[0]

    rows = list(df[["ticker", "d", "o", "h", "l", "c", "v"]].itertuples(index=False, name=None))
    n = _upsert(conn, rows)

    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO candles_meta (exchange, last_ingested, symbols_seen)
               VALUES (%s,%s,%s)
               ON CONFLICT (exchange) DO UPDATE SET
                 last_ingested = EXCLUDED.last_ingested,
                 symbols_seen = EXCLUDED.symbols_seen, updated_at = now()""",
            (exchange, bar_date, n),
        )
    conn.commit()
    log.info("ingested %s: %d rows for %s", exchange, n, bar_date)
    return {"exchange": exchange, "bar_date": bar_date, "rows": n}


def assert_fresh(conn, market: str, expected_session: dt.date) -> None:
    """The freshness gate (PLAN §3.2) — mandatory before every scan.

    v1's Yahoo feed served Bursa EOD a full day late routinely: a scan on the
    14th published the 13th's board, and nobody noticed for months because
    the data always *existed*. Existence is not the test. The latest bar must
    BE today's session, or we abort — a skipped scan is recoverable, a
    silently day-late board is not.
    """
    exchange = eod.EXCHANGES[market]
    with conn.cursor() as cur:
        cur.execute("SELECT last_ingested FROM candles_meta WHERE exchange = %s", (exchange,))
        row = cur.fetchone()
    got = row[0] if row else None
    if got != expected_session:
        raise DataUnavailable(
            f"STALE DATA: {exchange} warehouse holds {got}, expected session "
            f"{expected_session}. Refusing to scan on stale prices."
        )
    log.info("freshness OK: %s at %s", exchange, got)


# ---------------------------------------------------------------- read

def load_window(conn, market: str, min_bars: int = MIN_BARS_FOR_SCAN,
                max_stale_sessions: int = MAX_STALE_SESSIONS) -> dict[str, pd.DataFrame]:
    """Whole-universe read -> {ticker: yfinance-shaped DataFrame}.

    Identical shape to v2's `download_batch` return value, so every ported
    indicator, pattern and backtest routine runs unmodified.

    STALENESS: a ticker must have traded within the last `max_stale_sessions`
    sessions to be returned. This excludes suspended counters (1368.KL sat 9
    sessions behind) and any vendor alias that quietly stops updating — both
    would otherwise be scanned at their frozen price and published as a live
    board entry. The default of 3 is deliberate: the scan runs BEFORE the
    exchange session is finalised, so a thin counter that simply did not trade
    today legitimately has yesterday's bar as its latest.
    """
    # `.KL` is the only suffix in the internal namespace, so it partitions the
    # two markets exactly — and per-market calendars must never mix inside one
    # rolling window (PLAN §1.1 value 4).
    where = "ticker LIKE %s" if market == "MY" else "ticker NOT LIKE %s"
    with conn.cursor() as cur:
        cur.execute(
            f"""WITH recent AS (
                    SELECT DISTINCT d FROM candles WHERE {where}
                    ORDER BY d DESC LIMIT %s
                ), live AS (
                    SELECT DISTINCT ticker FROM candles
                    WHERE {where} AND d >= (SELECT min(d) FROM recent)
                )
                SELECT ticker, d, o, h, l, c, v FROM candles
                WHERE {where} AND ticker IN (SELECT ticker FROM live)
                ORDER BY ticker, d""",
            ("%.KL", max_stale_sessions, "%.KL", "%.KL"),
        )
        rows = cur.fetchall()

    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=["ticker", "d", "o", "h", "l", "c", "v"])

    out: dict[str, pd.DataFrame] = {}
    for ticker, g in df.groupby("ticker", sort=False):
        if len(g) < min_bars:
            continue  # too short to trend-template; the IPO path needs 126+
        frame = pd.DataFrame({
            "Open": g["o"].astype(float).values,
            "High": g["h"].astype(float).values,
            "Low": g["l"].astype(float).values,
            "Close": g["c"].astype(float).values,
            "Volume": g["v"].astype(float).values,
        }, index=pd.DatetimeIndex(pd.to_datetime(g["d"].values), name="Date"))
        out[ticker] = frame
    log.info("window %s: %d tickers with >=%d bars", market, len(out), min_bars)
    return out


def size_report(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*), count(DISTINCT ticker), min(d), max(d) FROM candles")
        n, tickers, lo, hi = cur.fetchone()
        cur.execute("SELECT pg_size_pretty(pg_total_relation_size('candles'))")
        size = cur.fetchone()[0]
    return {"rows": n, "tickers": tickers, "from": lo, "to": hi, "size": size}


# ---------------------------------------------------------------- maintain

def coverage_check(conn, market: str, bar_date) -> dict:
    """Compare this session's participation against the recent norm.

    COUNTS ONLY TRADED ROWS (v > 0). EODHD finalises a KLSE session by adding
    zero-volume placeholder rows for counters that did not trade — ~140 of
    them, hours after the traded rows land. Comparing TOTAL rows against a
    finalised day therefore reports a phantom 13% collapse every single
    evening, which is exactly the false alarm that would train us to ignore
    the guard PLAN §12 asks for ("a drop >2% turns the scan log loud").
    """
    op = "LIKE" if market == "MY" else "NOT LIKE"   # `.KL` partitions the markets
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT count(*) FILTER (WHERE v > 0) FROM candles
                WHERE ticker {op} %s AND d = %s""",
            ("%.KL", bar_date))
        traded = cur.fetchone()[0] or 0
        cur.execute(
            f"""SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY n) FROM (
                    SELECT d, count(*) FILTER (WHERE v > 0) n FROM candles
                    WHERE ticker {op} %s AND d < %s
                    GROUP BY d ORDER BY d DESC LIMIT 10) s""",
            ("%.KL", bar_date))
        median = cur.fetchone()[0]

    norm = float(median) if median else 0.0
    drop_pct = round((1 - traded / norm) * 100, 1) if norm else 0.0
    report = {"bar_date": bar_date, "traded": traded, "recent_median": norm,
              "drop_pct": drop_pct}
    if norm and drop_pct > 2.0:
        log.warning("COVERAGE DROP %s: %d counters traded vs median %.0f (-%.1f%%) "
                    "— missing counters must be visible, never silently skipped",
                    market, traded, norm, drop_pct)
    else:
        log.info("coverage %s: %d traded (median %.0f)", market, traded, norm)
    return report


def purge_unlisted(conn, market: str, directory: set[str] | None = None) -> int:
    """Drop warehouse tickers the exchange directory no longer carries.

    Catches vendor aliases that freeze: EODHD's HEXTAR/HLIND/ICON/KLCC
    duplicated live numeric listings and stopped updating on 2026-07-17, so
    each was a phantom entry in the RS percentile pool.
    """
    if directory is None:
        directory = set(eodhd_symbols(market)["ticker"])
    if not directory:
        return 0   # never purge on an empty directory — that would wipe the warehouse
    # benchmarks are absent from the common-stock directory by definition;
    # without this the purge quietly deletes the regime instrument
    directory = directory | set(BENCHMARKS.get(market, []))
    op = "LIKE" if market == "MY" else "NOT LIKE"
    with conn.cursor() as cur:
        cur.execute(f"SELECT DISTINCT ticker FROM candles WHERE ticker {op} %s", ("%.KL",))
        have = {r[0] for r in cur.fetchall()}
        gone = sorted(have - directory)
        if gone:
            cur.execute("DELETE FROM candles WHERE ticker = ANY(%s)", (gone,))
            log.info("purged %d unlisted tickers: %s", len(gone), gone[:10])
    conn.commit()
    return len(gone)


def prune(conn, window_days: int = WINDOW_DAYS) -> int:
    """Drop bars older than the window. Calendar days deliberately overshoot
    trading days (x1.45) so the window never lands short of 420 sessions."""
    cutoff = dt.date.today() - dt.timedelta(days=int(window_days * 1.45))
    with conn.cursor() as cur:
        cur.execute("DELETE FROM candles WHERE d < %s", (cutoff,))
        n = cur.rowcount
    conn.commit()
    log.info("pruned %d rows older than %s", n, cutoff)
    return n


def repull(conn, ticker: str, years: int = 2) -> int:
    """Overwrite a ticker's whole window from `history()`.

    Fired on any detected split. Stitching adjusted bars onto un-adjusted ones
    corrupts every MA that spans the split — PLAN §3.3 rates that P0.
    """
    df = eod.history(ticker, years=years)
    rows = [(ticker, idx.date(), float(r.Open), float(r.High), float(r.Low),
             float(r.Close), int(r.Volume)) for idx, r in df.iterrows()]
    with conn.cursor() as cur:
        cur.execute("DELETE FROM candles WHERE ticker = %s", (ticker,))
    n = _upsert(conn, rows)
    log.info("re-pulled %s: %d bars", ticker, n)
    return n


def backfill(conn, market: str, years: int = 2, tickers: list[str] | None = None) -> dict:
    """One-time seed of the window (PLAN §3.3): one `history()` call per
    symbol, ~9k calls total against a 100k/day budget.

    Failures are collected, not raised — a handful of dead symbols in a 6,000
    name directory must not sink a two-hour backfill.
    """
    if tickers is None:
        tickers = list(eodhd_symbols(market)["ticker"])
    # benchmarks are not common stock and would otherwise never be seeded
    for b in BENCHMARKS.get(market, []):
        if b not in tickers:
            tickers.append(b)
    ok = failed = bars = 0
    for i, t in enumerate(tickers, 1):
        try:
            n = repull(conn, t, years=years)
            ok += 1
            bars += n
        except Exception as e:
            failed += 1
            log.warning("backfill %s failed: %s", t, e)
        if i % 250 == 0:
            log.info("backfill %s: %d/%d (%d bars)", market, i, len(tickers), bars)
    report = {"market": market, "requested": len(tickers), "ok": ok,
              "failed": failed, "bars": bars}
    log.info("backfill complete: %s", report)
    return report


def eodhd_symbols(market: str) -> pd.DataFrame:
    """Live (not delisted) common stocks for a market — the warehouse only
    stores tradeable names; delisted history is a backtest-time fetch."""
    df = eod.symbols(eod.EXCHANGES[market], include_delisted=False)
    return df[~df["delisted"]]
