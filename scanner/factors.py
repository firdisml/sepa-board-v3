"""Factor validation (PLAN §9 upgrade C / NEXT.md §3) — do the board's scores
actually predict anything?

Weekly question, not nightly: joins every historical `candidates` row to the
candle warehouse and measures the 20-session forward return per score bucket.
If a score's buckets do not slope monotonically, /performance says so — a
quality score that does not predict is WORSE than no score, because it looks
like information.

Factors: quality (0-100), rs_rank (0-99), fundamentals->>'grade' (A-E),
setup->anticipation->>'score'. Numeric factors bucket into quintiles (deciles
need more history than a young board has; the bucket column stays 'D1'..'D5'
and can widen later), grade stays categorical.

Run: python -m scanner.factors   (env: DATABASE_URL)
"""
from __future__ import annotations

import logging

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("factors")

FWD_SESSIONS = 20
MIN_ROWS_PER_FACTOR = 30    # below this the buckets are noise about noise
N_BUCKETS = 5


def _candidate_rows(conn, market: str) -> pd.DataFrame:
    like = "%.KL" if market == "MY" else None
    with conn.cursor() as cur:
        cur.execute(
            """SELECT r.run_date, c.ticker, c.quality, c.rs_rank,
                      c.fundamentals->>'grade' AS grade,
                      (c.setup->'anticipation'->>'score')::float AS anticipation
               FROM candidates c JOIN scan_runs r ON r.id = c.run_id
               WHERE (%s::text IS NULL) OR c.ticker LIKE %s""",
            (like, like))
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=["run_date", "ticker", "quality",
                                       "rs_rank", "grade", "anticipation"])


def _closes(conn, tickers: list[str]) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute("SELECT ticker, d, c FROM candles WHERE ticker = ANY(%s) ORDER BY ticker, d",
                    (tickers,))
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["ticker", "d", "close"])
    return df


def forward_returns(cand: pd.DataFrame, closes: pd.DataFrame) -> pd.DataFrame:
    """fwd20 %, per candidate row. NaN when the warehouse lacks the entry bar
    or 20 further sessions (recent rows age into measurability later — they
    are skipped this week, not graded early)."""
    out = []
    by_ticker = {t: g.reset_index(drop=True) for t, g in closes.groupby("ticker")}
    for _, row in cand.iterrows():
        g = by_ticker.get(row["ticker"])
        if g is None:
            out.append(None)
            continue
        pos = g.index[g["d"] >= row["run_date"]]
        if len(pos) == 0 or pos[0] + FWD_SESSIONS >= len(g):
            out.append(None)
            continue
        i = pos[0]
        c0, c1 = float(g["close"].iloc[i]), float(g["close"].iloc[i + FWD_SESSIONS])
        out.append(round((c1 / c0 - 1) * 100, 2) if c0 > 0 else None)
    cand = cand.copy()
    cand["fwd20"] = out
    return cand.dropna(subset=["fwd20"])


def _bucketize(vals: pd.Series) -> pd.Series:
    # rank-based buckets: robust to ties/clumping (rs_rank clusters at 70+)
    return pd.qcut(vals.rank(method="first"), N_BUCKETS,
                   labels=[f"D{i+1}" for i in range(N_BUCKETS)])


def compute_factors(conn, market: str = "MY") -> list[dict]:
    cand = _candidate_rows(conn, market)
    if cand.empty:
        log.warning("no candidate history — nothing to validate")
        return []
    closes = _closes(conn, sorted(cand["ticker"].unique()))
    meas = forward_returns(cand, closes)
    log.info("factor validation %s: %d candidate rows, %d measurable (20d elapsed)",
             market, len(cand), len(meas))

    results = []
    factors = {
        "quality": meas["quality"].astype(float),
        "rs_rank": meas["rs_rank"].astype(float),
        "anticipation": meas["anticipation"].astype(float),
        "grade": meas["grade"],
    }
    for name, vals in factors.items():
        sub = meas[vals.notna()].copy()
        v = vals.dropna()
        if len(sub) < MIN_ROWS_PER_FACTOR:
            log.info("factor %s: only %d rows — skipped (need %d)",
                     name, len(sub), MIN_ROWS_PER_FACTOR)
            continue
        if name == "grade":
            sub["bucket"] = v.astype(str)
            order = ["E", "D", "C", "B", "A"]          # worst -> best
        else:
            try:
                sub["bucket"] = _bucketize(v)
            except ValueError:
                log.info("factor %s: too little spread to bucket — skipped", name)
                continue
            order = [f"D{i+1}" for i in range(N_BUCKETS)]

        g = sub.groupby("bucket", observed=True)["fwd20"]
        stats = pd.DataFrame({
            "n": g.size(), "mean": g.mean().round(2), "median": g.median().round(2),
            "win": (g.apply(lambda s: (s > 0).mean() * 100)).round(1),
        }).reindex([b for b in order if b in set(sub["bucket"].astype(str))])

        means = stats["mean"].dropna()
        monotone = bool(len(means) >= 3 and means.is_monotonic_increasing)
        for bucket, r in stats.iterrows():
            if pd.isna(r["n"]):
                continue
            results.append({
                "market": market, "factor": name, "bucket": str(bucket),
                "n": int(r["n"]), "fwd20_mean": float(r["mean"]),
                "fwd20_median": float(r["median"]), "win_rate": float(r["win"]),
                "monotone": monotone,
            })
        log.info("factor %s: %s slope %s", name,
                 list(means.values), "MONOTONE" if monotone else "NOT monotone")
    return results


def save(conn, rows: list[dict]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        for r in rows:
            cur.execute(
                """INSERT INTO factor_deciles
                   (market, factor, bucket, n, fwd20_mean, fwd20_median, win_rate, monotone)
                   VALUES (%(market)s, %(factor)s, %(bucket)s, %(n)s,
                           %(fwd20_mean)s, %(fwd20_median)s, %(win_rate)s, %(monotone)s)""",
                r)
    conn.commit()
    log.info("saved %d factor_deciles rows", len(rows))


def main() -> int:
    from . import db as dbmod
    conn = dbmod.connect()
    dbmod.apply_migrations(conn)
    rows = compute_factors(conn, "MY")
    save(conn, rows)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
