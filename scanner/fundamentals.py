"""Quarterly fundamentals — the E in SEPA. Code computes, AI interprets.

v3 change: the SOURCE moved to KLSE Screener (see klse_client), replacing
both Apify (a paid actor in front of a page we can fetch ourselves) and
yfinance (removed from the stack when US was parked). The MATH below is
v2's, unchanged — `growth_metrics` and `grade` are ported verbatim because
they encode fixed bugs: percentages off a negative base are meaningless and
return None rather than a fake number, and a mostly-empty profile returns
grade None rather than being punished for missing data.

Nothing here touches the network. `from_dossier` takes a dossier the caller
already fetched, so one page GET serves fundamentals, street data and news.
"""
from __future__ import annotations

import logging

import pandas as pd

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


def frame_from_quarters(quarters: list[dict]) -> pd.DataFrame | None:
    """Dossier quarters -> the line-item frame `growth_metrics` expects.

    Same adapter role the Apify `normalize()` played in v2: the pipeline speaks
    ONE fundamentals shape, and only this function knows where it came from.
    Needs 5 quarters — YoY is meaningless without a year-ago comparison.
    """
    rows = []
    for q in quarters or []:
        end = q.get("quarter_end")
        if not end:
            continue
        try:
            ts = pd.to_datetime(end)
        except Exception:
            continue  # a malformed quarter must not sink the counter
        rows.append((ts, q.get("revenue"), q.get("net_profit"), q.get("eps")))
    if len(rows) < 5:
        return None
    rows.sort(key=lambda r: r[0], reverse=True)
    cols = [r[0] for r in rows]
    nan = float("nan")
    data = {
        "Total Revenue": [r[1] if r[1] is not None else nan for r in rows],
        "Net Income": [r[2] if r[2] is not None else nan for r in rows],
    }
    if any(r[3] is not None for r in rows):
        data["Diluted EPS"] = [r[3] if r[3] is not None else nan for r in rows]
    return pd.DataFrame(data, index=cols).T


def from_dossier(d: dict) -> dict | None:
    """Dossier -> the fundamentals dict the rest of the pipeline speaks.

    Fundamentals are a nice-to-have — never fail the scan over them.
    """
    try:
        out = growth_metrics(frame_from_quarters(d.get("quarters")))
        if out is None:
            return None
        quarters = d.get("quarters") or []
        # The source publishes ROE PER QUARTER (Maybank 2.7), but `grade` tests
        # the O'Neil annual bar of 17%. Comparing the two would fail the ROE box
        # for essentially every counter and silently depress every grade, so
        # annualise. 2.7 x 4 = 10.8 matches Maybank's reported annual ROE.
        q_roe = next((q.get("roe_pct") for q in quarters if q.get("roe_pct") is not None), None)
        out["roe_pct"] = round(q_roe * 4, 1) if q_roe is not None else None
        out["roe_basis"] = "quarterly x4" if q_roe is not None else None
        out["debt_to_equity"] = None   # not published per-quarter by this source
        out["surprise_pct"] = None     # Bursa filings carry no consensus estimate
        out["source"] = "klsescreener"  # the stock page labels its provenance
        out["source_url"] = d.get("url")
        out["last_announced"] = quarters[0].get("announced") if quarters else None
        out["grade"] = grade(out)
        return out
    except Exception as e:
        log.info("fundamentals build failed for %s: %s", d.get("code"), e)
        return None
