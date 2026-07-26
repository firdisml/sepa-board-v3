"""US quarterly fundamentals from SEC XBRL — the E in SEPA, for US counters.

Bursa gets its fundamentals from filings (KLSE Screener's quarterly reports).
This is the symmetric US source: SEC's XBRL `companyfacts` API, which is the
as-filed primary data every paid vendor resells. Free, unauthenticated,
unlimited — EODHD's fundamentals feed is a separate $59.99 tier that returned
403 on this project's All-World plan (verified 2026-07-26).

THE MATH IS NOT REIMPLEMENTED. This module only ADAPTS XBRL into the
line-item frame `fundamentals.growth_metrics` already consumes, then calls
`fundamentals.grade` — so an A on a US counter means exactly what an A means
on a Bursa counter, which is the whole point of doing it this way.

Three XBRL realities this has to survive, all found by probing real filers:

1. TAG NAMES VARY BY FILER. Sandisk reports revenue as
   `RevenueFromContractWithCustomerExcludingAssessedTax`; Anterix uses plain
   `Revenues`. Hence a fallback chain per concept, not one tag.
2. A 10-Q CARRIES CUMULATIVE FIGURES TOO. The same period appears as a
   3-month AND a 6/9-month year-to-date value (SNDK 2026Q3 showed both
   11.28bn and 5.95bn). Taking either blindly makes revenue growth fiction,
   so only ~3-month durations are kept — preferring SEC's own calendar
   `frame` label, falling back to measuring start→end.
3. RESTATEMENTS. One period can appear in several filings with different
   values. The newest `filed` date wins.

Requires 5 quarters, same as the Bursa path: YoY is meaningless without a
year-ago comparison.
"""
from __future__ import annotations

import logging
import re

import pandas as pd

from . import edgar, fundamentals

log = logging.getLogger(__name__)

FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# Fallback chains, most-specific first. Order matters: a filer using the
# post-2018 revenue-recognition tag should not be read via a legacy alias.
REVENUE_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
)
NET_INCOME_TAGS = (
    "NetIncomeLoss",
    "NetIncomeLossAvailableToCommonStockholdersBasic",
    "ProfitLoss",
)
EPS_TAGS = (
    "EarningsPerShareDiluted",
    "EarningsPerShareBasicAndDiluted",
    "IncomeLossFromContinuingOperationsPerDilutedShare",
    "EarningsPerShareBasic",
)
EQUITY_TAGS = (
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
)

MIN_QUARTERS = 5
# SEC labels calendar-aligned durations CY2025Q3; a trailing "I" means INSTANT
# (a balance-sheet point), which must never be treated as a flow.
_FRAME_QUARTER = re.compile(r"^CY\d{4}Q\d$")
_FRAME_INSTANT = re.compile(r"^CY\d{4}Q\dI$")


def _is_quarter_duration(f: dict) -> bool:
    """Is this fact a ~3-month flow, rather than a year-to-date cumulative?"""
    frame = f.get("frame")
    if frame:
        return bool(_FRAME_QUARTER.match(frame))
    start, end = f.get("start"), f.get("end")
    if not (start and end):
        return False
    try:
        days = (pd.Timestamp(end) - pd.Timestamp(start)).days
    except Exception:
        return False
    return 75 <= days <= 100        # a fiscal quarter, however the filer cuts it


