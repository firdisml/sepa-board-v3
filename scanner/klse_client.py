"""KLSE Screener — Bursa fundamentals + street data. CODE parses, AI interprets.

PLAN §5 named i3investor as the source. A live probe on 2026-07-22 disproved
its stated premise: every table on klse.i3investor.com renders headers but an
EMPTY `<tbody>` — rows arrive by AJAX from `/wapi/web` carrying
`Authorization: Bearer <jwti3mq>`, a token issued only at sign-in. Anonymous
sessions get no token, and five POSTs to the sign-in endpoint drew a
Cloudflare 429. An authenticated nightly scrape from rotating Actions runners
is not a foundation a board can depend on, so the source moved here.

KLSE Screener serves the same Bursa filings as ordinary server-rendered HTML:
one GET of /v2/stocks/view/{code} yields 1,191 table rows covering 120
quarters, 25 annual years, dividends with ex-dates, and 919 substantial-
shareholder movements. One fetch per counter replaces seven, which is both
politer to the site and simpler to cache.

What was lost with i3investor: broker consensus price targets. Nothing else —
and per §7.1 street data is commentary-only, so no grade, bucket, signal or
receipt is affected.

TABLE SELECTION IS BY COLUMN SIGNATURE, never by index. If the site reorders
its tables, a signature miss returns None and the caller logs "unavailable"
(§14: parse failure = loud log + grade None, never a guess). Index-based
selection would silently parse dividends as earnings.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import time
from io import StringIO

import pandas as pd
import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

BASE = "https://www.klsescreener.com"
STOCK_PATH = "/v2/stocks/view/{code}"
TIMEOUT = 45
# Measured 2026-07-22: ~5 requests at 1.5s spacing trips a burst limit that
# returns HTTP 200 with the tables stripped out; 10s spacing ran clean. 8s is
# the compromise, and it still fits §7.1's ~10 pages/night in under two minutes.
THROTTLE = float(os.environ.get("KLSE_THROTTLE", "8"))
TIMEOUT_BACKOFF = (15, 45, 90)   # waits after a suspected soft-block
RETRIES = 2

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Sent on every request. See `fetch` for why these are assigned, not defaulted.
BROWSER_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

# Column signatures identifying each table regardless of position. `deny`
# disambiguates: the annual table's columns are a SUBSET of the quarterly
# table's, so without it "largest match wins" hands back 120 quarters when
# asked for 25 years.
SIGNATURES = {
    "quarters":     {"need": {"EPS", "Revenue", "Quarter", "Q Date"}, "deny": set()},
    "annual":       {"need": {"Financial Year", "EPS", "Report"}, "deny": {"Q Date", "Quarter"}},
    "dividends":    {"need": {"Announced", "Subject", "EX Date", "Payment Date"}, "deny": set()},
    "shareholding": {"need": {"Announced", "Date Change", "Type", "Shares", "Name"},
                     "deny": set()},
}

# PLAN §7.1.3 — code classifies, AI never decides what an announcement means.
ANNOUNCEMENT_RULES = [
    ("dilution",        r"private placement|rights issue|renounceable|placement of"),
    ("uma",             r"unusual market activity|\buma\b"),
    ("results",         r"quarterly report|quarterly rpt|financial report|annual report"),
    ("contract",        r"contract|letter of award|\bloa\b|tender|memorandum of understanding|\bmou\b"),
    ("bonus_split",     r"bonus issue|share split|subdivision"),
    ("esos",            r"\besos\b|employees share|share option"),
    ("related_party",   r"related party|\brpt\b"),
    ("insider_dealing", r"changes in sub|s-hldr|substantial shareholder|dealings? in securities|"
                        r"director'?s? interest|section 138|section 219"),
    ("capital",         r"share buy-back|buyback|capital repayment|redemption"),
]


class ParseFailure(RuntimeError):
    """The page loaded but did not contain what we expected. Never guessed at."""


def code_of(ticker: str) -> str:
    """Internal ticker -> Bursa numeric code. `1155.KL` -> `1155`."""
    return ticker.split(".", 1)[0]


def stock_url(code: str) -> str:
    return BASE + STOCK_PATH.format(code=code)


def _absolute(href: str) -> str:
    if not href:
        return ""
    return href if href.startswith("http") else BASE + href


def fetch(code: str, session: requests.Session | None = None) -> str:
    """GET a counter's page. Throttled, retried once on transport failure.

    HEADERS MUST BE ASSIGNED, NOT `setdefault`: a `requests.Session` is born
    carrying `User-Agent: python-requests/x.y`, so setdefault silently keeps it
    and every request announces itself as a script — which this site answers
    with 403. That bug was invisible for a while because the parser tests read
    a saved fixture and the early probes passed headers to `requests.get`
    directly, so this function was never exercised live.
    """
    s = session or requests.Session()
    s.headers.update(BROWSER_HEADERS)
    url = stock_url(code)
    last = None
    for attempt in range(len(TIMEOUT_BACKOFF) + 1):
        try:
            r = s.get(url, timeout=TIMEOUT)
            if r.status_code == 404:
                raise ParseFailure(f"{code}: no such counter (404)")
            if r.status_code in (403, 429):
                raise ParseFailure(
                    f"{code}: refused by source (HTTP {r.status_code}) — "
                    f"check headers before retrying")
            r.raise_for_status()
            if _throttled(r.text):
                # A burst limit answers 200 with the tables stripped out. Calling
                # that "layout changed" would send someone hunting a parser bug
                # that does not exist, so name it and wait it out.
                if attempt < len(TIMEOUT_BACKOFF):
                    wait = TIMEOUT_BACKOFF[attempt]
                    log.warning("%s: throttled by source, backing off %ds", code, wait)
                    time.sleep(wait)
                    continue
                raise ParseFailure(f"{code}: still throttled after "
                                   f"{len(TIMEOUT_BACKOFF)} backoffs")
            time.sleep(THROTTLE)
            return r.text
        except ParseFailure:
            raise
        except Exception as e:
            last = e
            if attempt < len(TIMEOUT_BACKOFF):
                time.sleep(2 + attempt * 2)
    raise ParseFailure(f"{code}: fetch failed — {last}")


def _throttled(html: str) -> bool:
    """A served page always carries table rows. HTTP 200 with none means the
    burst limit stripped it, NOT that the site changed shape."""
    return html.count("<tr") < 20


# ---------------------------------------------------------------- tables

def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Drop the `Unnamed:` padding columns that colspan artefacts create."""
    keep = [c for c in df.columns if not str(c).startswith("Unnamed:")]
    return df[keep]


