"""Weekly-timeframe view — Weinstein's Stage Analysis line, from daily bars.

Costs nothing: weekly is a resample of data already held, no new vendor and no
new plan tier. What it adds that daily cannot show:

  - The 30-WEEK MA and its SLOPE. This is the actual line Stage Analysis is
    built on. The 150-day MA approximates its level but not its behaviour —
    Weinstein's Stage 1 -> 2 boundary is defined by the 30-week flattening and
    turning up, and that slope is far steadier weekly than daily.
  - Base structure. A base is 7-65 weeks; on daily bars that is 35-325 noisy
    candles, on weekly it is a shape you can read.
  - Weekly volume, which filters the intraday noise daily volume carries.

THE LOOK-AHEAD TRAP, which is the whole reason this module exists rather than
a one-line resample at the call site: on a Wednesday, THIS week's weekly
candle is three days old and incomplete. Using it leaks information the
equivalent historical bar would not have had, and it leaks it in the
flattering direction — the partial week already contains part of the move
being predicted. Every function here drops the in-progress week, and
`weekly_aligned` additionally shifts by one week so a daily bar can only ever
see weeks that had CLOSED before it.
"""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)

STAGE_MA_WEEKS = 30      # Weinstein's line
SLOPE_LOOKBACK_WEEKS = 10


def to_weekly(df: pd.DataFrame, include_partial: bool = False) -> pd.DataFrame:
    """Daily OHLCV -> weekly, labelled on the Friday of each week.

    `include_partial=False` (the default, and the only safe option for any
    historical comparison) drops the final week when it does not end on the
    frame's last weekday — i.e. when the week is still running.
    """
    if df is None or df.empty:
        return pd.DataFrame()
    wk = df.resample("W-FRI").agg({
        "Open": "first", "High": "max", "Low": "min",
        "Close": "last", "Volume": "sum",
    }).dropna(subset=["Close"])
    if wk.empty or include_partial:
        return wk
    # the last label is a Friday that may not have happened yet; the week is
    # complete only if the data actually reaches it
    last_label = wk.index[-1]
    if df.index[-1] < last_label:
        wk = wk.iloc[:-1]
    return wk


def stage_ma(weekly_close: pd.Series, weeks: int = STAGE_MA_WEEKS) -> pd.Series:
    return weekly_close.rolling(weeks).mean()


def weekly_aligned(df: pd.DataFrame, weeks: int = STAGE_MA_WEEKS,
                   slope_lookback: int = SLOPE_LOOKBACK_WEEKS) -> pd.DataFrame:
    """Weekly Stage facts, reindexed onto the DAILY index, using only weeks
    that had closed before each day.

    Returns columns: ma30w, ma30w_rising, above_ma30w — all daily-indexed.

    The `.shift(1)` is the no-lookahead guarantee and the single most important
    line here. resample labels a week by its Friday, so the value AT that label
    includes the whole week; a Tuesday inside it must not see it.
    """
    wk = to_weekly(df)
    if wk.empty or len(wk) < weeks + 1:
        return pd.DataFrame(index=df.index,
                            columns=["ma30w", "ma30w_rising", "above_ma30w"])
    ma = stage_ma(wk["Close"], weeks)
    rising = ma > ma.shift(slope_lookback)
    facts = pd.DataFrame({"ma30w": ma, "ma30w_rising": rising})
    # only weeks CLOSED before the day in question
    facts = facts.shift(1).reindex(df.index, method="ffill")
    facts["above_ma30w"] = df["Close"] > facts["ma30w"]
    return facts


def confirmation(df: pd.DataFrame) -> dict | None:
    """The weekly read for one counter, for the dossier.

    Stage 2 on the weekly chart is: price above a RISING 30-week MA. Both parts
    matter — above a falling line is a bounce inside a decline, and below a
    rising one is a pullback that has not resolved.
    """
    wk = to_weekly(df)
    if wk.empty or len(wk) < STAGE_MA_WEEKS + 1:
        return None
    ma = stage_ma(wk["Close"])
    if pd.isna(ma.iloc[-1]):
        return None
    price = float(wk["Close"].iloc[-1])
    m_now = float(ma.iloc[-1])
    rising = (len(ma) > SLOPE_LOOKBACK_WEEKS
              and pd.notna(ma.iloc[-(SLOPE_LOOKBACK_WEEKS + 1)])
              and m_now > float(ma.iloc[-(SLOPE_LOOKBACK_WEEKS + 1)]))
    above = price > m_now
    return {
        "weeks": int(len(wk)),
        "ma30w": round(m_now, 3),
        "ma30w_rising": bool(rising),
        "above_ma30w": bool(above),
        "pct_from_ma30w": round((price / m_now - 1) * 100, 1) if m_now else None,
        # the flag a caller should gate on; both halves required
        "stage2_weekly": bool(above and rising),
        "note": ("Above a rising 30-week MA — Stage 2 on the weekly chart"
                 if above and rising else
                 "Above the 30-week MA but it is not yet rising — base, not trend"
                 if above else
                 "Below the 30-week MA — not Stage 2 weekly"),
    }
