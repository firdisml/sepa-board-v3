"""Weekly AI review of the receipts + backtests — hypotheses, never actions.

Code computes the aggregates (per market, per signal type, per month, plus
the latest per-market backtest stats); Gemini reads them and answers three
questions with numbers: what's working, what isn't, and which hypotheses are
worth testing next — phrased against the tools that exist (the backtest CLI
flags, receipts splits). Nothing here changes any parameter automatically;
the human reads it on /performance and decides.

Run: python -m scanner.reviewer   (env: DATABASE_URL, GEMINI_API_KEY;
optional ANALYST_REVIEW_MODEL). Scheduled Sunday mornings.
"""
from __future__ import annotations

import json
import logging
import os

from . import db
from .analyst import _call, make_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("reviewer")

REVIEW_MODELS = [m for m in (os.environ.get("ANALYST_REVIEW_MODEL"),
                             "gemini-3.5-flash", "gemini-3-flash-preview",
                             "gemini-3.1-flash-lite") if m]
# includes Gemini's thinking tokens — 1400 truncated the reply mid-JSON
REVIEW_MAX_TOKENS = 6000
KEEP_REVIEWS = 12


def _aggregates(conn) -> dict:
    out: dict = {}
    with conn.cursor() as cur:
        cur.execute("""
            SELECT market, signal_type,
                   count(*)                                                     AS signals,
                   count(*) FILTER (WHERE triggered)                            AS triggered,
                   count(*) FILTER (WHERE outcome IN ('win','loss'))            AS closed,
                   count(*) FILTER (WHERE outcome = 'win')                      AS wins,
                   round(avg(r_multiple) FILTER (WHERE outcome IN ('win','loss'))::numeric, 2) AS expectancy_r
            FROM signal_outcomes GROUP BY market, signal_type
            ORDER BY market, signal_type""")
        cols = [d.name for d in cur.description]
        out["receipts_by_market_and_type"] = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.execute("""
            SELECT to_char(date_trunc('month', signal_date), 'YYYY-MM') AS month,
                   count(*) FILTER (WHERE outcome IN ('win','loss'))    AS closed,
                   round(avg(r_multiple) FILTER (WHERE outcome IN ('win','loss'))::numeric, 2) AS expectancy_r
            FROM signal_outcomes GROUP BY 1 ORDER BY 1 DESC LIMIT 6""")
        cols = [d.name for d in cur.description]
        out["receipts_by_month"] = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.execute("""
            SELECT DISTINCT ON (params->>'market') params->>'market' AS market, label, stats
            FROM backtests WHERE params->>'market' IS NOT NULL
            ORDER BY params->>'market', created_at DESC""")
        out["latest_backtests"] = [{"market": r[0], "label": r[1], "stats": r[2]}
                                   for r in cur.fetchall()]
    return out


def _payload(agg: dict) -> dict:
    return {
        "task": (
            "Weekly performance review of this screener. Answer with numbers from "
            "the aggregates only. working[]/not_working[]: each line names the "
            "split (market/signal type/month), its expectancy and sample size. "
            "hypotheses[]: 1-4 items max, each with the evidence (numbers) and "
            "how_to_test phrased against the existing tools — the backtest CLI "
            "(--markets, --stop-pct, --max-hold, --risk-pct) or watching a "
            "receipts split. NEVER recommend applying a change directly; these "
            "are experiments for the human to run. If a split has under 20 closed "
            "signals, say the sample is too small instead of drawing conclusions."
        ),
        "output_schema": {
            "summary": "2-3 sentences with numbers",
            "working": ["split: numbers"],
            "not_working": ["split: numbers"],
            "hypotheses": [{"hypothesis": "string", "evidence": "numbers",
                            "how_to_test": "backtest CLI flags or receipts split to watch"}],
        },
        "data": agg,
    }


def main() -> int:
    if not os.environ.get("GEMINI_API_KEY"):
        log.error("GEMINI_API_KEY not set")
        return 1
    client = make_client()
    conn = db.connect()
    db.apply_migrations(conn)
    agg = _aggregates(conn)
    if not agg["receipts_by_market_and_type"] and not agg["latest_backtests"]:
        log.info("No receipts or backtests yet — nothing to review.")
        return 0

    result = _call(client, REVIEW_MODELS, _payload(agg), REVIEW_MAX_TOKENS)
    if not result or not result.get("summary"):
        log.error("Review call produced no usable output.")
        return 1

    review = {
        "summary": str(result.get("summary", ""))[:600],
        "working": [str(x)[:300] for x in (result.get("working") or [])][:6],
        "not_working": [str(x)[:300] for x in (result.get("not_working") or [])][:6],
        "hypotheses": [
            {"hypothesis": str(h.get("hypothesis", ""))[:300],
             "evidence": str(h.get("evidence", ""))[:300],
             "how_to_test": str(h.get("how_to_test", ""))[:300]}
            for h in (result.get("hypotheses") or []) if isinstance(h, dict)
        ][:4],
    }
    with conn.cursor() as cur:
        cur.execute("INSERT INTO ai_reviews (review) VALUES (%s)", (json.dumps(review),))
        cur.execute("""DELETE FROM ai_reviews
                       WHERE id NOT IN (SELECT id FROM ai_reviews
                                        ORDER BY created_at DESC, id DESC LIMIT %s)""",
                    (KEEP_REVIEWS,))
    conn.commit()
    conn.close()
    log.info("Weekly review stored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