def _pick(tables: list[pd.DataFrame], kind: str) -> pd.DataFrame | None:
    """Find the table whose columns match this kind's signature."""
    sig = SIGNATURES[kind]
    need, deny = sig["need"], sig["deny"]
    best = None
    for t in tables:
        cols = {str(c).strip() for c in t.columns}
        if need <= cols and not (deny & cols) and (best is None or len(t) > len(best)):
            best = t
    if best is None:
        log.warning("no table matching signature %s (%s)", kind, sorted(need))
        return None
    return _drop_group_rows(_clean(best))


def _drop_group_rows(df: pd.DataFrame) -> pd.DataFrame:
    """KLSE Screener groups quarters under a financial-year banner rendered as
    `<td colspan="100">`. pandas widens the whole table to 100 columns and
    emits the banner as a row repeating the year in every cell. Left in, those
    rows become phantom quarters with no figures."""
    if df.empty:
        return df
    def is_banner(row) -> bool:
        vals = {str(v).strip() for v in row if v is not None and str(v) != "nan"}
        return len(vals) <= 1
    return df[~df.apply(is_banner, axis=1)]


_SUFFIX = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}


def _num(v) -> float | None:
    """'14,915,455' / '20.53' / '4.2%' / '14.9b' / '(1,234)' -> float or None.

    PRECISION NOTE: the quarterly table publishes revenue and P/L abbreviated
    to three significant figures ('14.9b'), so YoY growth derived from them
    carries roughly ±1pp. EPS is exact to 4dp, which is why `grade` prefers
    the EPS growth box over the NI one — and why the annual table (exact,
    in thousands) is the cross-check. A missing figure is None, never 0:
    a fabricated zero would silently drive the grade.
    """
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip().replace(",", "").replace("%", "").replace("RM", "")
    if s in ("", "-", "nan", "N/A", "NA"):
        return None
    neg = s.startswith("(") and s.endswith(")")   # accounting negatives
    s = s.strip("()").strip()
    mult = 1.0
    if s and s[-1].lower() in _SUFFIX:
        mult = _SUFFIX[s[-1].lower()]
        s = s[:-1]
    try:
        f = float(s) * mult
    except ValueError:
        return None
    return -f if neg else f


