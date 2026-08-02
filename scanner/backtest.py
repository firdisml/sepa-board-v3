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

STRATEGIES beyond the default breakout (see `signals()`/`_signals_one_market`
for each one's exact vectorized rule): ma20_bounce / ma50_bounce (pullback
at a rising MA), episodic_pivot (gap on volume out of neglect — a measured
hazard, not a tradeable setup, PLAN §12.1), pocket_pivot (O'Neil/Kacher
volume thrust inside the base, before the breakout) and buyable_gap_up
(O'Neil/Kacher full gap on 2x+ volume at the pivot). pocket_pivot and
buyable_gap_up are ALREADY DETECTED on the live board (setup.pocket_pivot,
scan.py's inline gap_up check) but not yet part of the active_tactic
rotation traders see — this project's rule is measure before promote (see
episodic_pivot's demotion history), so they exist here to be backtested
first.

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

from . import eodhd_client as eod

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backtest")

# --deep-history parquet cache, keyed on ticker+years — a re-run without it
# re-pays a full history() call per survivor (NEXT.md §1's ~130 API calls).
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", ".cache", "deep_history")

DEFAULTS = dict(risk_pct=1.0, stop_pct=0.08, max_open=8, max_pos_pct=0.25,
                max_hold=40, start_equity=100_000.0, strategy="breakout")

STRATEGIES = ("breakout", "ma20_bounce", "ma50_bounce", "episodic_pivot",
             "pocket_pivot", "buyable_gap_up",
             # REVERSAL tactics. Every tactic above is trend-continuation and
             # gated on the Trend Template, so the board structurally cannot
             # see a stock until AFTER its first leg — 30% off the low and
             # within 25% of the high. These fire before that, which is the
             # point and also the danger: dropping the trend gate is exactly
             # what let episodic_pivot fire 723 times for -0.38R. Each carries
             # its own confirmation requirement instead, and trade COUNT is
             # reported beside expectancy because a tactic that fires
             # constantly is suspect on its face.
             "ma200_reclaim", "undercut_rally",
             # NON-MINERVINI methods, measured on the same basis so they are
             # comparable to everything above. donchian is deliberately the
             # NULL HYPOTHESIS for this whole project: if a naive 20-day
             # channel break matches the SEPA breakout, then VCP detection,
             # contraction counting and the Trend Template are decoration.
             "donchian", "darvas")

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
    """float32 on purpose. A survivorship-free US run is ~9,300 tickers x 1,250
    bars = 11.6M cells PER matrix, and _signals_one_market builds around twenty
    of them plus a cross-sectional rank — float64 OOM-killed the runner with no
    traceback, which reads as a mystery failure. Prices need ~7 significant
    digits; float32 gives that and halves the footprint."""
    return pd.DataFrame({t: df[field] for t, df in data.items()},
                        dtype="float32").sort_index()


def _by_market(data: dict[str, pd.DataFrame]) -> dict[str, dict[str, pd.DataFrame]]:
    out: dict[str, dict[str, pd.DataFrame]] = {}
    for t, df in data.items():
        out.setdefault("MY" if t.endswith(".KL") else "US", {})[t] = df
    return out


def _drop_ghost_days(C: pd.DataFrame, min_coverage: float = 0.2) -> pd.Index:
    """Index of REAL sessions: days where a meaningful fraction of the market
    actually traded.

    The union index otherwise picks up phantom rows: a few dozen junk US
    tickers carry vendor-garbage bars ON MARKET HOLIDAYS (Thanksgiving,
    Christmas, July 4th — 19 such days in one 434-day window, each with <2%
    coverage). Every real ticker is NaN on those rows, and one NaN nulls any
    rolling window that spans it — so ma200 was NaN for ALL 5,594 US tickers
    and the nightly US backtest reported zero trades forever. The same
    poisoning broke the 50MA exit. A real session has >50% coverage; 20% is a
    conservative floor that also behaves in tiny test universes.
    """
    return C.index[C.notna().mean(axis=1) >= min_coverage]


def _signals_one_market(data: dict[str, pd.DataFrame], strategy: str = "breakout",
                        mats: dict | None = None) -> pd.DataFrame:
    """Entry signals for tickers sharing ONE trading calendar.

    `mats` lets the caller pass matrices built ONCE. Without it this function,
    run_backtest and _ma50_matrix each rebuilt their own copies of the same
    frames — three times the peak footprint, which is what OOM-killed the US
    deep-history run (9,263 tickers x 1,250 bars).
    """
    if mats is not None:
        C, H, L, V, O = (mats[k] for k in ("C", "H", "L", "V", "O"))
    else:
        C, H, L, V, O = (_matrix(data, f) for f in ("Close", "High", "Low", "Volume", "Open"))
        real = _drop_ghost_days(C)
        C, H, L, V, O = (M.loc[real] for M in (C, H, L, V, O))

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
        pc, ph = C.shift(1), H.shift(1)
        gap_ok = (O > ph) | (O >= pc * 1.04)
        chg = C / pc - 1
        vol_x = V / vol50
        neglect = (pc / C.shift(64)) <= 1.10
        sig = gap_ok & (chg >= 0.06) & (vol_x >= 3) & (C >= O) & neglect
        return sig.fillna(False)

    if strategy == "donchian":
        # Turtle System 1, unmodified and DELIBERATELY UNGATED: close above the
        # highest high of the prior N days. No trend template, no VCP, no
        # volume test — that is the point. It is the control this project has
        # never run, and the comparison against `breakout` is the measurement
        # that says whether the elaborate entry logic earns its complexity.
        n = 20
        chan = H.rolling(n).max().shift(1)      # prior N days, never today
        return ((C > chan) & (C.shift(1) <= chan.shift(1))).fillna(False)

    if strategy == "darvas":
        # Darvas box: a new high, then a CONSOLIDATION that neither exceeds
        # that high nor breaks its floor, then a break of the box top. Darvas
        # traded this on weekly telegrams from Wall Street with no chart at
        # all, so the rule has to be crude to be faithful.
        #   box_top    the 60-day high, fixed 10 days back so the box is DEFINED
        #              BEFORE the break rather than drawn around it
        #   quiet      price spent those 10 days inside the box: never exceeded
        #              the top, never lost 8% beneath it
        box_top = H.shift(10).rolling(60).max()
        floor = box_top * 0.92
        inside_top = (H <= box_top).rolling(10).sum() >= 9
        inside_floor = (L >= floor).rolling(10).sum() >= 9
        breakout = (C > box_top) & (C.shift(1) <= box_top.shift(1))
        vol_ok = V > 1.4 * vol50
        return (breakout & inside_top & inside_floor & vol_ok).fillna(False)

    if strategy == "ma200_reclaim":
        # The mechanical Stage 1 -> Stage 2 boundary. A stock that has lived
        # BELOW its 200-day MA and closes back above it on real volume is the
        # earliest defensible "the trend may have turned" signal.
        # Confirmations, all required, because buying below the 200MA without
        # them is knife-catching:
        #   - genuinely weak lately: below the 200MA on >=40 of the last 90
        #     sessions. Measured over 90 rather than 30 on purpose — a Stage 1
        #     base STRADDLES the average, so requiring price still pinned
        #     beneath it in the final month excludes the exact shape this is
        #     meant to catch (a test fixture with a textbook base failed the
        #     30-day version).
        #   - the 200MA is not still collapsing; a reclaim into a falling
        #     average is a bounce in a downtrend
        #   - volume expansion on the reclaim day
        #   - closes in the upper half of its range (demand held into the bell)
        ma200_r = C.rolling(200).mean()
        below = (C < ma200_r).rolling(90).sum() >= 40
        reclaim = (C > ma200_r) & (C.shift(1) <= ma200_r.shift(1))
        ma200_ok = ma200_r >= ma200_r.shift(20) * 0.98
        vol_ok = V > 1.4 * vol50
        rng = H - L
        strong = (C - L) >= 0.5 * rng
        return (below & reclaim & ma200_ok & vol_ok & strong).fillna(False)

    if strategy == "undercut_rally":
        # O'Neil/Kacher-Morales shakeout: price undercuts a prior significant
        # low, stopping out everyone who set a stop just beneath it, then
        # closes back above that low. The undercut is the SETUP; the reclaim
        # is the signal.
        #   prior_low  lowest low of the 40 sessions ending 5 days ago, so the
        #              level pre-dates the undercut rather than being defined
        #              by it (defining it from today would be lookahead)
        prior_low = L.shift(5).rolling(40).min()
        undercut = (L <= prior_low).rolling(5).sum() >= 1     # dipped under it
        reclaim = (C > prior_low) & (C.shift(1) <= prior_low)
        vol_ok = V > 1.2 * vol50
        up_day = C > C.shift(1)
        rng = H - L
        strong = (C - L) >= 0.5 * rng
        return (undercut & reclaim & vol_ok & up_day & strong).fillna(False)

    pivot = H.rolling(25).max().shift(1)          # prior 25d high, never today

    if strategy == "pocket_pivot":
        # mirror of indicators.pocket_pivot — an O'Neil/Kacher early entry
        # INSIDE the base: volume beating every down day of the past 10
        # sessions on an up day that closes in the top third of its range,
        # un-extended above the 10-day line. indicators.pocket_pivot() is
        # NOT gated on the live Trend Template (it can fire on the IPO path),
        # but every other tradeable entry here IS — gating this one too, so
        # the backtest never risks capital on a volume thrust in a stock
        # that isn't even in a confirmed uptrend. Revisit if that turns out
        # to be where the edge actually lives.
        ma10 = C.rolling(10).mean()
        prev_close = C.shift(1)
        up_day = C > prev_close
        above_ma50 = C > ma50
        rng = H - L
        top_third = ((C - L) / rng.where(rng > 0)) >= 0.62
        near_ma10 = L <= ma10 * 1.02
        down_vol = V.where(C < prev_close)
        down_vol_max10 = down_vol.shift(1).rolling(10, min_periods=1).max()
        vol_thrust = (V > down_vol_max10) & down_vol_max10.notna()
        sig = up_day & above_ma50 & top_third & near_ma10 & vol_thrust
        return (tt & sig).fillna(False)

    if strategy == "buyable_gap_up":
        # mirror of scan.py's inline BGU check (O'Neil/Kacher): a full gap
        # (today's low above yesterday's high) on 2x+ volume, closing green
        # and at/above the base pivot — institutional urgency, a valid entry
        # rather than "extended". Live code reads the VCP-detected pivot;
        # this uses the same 25d-high proxy as the breakout strategy above
        # (the honest simplification stated in this module's docstring).
        prev_high = H.shift(1)
        vol_ratio = V / vol50
        gap = L > prev_high
        green = C > O
        near_pivot = C >= pivot * 0.98
        sig = gap & (vol_ratio >= 2) & green & near_pivot
        return (tt & sig).fillna(False)

    cross = (C > pivot) & (C.shift(1) <= pivot.shift(1))
    volume_ok = V > 1.4 * vol50

    return (tt & cross & volume_ok).fillna(False)


def signals(data: dict[str, pd.DataFrame], strategy: str = "breakout",
            mats: dict | None = None) -> pd.DataFrame:
    """Boolean entry-signal matrix (dates x tickers), no lookahead.

    Computed PER MARKET on that market's own calendar, then merged. Mixing
    US and Bursa tickers on one union index poisons every rolling window
    with NaN rows (each market has holes on the other's trading days) — a
    200-day MA with any NaN in the window is NaN, which silently evaluated
    the Trend Template to False for every ticker on every day.
    """
    if mats is not None:
        merged = _signals_one_market(None, strategy, mats=mats)
    else:
        parts = [_signals_one_market(sub, strategy) for sub in _by_market(data).values()]
        merged = pd.concat(parts, axis=1).sort_index()
    return merged.fillna(False).astype(bool)


def _ma50_matrix(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """50MA per ticker on its OWN calendar (same NaN-poisoning issue) —
    including the ghost-day filter, or the 50MA EXIT breaks the same way the
    entries did."""
    parts = []
    for sub in _by_market(data).values():
        C = _matrix(sub, "Close")
        parts.append(C.loc[_drop_ghost_days(C)].rolling(50).mean())
    return pd.concat(parts, axis=1).sort_index()



def regime_by_date(bench: dict[str, pd.DataFrame], indices: list[str],
                   dates) -> dict[str, str | None]:
    """{YYYY-MM-DD: 'green'|'yellow'|'red'} using ONLY bars through that date.

    Reuses scan.market_regime verbatim rather than reimplementing it, so the
    regime a backtest attributes to a trade is the same one the live board
    would have shown that evening. market_regime reads .iloc[-1], so slicing
    the benchmark to `<= d` is what makes it as-of — any other slice would be
    lookahead, and a regime split contaminated by the future is worse than no
    split at all.

    Computed only for the dates actually asked for (entry dates), because the
    call is O(bars) each and most sessions never see a fill.
    """
    from . import scan as scanmod
    out: dict[str, str | None] = {}
    for d in dates:
        sliced = {k: df.loc[:d] for k, df in bench.items()}
        # the regime needs a 200-day MA; before that it is not "unknown", it is
        # unmeasurable, and must not be bucketed as anything
        if not sliced or any(len(v) < 200 for v in sliced.values()):
            out[d] = None
            continue
        try:
            out[d] = scanmod.market_regime(sliced, indices).get("light")
        except Exception:
            out[d] = None
    return out


def benchmarks_for(market: str, years: int) -> dict[str, pd.DataFrame]:
    """Index history for the regime, deep enough for the tested window.

    The warehouse only holds ~420 sessions and load_deep_history pulls COMMON
    STOCK, which benchmarks are not — so a 5-year run has no index data unless
    it is fetched explicitly.
    """
    from . import warehouse
    out = {}
    for b in warehouse.BENCHMARKS.get(market, []):
        df = _cached_history(b, years)
        if df is None:
            try:
                df = eod.history(b, years=years)
                _save_cache(b, years, df)
            except Exception as e:
                log.warning("regime benchmark %s unavailable: %s", b, e)
                continue
        out[b] = df
    return out


def run_backtest(data: dict[str, pd.DataFrame], **kw) -> dict:
    """Pure function: OHLCV dict -> {stats, equity, trades, params}."""
    regimes = kw.pop("regime_by_date", None) or {}
    costs = kw.pop("costs", None) or {m: dict(c) for m, c in COSTS.items()}
    p = {**DEFAULTS, **{k: v for k, v in kw.items() if v is not None}}
    p["costs"] = costs
    total_fees = 0.0
    # Build every matrix ONCE and share it: signals(), this block and
    # _ma50_matrix each used to construct their own, so three copies were live
    # at peak — that is what OOM-killed the US deep-history run.
    #
    # ONLY for a single-market universe. signals() splits by market on purpose
    # (mixing calendars puts a NaN in every rolling window and silently zeroes
    # the Trend Template), and one shared matrix set cannot honour that. The
    # production path is single-market — run_per_market always splits first —
    # so the optimisation applies exactly where the scale problem is, and
    # mixed-market callers keep the correct, slower path.
    if len(_by_market(data)) == 1:
        _C = _matrix(data, "Close")
        real = _drop_ghost_days(_C)
        mats = {"C": _C.loc[real]}
        for k, f in (("H", "High"), ("L", "Low"), ("V", "Volume"), ("O", "Open")):
            mats[k] = _matrix(data, f).loc[real]
        del _C
        sig = signals(None, p["strategy"], mats=mats)
        O, H, L, C = (mats[k] for k in ("O", "H", "L", "C"))
        ma50 = C.rolling(50).mean()
        mats.clear()
        O, H, L, C, ma50 = (M.reindex(sig.index) for M in (O, H, L, C, ma50))
    else:
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
            # "Close below the 50MA" means THE TREND BROKE — which presupposes
            # a trend existed. Trend-continuation entries are above the 50MA by
            # construction (the Trend Template requires it), so this arms on the
            # entry bar and behaves exactly as before. REVERSAL entries are
            # often still below it, and the unarmed rule ejected them instantly:
            # undercut_rally exited after 2.3 days, 75% of them via this rule,
            # measuring "enter and immediately exit" rather than the setup.
            if not pos.get("ma50_armed") and pd.notna(ma50.at[d, t]) \
                    and c >= float(ma50.at[d, t]):
                pos["ma50_armed"] = True
            exit_px, reason = None, None
            if l is not None and l <= pos["stop"]:
                # conservative: gap-down opens fill at the open, not the stop
                exit_px = min(pos["stop"], o if o is not None else pos["stop"])
                reason = "stop"
            elif (pos.get("ma50_armed") and pd.notna(ma50.at[d, t])
                  and c < float(ma50.at[d, t])):
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
                    "fees": round(fee, 2), "regime": pos.get("regime"),
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
                m50 = ma50.at[d, t] if t in ma50.columns else None
                _ds = d.strftime("%Y-%m-%d")
                open_pos[t] = {"date": _ds, "entry": fill,
                               "regime": regimes.get(_ds),
                               "stop": stop, "shares": shares, "held": 0,
                               # already in a trend at entry -> armed immediately
                               "ma50_armed": bool(pd.notna(m50) and o >= float(m50))}
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
                        "regime": open_pos[t].get("regime"),
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
                "fees": round(fee, 2), "regime": pos.get("regime"),
            })
            del open_pos[t]

    stats = compute_stats(curve, trades, p["start_equity"])
    stats["total_fees"] = round(total_fees, 2)
    stats["by_regime"] = regime_breakdown(trades)
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



def regime_breakdown(trades: list[dict]) -> dict | None:
    """Expectancy split by the market regime AT ENTRY.

    The blended headline number hides whether an edge is consistent or is two
    different businesses averaged together — say +0.7R in green and -0.4R in
    red. That distinction decides WHEN to trade, which for a ~32% win-rate
    system matters more than finding another entry pattern. It is also the only
    way to check the exposure ladder, whose sizing rules are inherited from
    O'Neil rather than measured here.

    `n` is reported per bucket and must be read: red regimes are rarer, so that
    bucket is thinnest exactly where confidence is most wanted. Under 20 trades
    a bucket is a hypothesis, not a finding — the same bar bootstrap_risk uses.
    """
    if not trades:
        return None
    buckets: dict[str, list[float]] = {}
    for t in trades:
        g = t.get("regime")
        if not g:
            continue          # unmeasurable (pre-200MA), never bucketed as anything
        r = t.get("r")
        if r is not None:
            buckets.setdefault(g, []).append(float(r))
    if not buckets:
        return None
    out = {}
    for g in ("green", "yellow", "red"):
        rs = buckets.get(g) or []
        if not rs:
            continue
        wins = [r for r in rs if r > 0]
        out[g] = {
            "trades": len(rs),
            "expectancy_r": round(sum(rs) / len(rs), 2),
            "win_rate_pct": round(len(wins) / len(rs) * 100, 1),
            "total_r": round(sum(rs), 1),
            "thin": len(rs) < 20,     # say so rather than let the reader assume
        }
    untagged = sum(1 for t in trades if not t.get("regime"))
    if untagged:
        out["untagged"] = untagged
    return out or None


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
        # monthly returns grid + worst drawdown periods (PLAN §9 upgrade B /
        # NEXT.md §4) — jsonb-shaped for the /backtest page
        try:
            mr = qs.stats.monthly_returns(ret, compounded=True)
            stats["monthly_returns"] = {
                str(int(year)): {str(m): (round(float(v) * 100, 2) if pd.notna(v) else None)
                                  for m, v in row.items() if m != "EOY"}
                for year, row in mr.iterrows()
            }
        except Exception:
            stats["monthly_returns"] = None
        try:
            dd_detail = qs.stats.drawdown_details(qs.stats.to_drawdown_series(ret))
            worst = dd_detail.sort_values("max drawdown").head(5)
            stats["drawdown_periods"] = [
                {"start": str(r["start"])[:10], "end": str(r["end"])[:10],
                 "days": int(r["days"]),
                 "depth_pct": round(float(r["max drawdown"]), 2)}
                for _, r in worst.iterrows()
            ]
        except Exception:
            stats["drawdown_periods"] = None
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


def bootstrap_risk(trades: list[dict], start: float, risk_pct: float = 0.01,
                   iterations: int = 10_000, seed: int = 7) -> dict | None:
    """Resample the trade list to show the DISTRIBUTION of outcomes (PLAN §9 B).

    A backtest reports the one path history happened to take. Reshuffling the
    same trades many times answers the question that actually matters before
    risking money: how bad does this get when the same edge deals a worse hand?
    A 40% CAGR that shows a 5th-percentile −45% drawdown is not the same
    strategy as one that bottoms at −12%, even though both "worked".

    Order is what is resampled, not the edge itself — every path here has the
    strategy's real win rate and R distribution; only the sequence changes.
    """
    rs = [float(t["r"]) for t in (trades or []) if t.get("r") is not None]
    if len(rs) < 20:
        # under ~20 closed trades the resampled spread is noise about noise;
        # PLAN §7 makes the same call for the weekly review
        return {"note": f"sample too small ({len(rs)} trades) — need 20+",
                "trades": len(rs)}

    rng = np.random.default_rng(seed)
    n = len(rs)
    draws = rng.choice(np.array(rs), size=(iterations, n), replace=True)

    # equity path per iteration, compounding the same fixed-fractional risk
    growth = 1.0 + draws * risk_pct
    paths = np.cumprod(growth, axis=1)
    finals = paths[:, -1]

    running_max = np.maximum.accumulate(paths, axis=1)
    drawdowns = (paths / running_max - 1.0).min(axis=1)

    years = max(n / 50.0, 1e-9)   # ~50 trades a year is this system's cadence
    cagrs = finals ** (1 / years) - 1

    def pct(arr, p):
        return round(float(np.percentile(arr, p)) * 100, 1)

    return {
        "iterations": iterations, "trades": n, "risk_pct": risk_pct * 100,
        "cagr_p5": pct(cagrs, 5), "cagr_p50": pct(cagrs, 50), "cagr_p95": pct(cagrs, 95),
        "maxdd_p5": pct(drawdowns, 5), "maxdd_p50": pct(drawdowns, 50),
        "maxdd_p95": pct(drawdowns, 95),
        "p_dd_over_25pct": round(float((drawdowns <= -0.25).mean()) * 100, 1),
        "p_dd_over_50pct": round(float((drawdowns <= -0.50).mean()) * 100, 1),
        # risk of ruin proxy: how often the account halves at this risk level
        "p_ruin_half": round(float((finals < 0.5).mean()) * 100, 1),
    }


def _cache_path(ticker: str, years: int) -> str:
    return os.path.join(CACHE_DIR, f"{ticker.replace('/', '_')}_{years}y.parquet")


def _cached_history(ticker: str, years: int) -> pd.DataFrame | None:
    path = _cache_path(ticker, years)
    if not os.path.exists(path):
        return None
    try:
        return pd.read_parquet(path)
    except Exception as e:
        log.warning("deep-history cache unreadable for %s, refetching: %s", ticker, e)
        return None


def _save_cache(ticker: str, years: int, df: pd.DataFrame) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    df.to_parquet(_cache_path(ticker, years))


def load_deep_history(market: str, years: int, min_bars: int = 260,
                      stale_delisted_days: int = 15) -> dict[str, pd.DataFrame]:
    """Full-universe history INCLUDING delisted counters (PLAN §9 upgrade A,
    NEXT.md §1's survivorship fix).

    warehouse.load_window only ever sees currently-listed tickers over a
    rolling ~420-session window: every counter that collapsed and delisted
    during the tested period is invisible, and the window is too short to
    test more than one regime. This pulls per-ticker deep history on demand
    instead — the WHOLE live+delisted directory, deliberately NOT
    liquidity-filtered before fetching:

    scan.py's own docstring is explicit that "RS is ranked on the FULL
    universe BEFORE the liquidity filter, because ranking survivors of a
    pre-filter inflates every rank." A first version of this function
    liquidity-filtered live tickers down to ~130 API calls before ever
    computing signals — which shrinks signals()' cross-sectional RS-rank
    pool from ~972 names to ~230, reproducing exactly that documented
    anti-pattern on a friendlier subset. That run's expectancy IMPROVED over
    the biased baseline instead of getting worse, which is backwards: it is
    the tell that the pool, not the strategy, had changed. Fetching the
    whole directory costs more calls (~1,100 vs ~130 for KLSE) but a ranking
    pool that means what the live scan means by it is the entire point of
    running this backtest at all.

    Delisted counters that turn out to still be trading are excluded and
    logged: EODHD's KLSE directory flags 90+ live blue-chips (AEON, AXIATA,
    BAT, Bursa Malaysia itself, verified 2026-07-25) as "delisted" under a
    stale ALPHABETIC code duplicate of their live numeric listing — the same
    vendor-alias pattern already known for HEXTAR/HLIND/ICON/KLCC, just at
    far greater scale. The numeric-code filter in eod.symbols() catches most
    of these, but any that slip through are caught here mechanically: a
    "delisted" ticker whose fetched history's last bar is recent cannot be a
    real delisting, and keeping it would double-count that company under two
    ticker codes.

    Caching to parquet (keyed on ticker+years) makes a re-run of THIS
    function free; it does not help across GitHub Actions runs, which start
    from a clean checkout each time.
    """
    exchange = eod.EXCHANGES[market]
    directory = eod.symbols(exchange, include_delisted=True)
    live_codes = set(directory.loc[~directory["delisted"], "ticker"])
    delisted_codes = set(directory.loc[directory["delisted"], "ticker"])
    log.info("deep-history %s directory: %d live, %d delisted",
             market, len(live_codes), len(delisted_codes))
    if not delisted_codes:
        log.warning(
            "deep-history %s: zero delisted symbols returned — the survivorship "
            "fix is a no-op this run. Check eod.symbols(include_delisted=True) "
            "and the KLSE numeric-code filter before trusting these results.", market)

    targets = sorted(live_codes | delisted_codes)
    min_dv = float(os.environ.get("DEEP_MIN_DOLLAR_VOL", 100_000))
    log.info("deep-history %s: %d survivors to fetch (%d live + %d delisted, "
             "unfiltered by liquidity — full universe feeds RS ranking)",
             market, len(targets), len(live_codes), len(delisted_codes))

    today = dt.date.today()
    data: dict[str, pd.DataFrame] = {}
    failures: list[tuple[str, bool, str]] = []   # (ticker, was_delisted, reason)
    fetched = cached = failed = mislabeled = illiquid = 0
    for i, t in enumerate(targets, 1):
        df = _cached_history(t, years)
        if df is None:
            try:
                df = eod.history(t, years=years)
                _save_cache(t, years, df)
                fetched += 1
            except eod.DataUnavailable as e:
                failed += 1
                failures.append((t, t in delisted_codes, str(e)))
                continue
        else:
            cached += 1

        if t in delisted_codes and len(df):
            last_bar = df.index[-1].date()
            age = (today - last_bar).days
            if age <= stale_delisted_days:
                mislabeled += 1
                log.warning(
                    "deep-history %s: %s flagged delisted but last traded %s "
                    "(%d days ago) — likely a live-company alias duplicate, excluding",
                    market, t, last_bar, age)
                continue

        if len(df) >= min_bars:
            # Drop what could never have been traded. Deliberately a FLOOR
            # (~$100k median daily value), not the live scan's $5m liquidity
            # gate: the RS percentile pool must stay wide, and pre-filtering it
            # is what inflated ranks the first time. This only removes shells
            # no one could have bought, which on a delisted-inclusive US
            # universe is most of the tail.
            try:
                dv = float((df["Close"] * df["Volume"]).median())
            except Exception:
                dv = 0.0
            if dv >= min_dv:
                data[t] = df
            else:
                illiquid += 1
        if i % 50 == 0:
            log.info("deep-history %s: %d/%d (%d fetched, %d cached, %d failed, %d mislabeled)",
                     market, i, len(targets), fetched, cached, failed, mislabeled)
    log.info("deep-history %s complete: %d tickers with >=%d bars "
             "(%d fetched, %d cached, %d failed, %d mislabeled-delisted, "
             "%d below the $%.0fk tradeability floor)",
             market, len(data), min_bars, fetched, cached, failed, mislabeled,
             illiquid, min_dv / 1000)
    if failures:
        # name every failure so a systematic problem (a code-format change, a
        # plan-tier refusal) is distinguishable from ordinary vendor gaps on
        # thin/ancient names. A failed DELISTED ticker matters most: it is a
        # survivorship hole this function exists to close.
        dead = [(t, r) for t, was_dead, r in failures if was_dead]
        live = [(t, r) for t, was_dead, r in failures if not was_dead]
        if dead:
            log.warning("deep-history %s: %d DELISTED tickers had no history — each "
                        "is a residual survivorship hole: %s",
                        market, len(dead), [t for t, _ in dead])
        if live:
            log.warning("deep-history %s: %d live tickers had no history: %s",
                        market, len(live), [t for t, _ in live])
        reasons = {}
        for _, _, r in failures:
            key = r.split(":")[0][:60]   # collapse per-ticker detail into classes
            reasons[key] = reasons.get(key, 0) + 1
        log.warning("deep-history %s failure reasons: %s", market, reasons)
    return data


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
    ap.add_argument("--universe", choices=["MY", "US"],
                    help="test the WHOLE exchange from the warehouse, not just the "
                         "board (PLAN §9 upgrade A). --from-board only ever tests "
                         "names that are on the board TODAY, which is hindsight: it "
                         "cannot tell you what the rules would have found in 2024.")
    ap.add_argument("--min-bars", type=int, default=260,
                    help="skip warehouse tickers with less history than this")
    ap.add_argument("--deep-history", action="store_true",
                    help="pull per-ticker history incl. DELISTED counters via "
                         "eodhd_client.history(), instead of the warehouse's "
                         "~13-month live-only window. Fixes survivorship bias and "
                         "supports multi-year --years (PLAN §9 upgrade A, "
                         "NEXT.md §1). Requires --universe; results are cached "
                         "to .cache/deep_history/*.parquet.")
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

    # Prices come from the warehouse now. v2 called scan.download_batch (moomoo),
    # which no longer exists — this file would have crashed on line one.
    from . import warehouse

    if a.universe:
        if conn is None:
            conn = dbmod.connect()
        if a.deep_history:
            data = load_deep_history(a.universe, years=a.years, min_bars=a.min_bars)
            log.info("Deep-history backtest: %d %s tickers with >=%d bars (incl. delisted)",
                     len(data), a.universe, a.min_bars)
        else:
            data = warehouse.load_window(conn, a.universe, min_bars=a.min_bars)
            log.info("Full-universe backtest: %d %s tickers with >=%d bars",
                     len(data), a.universe, a.min_bars)
    else:
        if a.deep_history:
            ap.error("--deep-history requires --universe")
        if a.tickers:
            tickers = [t.strip() for t in a.tickers.split(",") if t.strip()]
        elif a.from_board:
            tickers = _board_tickers(conn)
            log.info("Backtesting latest board: %d tickers", len(tickers))
        else:
            ap.error("--tickers, --from-board or --universe required")
        market = _mkt(tickers[0]) if tickers else "MY"
        window = warehouse.load_window(conn or dbmod.connect(), market, min_bars=a.min_bars)
        data = {t: window[t] for t in tickers if t in window}
        missing = [t for t in tickers if t not in window]
        if missing:
            log.info("not in warehouse (%d): %s", len(missing), missing[:8])

    log.info("History loaded for %d tickers", len(data))
    if not data:
        log.error("No data — aborting.")
        return 1

    costs = {m: dict(c) for m, c in COSTS.items()}
    if a.us_slip is not None: costs["US"]["slip_pct"] = a.us_slip
    if a.us_fee is not None: costs["US"]["fee_pct"] = a.us_fee
    if a.my_slip is not None: costs["MY"]["slip_pct"] = a.my_slip
    if a.my_fee is not None: costs["MY"]["fee_pct"] = a.my_fee

    markets = [m.strip().upper() for m in a.markets.split(",") if m.strip()]
    # Regime at entry, per market. Fetched here rather than inside the replay
    # so one download serves every trade, and skipped silently if the index is
    # unavailable — a missing benchmark must not fail a backtest.
    results = {}
    for m in markets:
        sub = {t: df for t, df in data.items() if _mkt(t) == m}
        if not sub:
            continue
        rmap = {}
        try:
            from . import scan as scanmod
            bench = benchmarks_for(m, a.years)
            if bench:
                idx = scanmod.MARKETS.get(m, {}).get("indices") or list(bench)
                entry_days = sorted({d.strftime("%Y-%m-%d")
                                     for d in _matrix(sub, "Close").index})
                rmap = regime_by_date(bench, idx, entry_days)
                log.info("[%s] regime computed for %d sessions", m, len(rmap))
        except Exception as e:
            log.warning("[%s] regime split unavailable: %s", m, e)
        results[m] = run_backtest(sub, risk_pct=a.risk_pct, stop_pct=a.stop_pct,
                                  max_open=a.max_open, max_hold=a.max_hold,
                                  costs=costs, strategy=a.strategy,
                                  regime_by_date=rmap)
    if not results:
        log.error("No tickers matched the requested markets (%s) — aborting.", markets)
        return 1

    base_label = a.label or f"run {dt.date.today().isoformat()}"
    strat_suffix = "" if a.strategy == "breakout" else f" · {a.strategy}"
    for m, result in results.items():
        tickers_m = [t for t in data if _mkt(t) == m]
        # a full-universe run lists ~1,000 tickers; storing them all bloats the
        # row for no benefit, so record the count instead
        if a.universe:
            result["params"]["universe"] = a.universe
            result["params"]["ticker_count"] = len(tickers_m)
            result["params"]["deep_history"] = bool(a.deep_history)
        else:
            result["params"]["tickers"] = sorted(tickers_m)
        result["params"]["years"] = a.years
        result["params"]["market"] = m
        result["stats"]["bootstrap"] = bootstrap_risk(
            result["trades"], start=result["stats"].get("final_equity", 0) or 0,
            risk_pct=(a.risk_pct or 0.01))

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
