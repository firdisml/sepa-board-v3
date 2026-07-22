"""Sector rotation: RS ranking + quadrant classification for SPDR sector ETFs."""
from __future__ import annotations

import pandas as pd

from . import indicators

SECTOR_ETFS = {
    "XLK": "Technology", "XLC": "Communication Services", "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples", "XLE": "Energy", "XLF": "Financials", "XLV": "Health Care",
    "XLI": "Industrials", "XLB": "Materials", "XLRE": "Real Estate", "XLU": "Utilities",
    # sub-industries worth tracking
    "SMH": "Semiconductors", "XBI": "Biotech", "ITA": "Aerospace & Defense",
}


def momentum(close: pd.Series, days: int) -> float | None:
    if len(close) <= days:
        return None
    return float(close.iloc[-1] / close.iloc[-days - 1] - 1)


def classify_quadrant(mom_1m: float, mom_3m: float) -> str:
    """Plain-English RRG-style quadrant (relative to zero momentum)."""
    if mom_3m >= 0 and mom_1m >= 0:
        return "leading"
    if mom_3m >= 0 and mom_1m < 0:
        return "weakening"
    if mom_3m < 0 and mom_1m >= 0:
        return "improving"
    return "lagging"


def sector_rotation(data: dict[str, pd.DataFrame], spy: pd.DataFrame) -> list[dict]:
    """Compute per-sector RS vs SPY, momentum, and rotation quadrant.

    data: {etf_ticker: OHLCV df}. Returns list sorted by rs_raw desc.
    """
    rows = []
    spy_1m = momentum(spy["Close"], 21) or 0.0
    spy_3m = momentum(spy["Close"], 63) or 0.0

    for etf, name in SECTOR_ETFS.items():
        df = data.get(etf)
        if df is None or len(df) < 260:
            continue
        raw = indicators.rs_raw(df)
        m1 = momentum(df["Close"], 21)
        m3 = momentum(df["Close"], 63)
        if raw is None or m1 is None or m3 is None:
            continue
        rel_1m, rel_3m = m1 - spy_1m, m3 - spy_3m
        rows.append({
            "etf": etf,
            "sector": name,
            "rs_raw": round(raw, 4),
            "mom_1m_pct": round(m1 * 100, 2),
            "mom_3m_pct": round(m3 * 100, 2),
            "rel_mom_1m_pct": round(rel_1m * 100, 2),
            "rel_mom_3m_pct": round(rel_3m * 100, 2),
            "quadrant": classify_quadrant(rel_1m, rel_3m),
            # rotation flag: short-term relative momentum crossing long-term
            "rotating_in": rel_1m > rel_3m and rel_1m > 0,
            "rotating_out": rel_1m < rel_3m and rel_1m < 0,
        })
    rows.sort(key=lambda r: r["rs_raw"], reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows
