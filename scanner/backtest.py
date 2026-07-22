"""On-demand backtest of the board's breakout strategy — the receipts, in bulk.

Replays the screener's entry logic bar-by-bar over real history with NO
lookahead: every input to a day-t decision uses data through t (signals) and
fills at t+1's open. Sizing, caps, and stops are the board's own rules.

Strategy (v1, deliberately simple so every trade is auditable):
  ENTRY (signal day t, filled next open):
    - Trend Template passes that day (vectorized, incl. cross-sectional RS
      rank >= 70 computed WITHIN the tested universe)
    - Close crosses above the pivot (prior 25-day high, shifted — never
      includes the breakout day itself)
    - Volume >= 1.4x the prior 50-day average
  EXIT (whichever hits first):
    - intraday stop: entry * (1 - stop_pct)
    - close below the 50-day MA
    - max_hold sessions elapsed (fill next open)
  SIZING: risk_pct of CURRENT equity / per-share risk; position capped at
    25% of equity; at most max_open concurrent positions.

Costs ARE modeled (v1.3): per-side slippage + fees, US and Bursa separately.
Markets are backtested SEPARATELY (v1.4): US and Bursa each get their own
run — own equity curve, own stats, own row in the backtests table — because
the two markets behave too differently for one blended curve to mean much.
Honest limitation, stated up front: v1 gates entries on the Trend Template +
breakout + volume, NOT on VCP quality — per-day VCP detection over a full
history is expensive and is the next iteration. Expect the live board's picks
to be a tighter subset of these trades.

Run:
  python -m scanner.backtest --tickers NVDA,PLTR,CRWD --years 3
  python -m scanner.backtest --from-board --years 3        # latest candidates
  python -m scanner.backtest --from-board --markets MY     # Bursa only
  python -m scanner.backtest --tickers ... --no-db         # print only
Env: DATABASE_URL (unless --no-db).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import os

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backtest")

DEFAULTS = dict(risk_pct=1.0, stop_pct=0.08, max_open=8, max_pos_pct=0.25,
                max_hold=40, start_equity=100_000.0, strategy="breakout")

STRATEGIES = ("breakout", "ma20_bounce", "ma50_bounce", "episodic_pivot")

# Per-side transaction costs, applied to EVERY fill (v1.3 "Slippage"):
#   slip_pct — price impact: buys fill above the reference price, sells below.
#     US momentum names: ~0.10%/side. Bursa: wider ticks and thinner books on
#     small caps — 0.30%/side is a realistic default, not pessimism.
#   fee_pct — charged on notional per side.
#     US (moomoo-class broker): ~0.05%. MY: brokerage ~0.05% + clearing 0.03%
#     + stamp duty 0.10% ≈ 0.18%/side.
# Override per run: --us-slip/--us-fee/--my-slip/--my-fee (decimals, per side).
COSTS = {
    "US": {"slip_pct": 0.0010, "fee_pct": 0.0005},
    "MY": {"slip_pct": 0.0030, "fee_pct": 0.0018},
}


def _mkt(t: str) -> str:
    return "MY" if t.endswith(".KL") else "US"


def _matrix(data: dict[str, pd.DataFrame], field: str) -> pd.DataFrame:
    return pd.DataFrame({t: df[field] for t, df in data.items()}).sort_index()


def _by_market(data: dict[str, pd.DataFrame]) -> dict[str, dict[str, pd.DataFrame]]:
    out: dict[str, dict[str, pd.DataFrame]] = {}
    for t, df in data.items():
        out.setdefault("MY" if t.endswith(".KL") else "US", {})[t] = df
    return out


def _signals_one_market(data: dict[str, pd.DataFrame], strategy: str = "breakout") -> pd.DataFrame:
    """Entry signals for tickers sharing ONE trading calendar."""
    C, H, L, V = (_matrix(data, f) for f in ("Close", "High", "Low", "Volume"))

    ma50 = C.rolling(50).mean()
    ma150 = C.rolling(150).mean()
    ma200 = C.rolling(200).mean()
    ma200_prev = ma200.shift(22)
    hi52 = H.rolling(252, min_periods=126).max()
    lo52 = L.rolling(252, min_periods=126).min()

    # cross-sectional RS within the tested universe (renormalized weights,
    # same scheme as indicators.rs_raw)
    mom = pd.DataFrame(0.0, index=C.index, columns=C.columns)
    wsum = pd.DataFrame(0.0, index=C.index, columns=C.columns)
    for w, back in [(0.4, 63), (0.2, 126), (0.2, 189), (0.2, 252)]:
        r = C / C.shift(back)
        mom = mom.add(r.fillna(0.0) * w)
        wsum = wsum.add(r.notna().astype(float) * w)
    mom = mom.where(wsum >= 0.4) / wsum          # need at least the 3-month leg
    rs_rank = mom.rank(axis=1, pct=True) * 99

    tt = (
        (C > ma150) & (C > ma200)
        & (ma150 > ma200)
        & (ma200 > ma200_prev)
        & (ma50 > ma150) & (ma50 > ma200)
        & (C > ma50)
        & (C >= lo52 * 1.30)
        & (C >= hi52 * 0.75)
        & (rs_rank >= 70)
    )

    vol50 = V.rolling(50).mean().shift(1)

    def _bounce(window: int, rising_lag: int, tag_days: int) -> pd.DataFrame:
        # mirror of indicators._ma_bounce, vectorized over the whole history
        ma = C.rolling(window).mean()
        rising = ma > ma.shift(rising_lag)
        respects = (C > ma).rolling(40).sum() >= 30
        tag_light = (((L <= ma * 1.005) & (V < vol50))
                     .astype(float).shift(1).rolling(tag_days).max() > 0)
        up_day = C > C.shift(1)
        reclaim = C > ma
        strong_close = (C - L) >= 0.5 * (H - L)
        return tt & rising & respects & tag_light & up_day & reclaim & strong_close

    if strategy == "ma20_bounce":
        return _bounce(20, 5, 4).fillna(False)
    if strategy == "ma50_bounce":
        return _bounce(50, 10, 5).fillna(False)
    if strategy == "episodic_pivot":
        # mirror of indicators.episodic_pivot — deliberately NOT trend-gated:
        # EPs fire out of neglect, before the Trend Template can pass
        O = _matrix(data, "Open")
        pc, ph = C.shift(1), H.shift(1)
        gap_ok = (O > ph) | (O >= pc * 1.04)
        chg = C / pc - 1
        vol_x = V / vol50
        neglect = (pc / C.shift(64)) <= 1.10
        sig = gap_ok & (chg >= 0.06) & (vol_x >= 3) & (C >= O) & neglect
        return sig.fillna(False)

    pivot = H.rolling(25).max().shift(1)          # prior 25d high, never today
    cross = (C > pivot) & (C.shift(1) <= pivot.shift(1))
    volume_ok = V > 1.4 * vol50

    return (tt & cross & volume_ok).fillna(False)


def signals(data: dict[str, pd.DataFrame], strategy: str = "breakout") -> pd.DataFrame:
    """Boolean entry-signal matrix (dates x tickers), no lookahead.

    Computed PER MARKET on that market's own calendar, then merged. Mixing
    US and Bursa tickers on one union index poisons every rolling window
    with NaN rows (each market has holes on the other's trading days) — a
    200-day MA with any NaN in the window is NaN, which silently evaluated
    the Trend Template to False for every ticker on every day.
    """
    parts = [_signals_one_market(sub, strategy) for sub in _by_market(data).values()]
    merged = pd.concat(parts, axis=1).sort_index()
    return merged.fillna(False).astype(bool)


def _ma50_matrix(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """50MA per ticker on its OWN calendar (same NaN-poisoning issue)."""
    parts = [
        _matrix(sub, "Close").rolling(50).mean()
        for sub in _by_market(data).values()
    ]
    return pd.concat(parts, axis=1).sort_index()


def run_backtest(data: dict[str, pd.DataFrame], **kw) -> dict:
    """Pure function: OHLCV dict -> {stats, equity, trades, params}."""
    costs = kw.pop("costs", None) or {m: dict(c) for m, c in COSTS.items()}
    p = {**DEFAULTS, **{k: v for k, v in kw.items() if v is not None}}
    p["costs"] = costs
    total_fees = 0.0
    sig = signals(data, p["strategy"])
    O, H, L, C = (_matrix(data, f) for f in ("Open", "High", "Low", "Close"))
    ma50 = _ma50_matrix(data).reindex(sig.index)
    O, H, L, C = (M.reindex(sig.index) for M in (O, H, L, C))
    dates = sig.index
    if not sig.values.any():
        log.warning("Zero entry signals over the whole period — check universe/history length.")

    equity = p["start_equity"]
    cash = equity
    open_pos: dict[str, dict] = {}
    trades: list[dict] = []
    curve: list[dict] = []

    def px(M, t, d):
        v = M.at[d, t]
        return float(v) if pd.notna(v) else None

    for di in range(1, len(dates)):
        d, prev = dates[di], dates[di - 1]

        # ---- exits first (on today's bar, decided from yesterday's info) ----
        for t in list(open_pos):
            pos = open_pos[t]
            o, h, l, c = (px(M, t, d) for M in (O, H, L, C))
            if c is None:
                continue
            pos["held"] += 1
            exit_px, reason = None, None
            if l is not None and l <= pos["stop"]:
                # conservative: gap-down opens fill at the open, not the stop
                exit_px = min(pos["stop"], o if o is not None else pos["stop"])
                reason = "stop"
            elif pd.notna(ma50.at[d, t]) and c < float(ma50.at[d, t]):
                exit_px, reason = c, "ma50_break"
            elif pos["held"] >= p["max_hold"]:
                exit_px, reason = c, "time"
            if exit_px is not None:
                cm = costs[_mkt(t)]
                fill = exit_px * (1 - cm["slip_pct"])          # sells fill below
                fee = pos["shares"] * fill * cm["fee_pct"]
                total_fees += fee
                cash += pos["shares"] * fill - fee
                r = (fill - pos["entry"]) / (pos["entry"] - pos["stop"])
                trades.append({
                    "ticker": t, "entry_date": pos["date"], "exit_date": d.strftime("%Y-%m-%d"),
                    "entry": round(pos["entry"], 4), "exit": round(fill, 4),
                    "stop": round(pos["stop"], 4), "shares": pos["shares"],
                    "r": round(r, 2), "held": pos["held"], "reason": reason,
                    "fees": round(fee, 2),
                })
                del open_pos[t]

        # ---- entries: yesterday's signals fill at today's open ----
        if len(open_pos) < p["max_open"]:
            for t in sig.columns[sig.loc[prev].values]:
                if t in open_pos or len(open_pos) >= p["max_open"]:
                    continue
                o = px(O, t, d)
                if o is None or o <= 0:
                    continue
                cm = costs[_mkt(t)]
                fill = o * (1 + cm["slip_pct"])                # buys fill above
                stop = fill * (1 - p["stop_pct"])
                rps = fill - stop
                shares = math.floor(equity * (p["risk_pct"] / 100) / rps)
                shares = min(shares, math.floor(equity * p["max_pos_pct"] / fill))
                fee = shares * fill * cm["fee_pct"]
                cost = shares * fill + fee
                if shares <= 0 or cost > cash:
                    continue
                total_fees += fee
                cash -= cost
                open_pos[t] = {"date": d.strftime("%Y-%m-%d"), "entry": fill,
                               "stop": stop, "shares": shares, "held": 0}
                # same-day stop: a breakout that reverses through its stop on
                # the entry bar exits TODAY — leaving it for tomorrow's exit
                # loop quietly flattered every whipsaw entry by one day
                l = px(L, t, d)
                if l is not None and l <= stop:
                    cm = costs[_mkt(t)]
                    out = stop * (1 - cm["slip_pct"])
                    fee = shares * out * cm["fee_pct"]
                    total_fees += fee
                    cash += shares * out - fee
                    trades.append({
                        "ticker": t, "entry_date": open_pos[t]["date"],
                        "exit_date": d.strftime("%Y-%m-%d"),
                        "entry": round(fill, 4), "exit": round(out, 4),
                        "stop": round(stop, 4), "shares": shares,
                        "r": round((out - fill) / (fill - stop), 2), "held": 0,
                        "reason": "stop", "fees": round(fee, 2),
                    })
                    del open_pos[t]

        # ---- mark to market ----
        mtm = cash + sum(pos["shares"] * (px(C, t, d) or pos["entry"])
                         for t, pos in open_pos.items())
        equity = mtm
        curve.append({"t": d.strftime("%Y-%m-%d"), "eq": round(mtm, 2)})

    # ---- liquidate whatever is still open at the last bar ----
    # dropping open positions silently omits every trade still running on the
    # final day; close them at the last available price, clearly tagged
    if open_pos:
        d = dates[-1]
        for t, pos in list(open_pos.items()):
            cm = costs[_mkt(t)]
            c = (px(C, t, d) or pos["entry"]) * (1 - cm["slip_pct"])
            fee = pos["shares"] * c * cm["fee_pct"]
            total_fees += fee
            cash += pos["shares"] * c - fee
            r = (c - pos["entry"]) / (pos["entry"] - pos["stop"])
            trades.append({
                "ticker": t, "entry_date": pos["date"], "exit_date": d.strftime("%Y-%m-%d"),
                "entry": round(pos["entry"], 4), "exit": round(c, 4),
                "stop": round(pos["stop"], 4), "shares": pos["shares"],
                "r": round(r, 2), "held": pos["held"], "reason": "end_of_data",
                "fees": round(fee, 2),
            })
            del open_pos[t]

    stats = compute_stats(curve, trades, p["start_equity"])
    stats["total_fees"] = round(total_fees, 2)
    return {"params": p, "stats": stats, "equity": curve, "trades": trades}


def run_per_market(data: dict[str, pd.DataFrame], markets=("US", "MY"), **kw) -> dict[str, dict]:
    """One INDEPENDENT backtest per market — own equity curve, own stats.

    US and Bursa are different animals (trend persistence, liquidity, tick
    sizes, costs); blending them into one portfolio let the market with more
    tickers dominate the curve and hid how each actually behaves. Each market
    gets the full start_equity and its own max_open/caps.
    """
    by = _by_market(data)
    return {m: run_backtest(by[m], **kw) for m in markets if by.get(m)}


def compute_stats(curve: list[dict], trades: list[dict], start: float) -> dict:
    if not curve:
        return {"note": "no bars"}
    eq = pd.Series([c["eq"] for c in curve],
                   index=pd.to_datetime([c["t"] for c in curve]))
    ret = eq.pct_change().dropna()
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = (eq.iloc[-1] / start) ** (1 / years) - 1
    dd = (eq / eq.cummax() - 1).min()
    closed = [t for t in trades]
    wins = [t["r"] for t in closed if t["r"] > 0]
    losses = [t["r"] for t in closed if t["r"] <= 0]
    stats = {
        "final_equity": round(float(eq.iloc[-1]), 2),
        "cagr_pct": round(cagr * 100, 2),
        "max_drawdown_pct": round(float(dd) * 100, 2),
        "trades": len(closed),
        "win_rate_pct": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "expectancy_r": round(float(np.mean([t["r"] for t in closed])), 2) if closed else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if wins and losses else None,
        "avg_win_r": round(float(np.mean(wins)), 2) if wins else None,
        "avg_loss_r": round(float(np.mean(losses)), 2) if losses else None,
        "avg_hold_days": round(float(np.mean([t["held"] for t in closed])), 1) if closed else None,
    }
    # quantstats when available (richer risk metrics); manual fallbacks otherwise
    try:
        import quantstats as qs
        stats["sharpe"] = round(float(qs.stats.sharpe(ret)), 2)
        stats["sortino"] = round(float(qs.stats.sortino(ret)), 2)
        stats["volatility_pct"] = round(float(qs.stats.volatility(ret)) * 100, 2)
        stats["metrics_source"] = "quantstats"
    except Exception:
        sd = float(ret.std())
        stats["sharpe"] = round(float(ret.mean()) / sd * math.sqrt(252), 2) if sd > 0 else None
        down = ret[ret < 0]
        dsd = float(down.std())
        stats["sortino"] = round(float(ret.mean()) / dsd * math.sqrt(252), 2) if len(down) and dsd > 0 else None
        stats["volatility_pct"] = round(sd * math.sqrt(252) * 100, 2) if sd > 0 else None
        stats["metrics_source"] = "builtin"
    # Postgres jsonb rejects NaN/Infinity tokens — a degenerate run (zero
    # trades -> zero-variance returns) made quantstats emit NaN Sharpe and
    # crashed the save. None everywhere a number is not finite.
    for k, v in list(stats.items()):
        if isinstance(v, float) and not math.isfinite(v):
            stats[k] = None
    return stats


def _board_tickers(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT c.ticker FROM candidates c
                       JOIN scan_runs r ON r.id = c.run_id
                       WHERE r.run_date = (SELECT max(run_date) FROM scan_runs)""")
        return [r[0] for r in cur.fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", help="comma-separated, e.g. NVDA,PLTR or 0138.KL")
    ap.add_argument("--from-board", action="store_true", help="use latest scan's candidates")
    ap.add_argument("--markets", default="US,MY",
                    help="comma-separated markets to test; each runs as its OWN "
                         "backtest with its own equity curve (default US,MY)")
    ap.add_argument("--strategy", default="breakout", choices=list(STRATEGIES),
                    help="entry strategy: pivot breakout (default), ma20_bounce / "
                         "ma50_bounce (pullback at the rising MA), or "
                         "episodic_pivot (gap on volume out of neglect)")
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--risk-pct", type=float)
    ap.add_argument("--stop-pct", type=float, help="fraction, e.g. 0.08")
    ap.add_argument("--max-open", type=int)
    ap.add_argument("--max-hold", type=int)
    ap.add_argument("--us-slip", type=float, help="US slippage per side, decimal (default 0.001)")
    ap.add_argument("--us-fee", type=float, help="US fee per side on notional (default 0.0005)")
    ap.add_argument("--my-slip", type=float, help="Bursa slippage per side (default 0.003)")
    ap.add_argument("--my-fee", type=float, help="Bursa fees per side incl. stamp duty (default 0.0018)")
    ap.add_argument("--label", default=None)
    ap.add_argument("--no-db", action="store_true")
    a = ap.parse_args()

    from . import scan as scanmod
    from . import db as dbmod

    conn = None
    if a.from_board or not a.no_db:
        conn = dbmod.connect()
        dbmod.apply_migrations(conn)

    if a.tickers:
        tickers = [t.strip() for t in a.tickers.split(",") if t.strip()]
    elif a.from_board:
        tickers = _board_tickers(conn)
        log.info("Backtesting latest board: %d tickers", len(tickers))
    else:
        ap.error("--tickers or --from-board required")

    data = scanmod.download_batch(tickers, period=f"{a.years}y")
    log.info("History downloaded for %d/%d tickers", len(data), len(tickers))
    if not data:
        log.error("No data — aborting.")
        return 1

    costs = {m: dict(c) for m, c in COSTS.items()}
    if a.us_slip is not None: costs["US"]["slip_pct"] = a.us_slip
    if a.us_fee is not None: costs["US"]["fee_pct"] = a.us_fee
    if a.my_slip is not None: costs["MY"]["slip_pct"] = a.my_slip
    if a.my_fee is not None: costs["MY"]["fee_pct"] = a.my_fee

    markets = [m.strip().upper() for m in a.markets.split(",") if m.strip()]
    results = run_per_market(data, markets=markets, risk_pct=a.risk_pct,
                             stop_pct=a.stop_pct, max_open=a.max_open,
                             max_hold=a.max_hold, costs=costs, strategy=a.strategy)
    if not results:
        log.error("No tickers matched the requested markets (%s) — aborting.", markets)
        return 1

    base_label = a.label or f"run {dt.date.today().isoformat()}"
    strat_suffix = "" if a.strategy == "breakout" else f" · {a.strategy}"
    for m, result in results.items():
        tickers_m = [t for t in data if _mkt(t) == m]
        result["params"]["tickers"] = sorted(tickers_m)
        result["params"]["years"] = a.years
        result["params"]["market"] = m

    print(json.dumps({m: r["stats"] for m, r in results.items()}, indent=2))
    if conn and not a.no_db:
        with conn.cursor() as cur:
            for m, result in results.items():
                cur.execute(
                    """INSERT INTO backtests (label, params, stats, equity, trades)
                       VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                    (f"{base_label} [{m}]{strat_suffix}",
                     json.dumps(result["params"]), json.dumps(result["stats"]),
                     json.dumps(result["equity"]), json.dumps(result["trades"])),
                )
                log.info("Saved %s backtest id=%s", m, cur.fetchone()[0])
            # nightly auto-runs (chained to the scan) would add 2 heavy jsonb
            # rows per day forever — keep the latest 30 (~3 weeks of US+MY);
            # manually labeled runs are never pruned
            cur.execute(
                """DELETE FROM backtests
                   WHERE label LIKE 'nightly %'
                     AND id NOT IN (SELECT id FROM backtests
                                    WHERE label LIKE 'nightly %'
                                    ORDER BY created_at DESC, id DESC LIMIT 30)"""
            )
        conn.commit()
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
