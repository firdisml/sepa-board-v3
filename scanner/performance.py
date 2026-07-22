"""The receipts: grade past signals against what actually happened.

Every scan, past swing/watchlist picks (up to 90 days back) are replayed
against real price history: did the breakout trigger? Did it reach 2R before
the stop? Results land in signal_outcomes and feed the /performance page.
"""
from __future__ import annotations

import datetime as dt
import json
import logging

import pandas as pd

log = logging.getLogger(__name__)

TRIGGER_WINDOW = 15   # sessions a signal has to trigger before it expires
EVAL_WINDOW = 60      # sessions a triggered trade is tracked


def grade(df_after: pd.DataFrame, trigger: float, stop: float, target: float) -> dict:
    """Replay one signal on the bars AFTER its scan date (pure function).

    Returns {triggered, outcome: win|loss|open|never_triggered,
             r_multiple, days_to_trigger}. Same-day stop+target = loss
    (conservative). Never-triggered signals carry no R.
    """
    trig_i = None
    for i in range(min(len(df_after), TRIGGER_WINDOW)):
        if float(df_after["High"].iloc[i]) >= trigger:
            trig_i = i
            break
    if trig_i is None:
        return {"triggered": False, "outcome": "never_triggered",
                "r_multiple": None, "days_to_trigger": None}
    risk = trigger - stop
    if risk <= 0:
        return {"triggered": False, "outcome": "never_triggered",
                "r_multiple": None, "days_to_trigger": None}
    tail = df_after.iloc[trig_i:trig_i + EVAL_WINDOW]
    for _, bar in tail.iterrows():
        if float(bar["Low"]) <= stop:
            return {"triggered": True, "outcome": "loss", "r_multiple": -1.0,
                    "days_to_trigger": trig_i}
        if float(bar["High"]) >= target:
            r = round((target - trigger) / risk, 2)
            return {"triggered": True, "outcome": "win", "r_multiple": r,
                    "days_to_trigger": trig_i}
    last = float(tail["Close"].iloc[-1])
    return {"triggered": True, "outcome": "open",
            "r_multiple": round((last - trigger) / risk, 2), "days_to_trigger": trig_i}


def _signals_from_row(run_date, ticker, market, bucket, pivot, stop, t2r, ee,
                      mb=None, m5=None, ep=None) -> list[dict]:
    out = []
    # breakout/early-entry only from base-driven buckets — a position-bucket
    # "pivot" is a stale swing high, not a graded buy point
    if bucket in ("swing", "watchlist") and pivot and stop and t2r:
        out.append({"signal_date": run_date, "ticker": ticker, "market": market,
                    "signal_type": "breakout", "trigger": float(pivot),
                    "stop": float(stop), "target": float(t2r)})
    if bucket in ("swing", "watchlist") and ee and ee.get("trigger") and ee.get("stop"):
        trig, s = float(ee["trigger"]), float(ee["stop"])
        out.append({"signal_date": run_date, "ticker": ticker, "market": market,
                    "signal_type": "early_entry", "trigger": trig, "stop": s,
                    "target": trig + 2 * (trig - s)})  # 2R on its own risk
    if mb and mb.get("trigger") and mb.get("stop"):
        trig, s = float(mb["trigger"]), float(mb["stop"])
        out.append({"signal_date": run_date, "ticker": ticker, "market": market,
                    "signal_type": "ma20_bounce", "trigger": trig, "stop": s,
                    "target": trig + 2 * (trig - s)})
    if m5 and m5.get("trigger") and m5.get("stop"):
        trig, s = float(m5["trigger"]), float(m5["stop"])
        out.append({"signal_date": run_date, "ticker": ticker, "market": market,
                    "signal_type": "ma50_bounce", "trigger": trig, "stop": s,
                    "target": trig + 2 * (trig - s)})
    if ep and ep.get("trigger") and ep.get("stop"):
        trig, s = float(ep["trigger"]), float(ep["stop"])
        out.append({"signal_date": run_date, "ticker": ticker, "market": market,
                    "signal_type": "episodic_pivot", "trigger": trig, "stop": s,
                    "target": trig + 2 * (trig - s)})
    return out