def _date(v) -> str | None:
    """'31 Dec, 2026' / '22 Jul 2026' -> ISO date string."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip().replace(",", "")
    for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_quarters(tables: list[pd.DataFrame], limit: int = 12) -> list[dict]:
    """Most recent quarters first — the §5 fundamentals source."""
    t = _pick(tables, "quarters")
    if t is None or t.empty:
        return []
    out = []
    for _, r in t.head(limit).iterrows():
        q_date = _date(r.get("Q Date")) or _date(r.get("Financial Year"))
        rev, pl = _num(r.get("Revenue")), _num(r.get("P/L"))
        if q_date is None and rev is None and pl is None:
            continue
        out.append({
            "quarter_end": q_date,
            "announced": _date(r.get("Announced")),
            "revenue": rev,
            "net_profit": pl,
            "eps": _num(r.get("EPS")),
            "dps": _num(r.get("DPS")),
            "nta": _num(r.get("NTA")),
            "roe_pct": _num(r.get("ROE")),
        })
    return out


def parse_annual(tables: list[pd.DataFrame], limit: int = 6) -> list[dict]:
    """5y+ trend — is the quarterly acceleration an inflection or a blip? (§7.1.5)"""
    t = _pick(tables, "annual")
    if t is None or t.empty:
        return []
    rev_col = next((c for c in t.columns if str(c).startswith("Revenue")), None)
    net_col = next((c for c in t.columns if str(c).startswith("Net (")), None)

    def scaled(row, col):
        """This table publishes in thousands; normalise to absolute units so
        annual and quarterly figures are directly comparable."""
        if not col:
            return None
        v = _num(row.get(col))
        return v * 1000 if v is not None and "'000" in str(col) else v

    out = []
    for _, r in t.head(limit).iterrows():
        out.append({
            "year_end": _date(r.get("Financial Year")),
            "revenue": scaled(r, rev_col),
            "net_profit": scaled(r, net_col),
            "eps": _num(r.get("EPS")),
        })
    return out


def parse_dividends(tables: list[pd.DataFrame], limit: int = 6) -> list[dict]:
    """Ex-date near a breakout is a gap hazard, same class as earnings (§7.1.7)."""
    t = _pick(tables, "dividends")
    if t is None or t.empty:
        return []
    out = []
    for _, r in t.head(limit).iterrows():
        out.append({
            "announced": _date(r.get("Announced")),
            "subject": str(r.get("Subject") or "").strip()[:120],
            "ex_date": _date(r.get("EX Date")),
            "payment_date": _date(r.get("Payment Date")),
            "amount": str(r.get("Amount") or "").strip()[:40],
        })
    return out


def parse_shareholding(tables: list[pd.DataFrame], days: int = 90,
                       limit: int = 40) -> dict:
    """Substantial-shareholder movement — the sponsorship leg moomoo used to
    provide (§7.1.2). Accumulation by EPF/KWAP during a base is the paper
    confirmation of what the chart implies."""
    t = _pick(tables, "shareholding")
    if t is None or t.empty:
        return {"window_days": days, "acquired": 0, "disposed": 0, "net_shares": 0,
                "holders": [], "events": []}
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    acq = dis = 0
    holders: dict[str, int] = {}
    events = []
    for _, r in t.head(500).iterrows():
        when = _date(r.get("Announced"))
        if when is None or when < cutoff:
            continue
        shares = _num(r.get("Shares")) or 0
        kind = str(r.get("Type") or "").strip().lower()
        name = str(r.get("Name") or "").strip()[:80]
        signed = int(shares) if kind.startswith("acquir") else -int(shares)
        if signed >= 0:
            acq += signed
        else:
            dis += -signed
        holders[name] = holders.get(name, 0) + signed
        if len(events) < limit:
            events.append({"announced": when, "date_change": _date(r.get("Date Change")),
                           "type": kind, "shares": int(shares), "name": name})
    top = sorted(holders.items(), key=lambda kv: abs(kv[1]), reverse=True)[:5]
    return {"window_days": days, "acquired": acq, "disposed": dis,
            "net_shares": acq - dis,
            "holders": [{"name": n, "net_shares": v} for n, v in top],
            "events": events}


# ---------------------------------------------------------------- lists

def classify(title: str) -> str:
    """Keyword classification of an announcement. Deterministic, auditable —
    the AI receives the label, it does not choose it."""
    t = (title or "").lower()
    for label, pattern in ANNOUNCEMENT_RULES:
        if re.search(pattern, t):
            return label
    return "other"


def _list_items(soup: BeautifulSoup, heading: str, limit: int) -> list[dict]:
    """The announcement/news blocks are `<li class="list-group-item">` with an
    `<h6><a href>` title and a `<time datetime>` — NOT tables, so read_html
    never sees them. Every item keeps its source URL so the UI can link out."""
    head = soup.find(lambda tag: tag.name in ("h5", "h6", "h4")
                     and heading.lower() in tag.get_text(strip=True).lower())
    if head is None:
        return []
    ul = head.find_next("ul", class_="list-group")
    if ul is None:
        return []
    out = []
    for li in ul.find_all("li", recursive=False)[:limit]:
        a = li.find("a", href=True)
        if a is None:
            continue
        tm = li.find("time")
        body = li.find("div", class_="text-justify")
        src = li.find("span")
        item = {
            "title": a.get_text(" ", strip=True)[:200],
            "url": _absolute(a["href"]),
            "date": (tm.get("datetime") or tm.get_text(strip=True))[:19] if tm else None,
            "summary": body.get_text(" ", strip=True)[:300] if body else "",
            "source": src.get_text(strip=True)[:40] if src else "",
        }
        out.append(item)
    return out


def parse_announcements(soup: BeautifulSoup, limit: int = 20) -> list[dict]:
    items = _list_items(soup, "Recent Announcements", limit)
    for it in items:
        it["category"] = classify(it["title"])
        it.pop("source", None)
        it.pop("summary", None)
    return items


def parse_news(soup: BeautifulSoup, limit: int = 10) -> list[dict]:
    """Headlines are UNTRUSTED text — they reach Gemini as data, never as
    instructions (§7 system-prompt invariant). Malaysian coverage is often
    Chinese-language; that is fine, the model reads it."""
    return _list_items(soup, "Recent News", limit)


# ---------------------------------------------------------------- dossier

def dossier(code: str, session: requests.Session | None = None) -> dict:
    """One counter, one fetch -> the compact JSON the Tier-A prompt receives.

    Raises ParseFailure only when the page itself is unusable; individual
    sections degrade to empty and are reported as unavailable, because a
    missing dividend table must not cost us the earnings history.
    """
    html = fetch(code, session=session)
    soup = BeautifulSoup(html, "html.parser")
    try:
        tables = pd.read_html(StringIO(html))
    except ValueError:
        tables = []
    if not tables:
        raise ParseFailure(f"{code}: no tables in page — layout changed?")

    d = {
        "code": code,
        "url": stock_url(code),
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "quarters": parse_quarters(tables),
        "annual": parse_annual(tables),
        "dividends": parse_dividends(tables),
        "shareholding": parse_shareholding(tables),
        "announcements": parse_announcements(soup),
        "news": parse_news(soup),
    }
    missing = [k for k in ("quarters", "annual", "shareholding") if not d[k]]
    if missing:
        log.warning("%s: sections unavailable: %s", code, missing)
    d["unavailable"] = missing
    return d
