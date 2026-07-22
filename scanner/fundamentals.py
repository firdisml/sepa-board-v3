"""Quarterly fundamentals — the E in SEPA. Code computes, AI interprets.

From yfinance quarterly income statements: YoY revenue / net-income / EPS
growth with acceleration flags (the CANSLIM tell) and the net-margin trend.
From the ticker profile: ROE and debt/equity (O'Neil quality screens).
From the earnings calendar: the last reported EPS surprise.

Everything rolls into a mechanical A-E "grade" — a transparent scorecard,
not an AI opinion: EPS growth 25%+, revenue growth 20%+, acceleration,
margin expansion, ROE 17%+. Percentages off a negative base are meaningless
and return None rather than a fake number; a mostly-empty profile (common on
Bursa) returns grade None rather than punishing missing data.
"""
from __future__ import annotations

import logging

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

_REV_ROWS = ("Total Revenue", "TotalRevenue", "Operating Revenue")
_NI_ROWS = ("Net Income", "NetIncome", "Net Income Common Stockholders")
_EPS_ROWS = ("Diluted EPS", "DilutedEPS", "Basic EPS", "BasicEPS")


def growth_metrics(q: pd.DataFrame | None) -> dict | None:
    """q: quarterly income statement (rows = line items, columns = period end).

    Returns growth + margin fields or None when nothing usable exists.
    """
    if q is None or q.empty:
        return None
    try:
        q = q[sorted(q.columns, reverse=True)]  # newest quarter first
    except Exception:
        return None

    def row(names):
        for n in names:
            if n in q.index:
                return q.loc[n]
        return None

    rev, ni, eps = row(_REV_ROWS), row(_NI_ROWS), row(_EPS_ROWS)
    if rev is None and ni is None:
        return None

    def yoy(s, i):
        # quarter i vs the same quarter a year earlier (i+4)
        try:
            cur, prev = float(s.iloc[i]), float(s.iloc[i + 4])
        except Exception:
            return None
        if pd.isna(cur) or pd.isna(prev) or prev <= 0:
            return None  # negative/zero base -> growth % is meaningless
        return round((cur / prev - 1) * 100, 1)

    def margin(i):
        # net margin of quarter i, in % — valid even when NI is negative
        try:
            r, n = float(rev.iloc[i]), float(ni.iloc[i])
        except Exception:
            return None
        if pd.isna(r) or pd.isna(n) or r <= 0:
            return None
        return round(n / r * 100, 1)

    out = {
        "rev_yoy_pct": yoy(rev, 0) if rev is not None else None,
        "rev_yoy_prev_pct": yoy(rev, 1) if rev is not None else None,
        "ni_yoy_pct": yoy(ni, 0) if ni is not None else None,
        "ni_yoy_prev_pct": yoy(ni, 1) if ni is not None else None,
        "eps_yoy_pct": yoy(eps, 0) if eps is not None else None,
        "eps_yoy_prev_pct": yoy(eps, 1) if eps is not None else None,
        "quarter_end": (str(q.columns[0].date()) if hasattr(q.columns[0], "date")
                        else str(q.columns[0])),
    }
    if all(out[k] is None for k in ("rev_yoy_pct", "rev_yoy_prev_pct",
                                    "ni_yoy_pct", "ni_yoy_prev_pct")):
        return None
    g, p = out["ni_yoy_pct"], out["ni_yoy_prev_pct"]
    out["accelerating"] = bool(g is not None and p is not None and g > p)
    ge, pe = out["eps_yoy_pct"], out["eps_yoy_prev_pct"]
    out["eps_accelerating"] = bool(ge is not None and pe is not None and ge > pe)
    m_now, m_yr = (margin(0), margin(4)) if ni is not None and rev is not None else (None, None)
    out["margin_pct"] = m_now
    out["margin_delta_pp"] = (round(m_now - m_yr, 1)
                              if m_now is not None and m_yr is not None else None)
    return out


def profile_metrics(t: yf.Ticker) -> dict:
    """Quality numbers from the ticker profile — each guarded; Bursa coverage
    is spotty and a missing field must stay None, never crash the scan."""
    out = {"roe_pct": None, "debt_to_equity": None, "surprise_pct": None}
    try:
        info = t.info or {}
        roe = info.get("returnOnEquity")
        if isinstance(roe, (int, float)) and not pd.isna(roe):
            out["roe_pct"] = round(float(roe) * 100, 1)
        dte = info.get("debtToEquity")  # yfinance reports this as a percent-like number
        if isinstance(dte, (int, float)) and not pd.isna(dte):
            out["debt_to_equity"] = round(float(dte), 1)
    except Exception:
        pass
    try:
        ed = t.earnings_dates
        if ed is not None and "Surprise(%)" in getattr(ed, "columns", []):
            s = ed["Surprise(%)"].dropna()  # future rows are NaN; first drop = latest reported
            if len(s):
                out["surprise_pct"] = round(float(s.iloc[0]), 1)
    except Exception:
        pass
    return out


def grade(m: dict) -> str | None:
    """Mechanical CANSLIM-style scorecard, graded on the boxes that HAVE data:
    EPS (or NI) growth 25%+, revenue growth 20%+, growth accelerating,
    margin expanding, ROE 17%+. Under 3 known boxes -> None (not enough to
    judge), so sparse Bursa profiles aren't punished for missing fields."""
    growth = m.get("eps_yoy_pct") if m.get("eps_yoy_pct") is not None else m.get("ni_yoy_pct")
    accel = (m.get("eps_accelerating") or m.get("accelerating")
             if (m.get("eps_yoy_prev_pct") is not None or m.get("ni_yoy_prev_pct") is not None)
             else None)
    boxes = [
        (growth, lambda v: v >= 25),
        (m.get("rev_yoy_pct"), lambda v: v >= 20),
        (accel, lambda v: bool(v)),
        (m.get("margin_delta_pp"), lambda v: v > 0),
        (m.get("roe_pct"), lambda v: v >= 17),
    ]
    known = [(v, ok) for v, ok in boxes if v is not None]
    if len(known) < 3:
        return None
    score = sum(1 for v, ok in known if ok(v)) / len(known)
    return ("A" if score >= 0.8 else "B" if score >= 0.6 else
            "C" if score >= 0.4 else "D" if score >= 0.2 else "E")


def fetch(ticker: str) -> dict | None:
    """Fundamentals are a nice-to-have — never fail the scan over them."""
    try:
        t = yf.Ticker(ticker)
        out = growth_metrics(t.quarterly_income_stmt)
        if out is None:
            return None
        out.update(profile_metrics(t))
        out["grade"] = grade(out)
        return out
    except Exception as e:
        log.info("fundamentals fetch failed for %s: %s", ticker, e)
        return None