def evaluate_and_store(conn, all_data: dict, run_date: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT r.run_date, c.ticker, c.market, c.bucket, c.pivot, c.stop,
                      c.target_2r, c.setup->'early_entry', c.setup->'ma20_bounce',
                      c.setup->'ma50_bounce', c.setup->'episodic_pivot'
               FROM candidates c JOIN scan_runs r ON r.id = c.run_id
               WHERE r.run_date < %s
                 AND r.run_date >= %s::date - INTERVAL '90 days'""",
            (run_date, run_date),
        )
        rows = cur.fetchall()
        cur.execute(
            """SELECT signal_date, ticker, market, signal_type,
                      trigger_price, stop_price, target_price
               FROM signal_outcomes"""
        )
        prior = cur.fetchall()

    # a pick repeated across consecutive scans is ONE signal: keep the earliest
    # sighting of each (ticker, signal_type, ~trigger) combination.
    # Seed with the rows already on the record — the old DELETE-and-regrade
    # dropped any signal whose ticker later fell out of the downloaded
    # universe (delistings and liquidity casualties, i.e. mostly the losers),
    # which was quiet survivorship bias in the one page that promises none.
    signals: dict[tuple, dict] = {}
    for s_date, ticker, market, s_type, trig, stop, target in prior:
        if trig is None or stop is None or target is None:
            continue
        s = {"signal_date": s_date, "ticker": ticker, "market": market,
             "signal_type": s_type, "trigger": float(trig), "stop": float(stop),
             "target": float(target)}
        key = (ticker, s_type, round(float(trig), 2))
        if key not in signals or s_date < signals[key]["signal_date"]:
            signals[key] = s
    for r_date, ticker, market, bucket, pivot, stop, t2r, ee, mb, m5, ep in rows:
        loads = lambda v: v if isinstance(v, dict) else (json.loads(v) if v else None)
        ee, mb, m5, ep = loads(ee), loads(mb), loads(m5), loads(ep)
        for s in _signals_from_row(r_date, ticker, market, bucket, pivot, stop, t2r,
                                   ee, mb, m5, ep):
            key = (s["ticker"], s["signal_type"], round(s["trigger"], 2))
            if key not in signals or s["signal_date"] < signals[key]["signal_date"]:
                signals[key] = s

    graded, frozen = [], 0
    for s in signals.values():
        df = all_data.get(s["ticker"])
        if df is None:
            frozen += 1  # no data this run — keep the stored grade untouched
            continue
        after = df[df.index.date > s["signal_date"]]
        if after.empty:
            continue
        g = grade(after, s["trigger"], s["stop"], s["target"])
        graded.append({**s, **g})

    with conn.cursor() as cur:
        for g in graded:
            cur.execute(
                """INSERT INTO signal_outcomes
                   (eval_date, signal_date, ticker, market, signal_type, trigger_price,
                    stop_price, target_price, triggered, outcome, r_multiple, days_to_trigger)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (signal_date, ticker, signal_type) DO UPDATE SET
                       eval_date = EXCLUDED.eval_date,
                       market = EXCLUDED.market,
                       trigger_price = EXCLUDED.trigger_price,
                       stop_price = EXCLUDED.stop_price,
                       target_price = EXCLUDED.target_price,
                       triggered = EXCLUDED.triggered,
                       outcome = EXCLUDED.outcome,
                       r_multiple = EXCLUDED.r_multiple,
                       days_to_trigger = EXCLUDED.days_to_trigger""",
                (run_date, g["signal_date"], g["ticker"], g["market"], g["signal_type"],
                 g["trigger"], g["stop"], g["target"], g["triggered"], g["outcome"],
                 g["r_multiple"], g["days_to_trigger"]),
            )
    conn.commit()
    log.info("Signal receipts: %d graded, %d frozen at last grade (no data this run)",
             len(graded), frozen)