def _flow_series(gaap: dict, tags: tuple[str, ...],
                 max_age_years: int = 4) -> dict[str, float]:
    """{period_end: value} for quarterly flows.

    MERGES the whole tag chain instead of returning the first tag that happens
    to have enough facts. Filers CHANGE TAGS over time: CF Industries stopped
    reporting `NetIncomeLoss` around 2012, so first-match returned net income
    ending 2012 alongside revenue ending 2026 — the intersection was empty and
    the counter went ungraded, but had the periods overlapped it would have
    produced a confident grade computed from FOURTEEN-YEAR-OLD financials.
    Merging means whichever tag the filer uses TODAY covers recent quarters,
    while earlier entries in the chain still win any period they both report.

    Also drops anything older than `max_age_years`: five quarters plus a
    year-ago comparison is all the scorecard needs, and stale data can only
    mislead.
    """
    cutoff = (pd.Timestamp.utcnow().tz_localize(None)
              - pd.DateOffset(years=max_age_years))
    best: dict[str, tuple[int, str, float]] = {}   # end -> (tag_rank, filed, val)
    for rank, tag in enumerate(tags):
        node = gaap.get(tag)
        if not node:
            continue
        for facts in node.get("units", {}).values():
            for f in facts:
                if not _is_quarter_duration(f):
                    continue
                end, val = f.get("end"), f.get("val")
                if end is None or val is None:
                    continue
                try:
                    if pd.Timestamp(end) < cutoff:
                        continue
                except Exception:
                    continue
                filed = f.get("filed") or ""
                prev = best.get(end)
                # higher-priority tag wins; within one tag, the LATEST filing
                # wins so a restatement supersedes the original
                if prev is None or rank < prev[0] or (rank == prev[0] and filed > prev[1]):
                    best[end] = (rank, filed, float(val))
    return {e: v for e, (_, _, v) in best.items()}


def _latest_instant(gaap: dict, tags: tuple[str, ...]) -> float | None:
    """Most recent balance-sheet value (equity), newest filing winning ties."""
    for tag in tags:
        node = gaap.get(tag)
        if not node:
            continue
        best = None
        for facts in node.get("units", {}).values():
            for f in facts:
                if f.get("start"):
                    continue            # a duration, not an instant
                frame = f.get("frame")
                if frame and not _FRAME_INSTANT.match(frame):
                    continue
                end, val = f.get("end"), f.get("val")
                if end is None or val is None:
                    continue
                key = (end, f.get("filed") or "")
                if best is None or key > best[0]:
                    best = (key, float(val))
        if best:
            return best[1]
    return None


def _quarter_grid(ends: list[str], span: int = 12) -> list[tuple[pd.Timestamp, str | None]]:
    """Newest-first consecutive quarter slots as (column_label, filed_end).

    `filed_end` is None where no filing covers that slot. The label is ALWAYS a
    real timestamp, never NaT: growth_metrics re-sorts on `q.columns`, and NaT
    poisons that sort so every metric came back None. A gap therefore carries a
    synthetic label (the expected quarter end) with NaN values underneath —
    ordering stays correct while the absence stays honest.

    Walks back ~91 days at a time and snaps to a real end date within a month,
    because fiscal calendars drift (Apple's quarters end on a Saturday) and
    exact arithmetic would match nothing.
    """
    if not ends:
        return []
    ts = sorted((pd.Timestamp(e) for e in ends), reverse=True)
    by_date = {pd.Timestamp(e): e for e in ends}
    out: list[tuple[pd.Timestamp, str | None]] = []
    cursor = ts[0]
    for _ in range(span):
        hit = min(ts, key=lambda d: abs((d - cursor).days))
        if abs((hit - cursor).days) <= 30:
            out.append((hit, by_date[hit]))
            cursor = hit - pd.Timedelta(days=91)
        else:
            out.append((cursor, None))   # genuinely no filing for this quarter
            cursor = cursor - pd.Timedelta(days=91)
    return out


