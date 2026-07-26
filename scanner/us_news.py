"""US headlines from EODHD's news endpoint — the narrative layer over EDGAR.

edgar.py answers "what was FILED" (primary source, immutable, categorised).
This answers "what is being SAID", which is the other half of the §7.1
catalyst check and the input to the analyst's news-risk rating.

RELEVANCE IS THE WHOLE PROBLEM HERE, measured before this was written
(2026-07-25, live token):

    AAPL.US    3/20 titles actually named Apple   (median 8 symbols/article)
    ELF.US    12/20                               (median 5)
    1155.KLSE  5/5                                (median 2)

The cause is visible in the payload: articles are tagged with EVERY ticker
they mention, including promotional boilerplate ("if you invested $1,000 in
Nvidia..."), so mega-caps drown in listicles that are not about them. A
query for AAPL.US returned "What's Going on With Alphabet Stock?".

Two cheap filters fix it without hand-maintained rules:
  - drop articles tagged with more than `max_symbols` tickers — an 11-ticker
    article is a listicle, not company news;
  - keep the rest only if the ticker ranks early in `symbols` or the company
    name appears in the title.
Both are conservative: a dropped article costs the AI a headline it could
have read, while a kept irrelevant one actively misleads a risk rating.

This matters less than it sounds for THIS board, which screens for liquid
small/mid caps: the US dry-run's picks (ERAS, ATEX, SNDK) returned 1-symbol,
on-topic articles. The filter is insurance for when a mega-cap qualifies.

Headlines are UNTRUSTED third-party text (§7 invariant): data for the AI to
read, never an input to a grade, bucket, signal or receipt.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

BASE = "https://eodhd.com/api/news"
TIMEOUT = 60
RETRIES = 3

# An article tagged with more tickers than this is about a THEME, not a
# company. Measured: on-topic company news carries 1-2.
MAX_SYMBOLS = 6
# At or below this many tags, being listed at all means the piece is about
# you. Above it, the title must say so.
EXCLUSIVE_TAGS = 2


class NewsUnavailable(RuntimeError):
    """Vendor returned nothing usable. Callers degrade — a counter without
    headlines is worse-informed, never wrong."""


def _token() -> str:
    tok = os.environ.get("EODHD_API_TOKEN")
    if not tok:
        raise RuntimeError("EODHD_API_TOKEN is not set")
    return tok


def to_vendor(ticker: str) -> str:
    """Internal ticker -> vendor code, matching eodhd_client's mapping so the
    two never disagree about what a ticker is called."""
    if ticker.endswith(".KL"):
        return ticker[:-3] + ".KLSE"
    return ticker if "." in ticker else ticker + ".US"


def _get(**params) -> list:
    params.update(api_token=_token(), fmt="json")
    url = f"{BASE}?{urllib.parse.urlencode(params)}"
    last = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
                return json.loads(r.read())
        except Exception as e:
            last = str(e)
            code = getattr(e, "code", None)
            if code and 400 <= code < 500:
                raise NewsUnavailable(f"HTTP {code} for {params.get('s')}") from e
        if attempt < RETRIES - 1:
            time.sleep(2 ** attempt)
    raise NewsUnavailable(f"news {params.get('s')} failed after {RETRIES}: {last}")


def _name_tokens(company: str | None) -> list[str]:
    """Distinctive words from a company name, for title matching. Corporate
    suffixes are dropped — every filing says 'Inc', so matching on it would
    make the filter useless."""
    if not company:
        return []
    stop = {"inc", "corp", "corporation", "co", "company", "ltd", "limited",
            "plc", "holdings", "holding", "group", "the", "and", "class",
            "common", "stock", "sa", "nv", "ag", "berhad", "bhd"}
    words = re.findall(r"[a-z0-9\.']+", company.lower())
    return [w for w in words if w not in stop and len(w) > 2]


def is_relevant(item: dict, ticker: str, company: str | None = None,
                max_symbols: int = MAX_SYMBOLS,
                exclusive_tags: int = EXCLUSIVE_TAGS) -> bool:
    """Is this article ABOUT the counter, or does it merely mention it?

    POSITION IN `symbols` IS NOT A RELEVANCE SIGNAL — the array is sorted
    ALPHABETICALLY. An early draft treated "ticker in the first 3 symbols" as
    subject-hood, which passed every AAPL listicle ever written, because AAPL
    sorts first among mega-caps. Verified against the live feed 2026-07-26.

    What actually separates the two, measured on that feed:
      - the TITLE names the company or its ticker; or
      - the article is tagged with almost nothing else, so being tagged at
        all means it is the subject (on-topic pieces carry 1-2 tags;
        listicles carry 4-11).
    """
    syms = [str(s).upper() for s in (item.get("symbols") or [])]
    title = (item.get("title") or "").lower()
    bare = ticker.split(".")[0].upper()

    # TITLE MATCH WINS, and is checked FIRST. An earlier ordering rejected on
    # tag-count before looking at the title, which threw away "Anterix Surges
    # 163% in the Past Year" — an article about the counter, dropped for also
    # mentioning peers. If the headline names the company, it is about the
    # company however many tickers ride along.
    if re.search(rf"\b{re.escape(bare.lower())}\b", title):
        return True
    if any(t in title for t in _name_tokens(company)):
        return True

    if len(syms) > max_symbols:
        return False                      # listicle / theme piece, unnamed

    # Not named in the headline, but tagged almost exclusively -> still ours.
    tagged = any(s == to_vendor(ticker).upper() or s.split(".")[0] == bare
                 for s in syms)
    return tagged and len(syms) <= exclusive_tags


def headlines(ticker: str, limit: int = 12, days: int | None = 90,
              company: str | None = None, filter_relevance: bool = True) -> list[dict]:
    """Recent headlines for one counter, shaped like klse_client's news items
    so db.save_counter_news stores them unchanged.

    `link` is the dedupe key: the feed serves exact duplicates (the same NVDA
    article appeared twice, same timestamp, in the probe).
    """
    params = {"s": to_vendor(ticker), "limit": max(limit * 3, 30), "offset": 0}
    if days:
        from datetime import date, timedelta
        params["from"] = str(date.today() - timedelta(days=days))
    items = _get(**params) or []

    out, seen = [], set()
    for it in items:
        link = it.get("link") or ""
        # Dedupe on TITLE, not link: the same story is syndicated to several
        # domains under different URLs (one article arrived from both
        # finance.yahoo.com and nasdaq.com), and the feed also repeats exact
        # duplicates. Three copies of one headline reads as three events.
        key = re.sub(r"\W+", "", (it.get("title") or "").lower())[:120] or link
        if key in seen:
            continue
        if filter_relevance and not is_relevant(it, ticker, company):
            continue
        seen.add(key)
        sent = it.get("sentiment") or {}
        out.append({
            # no /view/ id in this feed; the article URL is the stable key
            "item_id": link[:200] or f"{ticker}:{it.get('date','')}",
            "title": (it.get("title") or "").strip()[:300],
            "url": link,
            "source": _domain(link),
            "category": None,              # news is uncategorised; filings carry categories
            "date": (it.get("date") or "")[:19],
            # kept for the AI to weigh, never fed into a grade or signal
            "polarity": sent.get("polarity"),
        })
        if len(out) >= limit:
            break
    return out


def _domain(url: str) -> str:
    try:
        host = urllib.parse.urlparse(url).netloc
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""