def frame_from_xbrl(gaap: dict) -> pd.DataFrame | None:
    """XBRL facts -> the line-item frame `growth_metrics` expects.

    Same adapter role `fundamentals.frame_from_quarters` plays for Bursa: the
    pipeline speaks ONE fundamentals shape and only this function knows where
    the numbers came from.
    """
    rev = _flow_series(gaap, REVENUE_TAGS)
    ni = _flow_series(gaap, NET_INCOME_TAGS)
    eps = _flow_series(gaap, EPS_TAGS)
    if len(rev) < MIN_QUARTERS or len(ni) < MIN_QUARTERS:
        return None

    # Only periods with BOTH revenue and net income — a quarter missing either
    # produces a half-real column that growth_metrics would silently average in.
    have = sorted(set(rev) & set(ni), reverse=True)
    if len(have) < MIN_QUARTERS:
        return None

    # COLUMNS MUST BE CONSECUTIVE QUARTERS. growth_metrics compares iloc[i]
    # against iloc[i+4] — position, not date — so any gap silently compares the
    # wrong periods. Apple showed this: its September quarter appears only in
    # the 10-K as a 12-MONTH figure, so the 3-month filter (correctly) drops it,
    # and the resulting hole made iloc[4] land 15 months back — reporting a 10%
    # revenue DECLINE for a company that grew.
    #
    # A missing quarter is left as NaN rather than derived from FY-minus-9M:
    # yoy() already returns None on NaN, so a hole degrades to "unknown" while
    # a derived figure would be a number nobody filed.
    grid = _quarter_grid(have)
    nan = float("nan")
    cols = [label for label, _ in grid]
    keys = [key for _, key in grid]
    data = {
        "Total Revenue": [rev.get(k, nan) if k else nan for k in keys],
        "Net Income": [ni.get(k, nan) if k else nan for k in keys],
    }
    if any(k in eps for k in keys if k):
        data["Diluted EPS"] = [eps.get(k, nan) if k else nan for k in keys]
    return pd.DataFrame(data, index=cols).T


def for_ticker(ticker: str) -> dict | None:
    """Ticker -> the fundamentals dict the rest of the pipeline speaks, graded
    on the SAME scorecard as Bursa.

    Fundamentals are a nice-to-have — never fail the scan over them.
    """
    try:
        cik = edgar.cik_of(ticker)
        if cik is None:
            return None
        d = edgar._get(FACTS_URL.format(cik=cik))
        gaap = (d.get("facts") or {}).get("us-gaap") or {}
        if not gaap:
            log.info("XBRL: %s reports no us-gaap facts", ticker)
            return None

        out = fundamentals.growth_metrics(frame_from_xbrl(gaap))
        if out is None:
            return None

        # ROE on a TRAILING-TWELVE-MONTH basis, because `grade` tests O'Neil's
        # ANNUAL 17% bar. The Bursa path multiplies a published quarterly ROE
        # by 4; here the four quarters are already in hand, so summing them is
        # strictly more accurate than annualising one.
        ni = _flow_series(gaap, NET_INCOME_TAGS)
        equity = _latest_instant(gaap, EQUITY_TAGS)
        roe = None
        if ni and equity and equity > 0:
            ttm = sum(v for _, v in sorted(ni.items(), reverse=True)[:4])
            if len(ni) >= 4:
                roe = round(ttm / equity * 100, 1)
        # A net margin far outside +-100% is not measuring operations — it is a
        # one-off gain or an asset sale against tiny revenue (Anterix: $72m
        # income on $4.5m revenue = 1600%, from selling spectrum). Grading
        # "margin expanding" on that inflates the grade, so drop it, following
        # this file's existing rule that a meaningless percentage returns None
        # rather than a fake number. NOTE: the Bursa path shares this exposure
        # and does not yet guard it.
        mp = out.get("margin_pct")
        if mp is not None and abs(mp) > 100:
            out["margin_delta_pp"] = None
            out["margin_note"] = "margin not graded — one-off items dominate revenue"

        out["roe_pct"] = roe
        out["roe_basis"] = "TTM net income / latest equity" if roe is not None else None
        out["debt_to_equity"] = None    # needs a liabilities tag chain; not yet
        out["surprise_pct"] = None      # filings carry no consensus estimate
        out["source"] = "sec-xbrl"
        out["source_url"] = (f"https://www.sec.gov/cgi-bin/browse-edgar"
                             f"?action=getcompany&CIK={cik}&type=10-Q")
        out["last_announced"] = None
        out["grade"] = fundamentals.grade(out)
        return out
    except Exception as e:
        log.info("US fundamentals build failed for %s: %s", ticker, e)
        return None
