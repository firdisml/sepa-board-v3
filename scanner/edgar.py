"""SEC EDGAR client — the US analog of the Bursa announcement feed (PLAN §7.1).

klse_client gives Bursa counters their filings: what was actually FILED, in
what category, with dates. US counters had no equivalent, so every US
candidate reached the board with price data and nothing else — no catalyst
trail, no sector, no answer to §7.1's question ("was there a contract win
inside the base?").

EDGAR answers it better than any news API can, because it IS the primary
source those articles are written from, and it is free and unauthenticated.
Three things come out of one submissions request per counter:

  1. FILINGS with categories. 8-K item codes are already structured
     ("1.01,9.01"), so the category is READ, not keyword-guessed — a stronger
     version of klse_client.classify(), which has to regex Bursa titles. The
     labels deliberately match that taxonomy (contract / results / dilution /
     insider_dealing / capital) so the AI prompt, the UI and counter_news all
     work unchanged, plus US-specific hazards that have no Bursa analog.
  2. SIC INDUSTRY. US candidates had no industry at all, which silently
     disabled group-RS ranking for the whole market.
  3. Company name and exchange.

SEC ETIQUETTE, non-negotiable: a User-Agent naming the caller and a contact
address (they throttle/block anonymous scrapers), and <=10 requests/second.
Both are enforced here rather than left to callers.

Nothing here touches the database or interprets a filing's CONTENT: titles
and categories are UNTRUSTED third-party text under the §7 invariant — data
for the AI to read, never an input to a grade, bucket or signal.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import re
import time
import urllib.request

log = logging.getLogger(__name__)

CIK_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
TIMEOUT = 30
RETRIES = 3
MIN_INTERVAL = 0.11          # SEC fair-use ceiling is 10 req/s; stay under it

# Contact address is a REQUIREMENT, not politeness — SEC blocks UAs without
# one. Override per-deployment; the default names the project and its owner.
USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "sepa-board-v3 firdausfitri010199@gmail.com")

# 8-K item code -> category. Labels reuse klse_client's taxonomy wherever the
# concept exists on both exchanges, so downstream consumers need no branching.
ITEM_CATEGORIES = {
    "1.01": "contract",          # entry into a material definitive agreement
    "1.02": "contract",          # termination of one — same trail, opposite sign
    "1.03": "bankruptcy",
    "2.01": "acquisition",
    "2.02": "results",           # results of operations / earnings release
    "2.03": "capital",           # direct financial obligation created
    "2.04": "capital",
    "2.05": "restructuring",
    "2.06": "impairment",
    "3.01": "delisting_risk",    # listing-rule failure — no Bursa analog, matters
    "3.02": "dilution",          # unregistered equity sales
    "3.03": "dilution",
    "4.01": "auditor_change",
    "4.02": "restatement",       # non-reliance on prior financials
    "5.01": "control_change",
    "5.02": "management",        # director/officer departure or election
    "5.07": "agm",
    "7.01": "disclosure",
    "8.01": "other_event",
}

# Form type -> category, for everything that is not an 8-K. Form 4 and
# SC 13D/G are the direct analog of Bursa's "changes in substantial
# shareholder" filings, which the board already treats as sponsorship signal.
FORM_CATEGORIES = {
    "10-Q": "results", "10-K": "results", "20-F": "results", "40-F": "results",
    "4": "insider_dealing", "3": "insider_dealing", "5": "insider_dealing",
    "SC 13D": "insider_dealing", "SC 13D/A": "insider_dealing",
    "SC 13G": "insider_dealing", "SC 13G/A": "insider_dealing",
    "144": "insider_selling",
    "S-1": "dilution", "S-3": "dilution", "S-3ASR": "dilution",
    "424B5": "dilution", "424B3": "dilution", "424B4": "dilution",
    "DEF 14A": "agm", "8-A12B": "listing",
}

ITEM_LABELS = {
    "1.01": "Entered a material definitive agreement",
    "1.02": "Terminated a material definitive agreement",
    "1.03": "Bankruptcy or receivership",
    "2.01": "Completed an acquisition or disposition",
    "2.02": "Results of operations (earnings release)",
    "2.03": "Created a direct financial obligation",
    "2.04": "Triggering event accelerating an obligation",
    "2.05": "Exit or disposal costs",
    "2.06": "Material impairment",
    "3.01": "Delisting notice / listing-rule failure",
    "3.02": "Unregistered sale of equity securities",
    "3.03": "Material modification to shareholder rights",
    "4.01": "Changed certifying accountant",
    "4.02": "Non-reliance on previously issued financials",
    "5.01": "Change in control",
    "5.02": "Director/officer departure or appointment",
    "5.07": "Shareholder vote results",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other event",
}

# Filings that carry no standalone meaning — 9.01 is the exhibit index that
# rides along with a real item, and listing it alone would be noise.
IGNORED_ITEMS = {"9.01"}

_last_call = 0.0
_cik_cache: dict[str, int] | None = None
# company_info() and filings() read the SAME submissions document; without
# this every counter costs two identical requests.
_submissions_cache: dict[int, dict] = {}


class EdgarUnavailable(RuntimeError):
    """EDGAR has no data for this request, or refused it. Callers degrade —
    a US counter without filings is worse-informed, never wrong."""


def _get(url: str) -> dict:
    """Throttled, gzip-aware GET. Retries transport/5xx, never 404 — an
    unmapped ticker will not become mapped by asking again."""
    global _last_call
    last = None
    for attempt in range(RETRIES):
        gap = time.monotonic() - _last_call
        if gap < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - gap)
        _last_call = time.monotonic()
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT,
                              "Accept-Encoding": "gzip, deflate"})
            resp = urllib.request.urlopen(req, timeout=TIMEOUT)
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return json.loads(raw)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise EdgarUnavailable(f"404 {url}") from e
            last = f"HTTP {e.code}"
        except Exception as e:
            last = str(e)
        if attempt < RETRIES - 1:
            time.sleep(2 ** attempt)
    raise EdgarUnavailable(f"{url} failed after {RETRIES} attempts: {last}")


def _submissions(cik: int) -> dict:
    """One submissions document per CIK per process."""
    if cik not in _submissions_cache:
        _submissions_cache[cik] = _get(SUBMISSIONS_URL.format(cik=cik))
    return _submissions_cache[cik]


def cik_map(refresh: bool = False) -> dict[str, int]:
    """ticker -> CIK for every SEC registrant (~10k). Held in memory for the
    process: one 1MB file serves a whole scan, and it changes on the scale of
    IPOs, not sessions."""
    global _cik_cache
    if _cik_cache is None or refresh:
        data = _get(CIK_MAP_URL)
        _cik_cache = {v["ticker"].upper(): int(v["cik_str"])
                      for v in data.values() if v.get("ticker")}
        log.info("EDGAR CIK map: %d tickers", len(_cik_cache))
    return _cik_cache


def cik_of(ticker: str) -> int | None:
    """Internal ticker -> CIK. US tickers only; `.KL` names are Bursa and have
    no SEC registration, so they resolve to None rather than raising."""
    if ticker.endswith(".KL"):
        return None
    # class shares differ between vendors: EODHD/board use BRK-B, SEC uses BRK-B
    # too, but some feeds emit BRK.B — normalise before lookup.
    key = ticker.upper().replace(".", "-")
    return cik_map().get(key)


def _title_for(form: str, items: str) -> str:
    """Human-readable filing title. For an 8-K the ITEM CODES carry the
    meaning — '8-K' alone tells a reader nothing, while '8-K: Entered a
    material definitive agreement' is the contract-win signal itself."""
    codes = [c.strip() for c in (items or "").split(",") if c.strip()]
    codes = [c for c in codes if c not in IGNORED_ITEMS]
    labels = [ITEM_LABELS[c] for c in codes if c in ITEM_LABELS]
    if labels:
        return f"{form}: " + "; ".join(labels)
    return {"10-Q": "10-Q Quarterly report", "10-K": "10-K Annual report",
            "4": "Form 4 Insider transaction", "144": "Form 144 Proposed insider sale",
            }.get(form, form)


def _category_for(form: str, items: str) -> str:
    """Category from item codes first (8-K), else form type. Deterministic and
    auditable — the AI receives the label, it never chooses it (same contract
    as klse_client.classify)."""
    for code in (c.strip() for c in (items or "").split(",")):
        if code in ITEM_CATEGORIES:
            return ITEM_CATEGORIES[code]
    return FORM_CATEGORIES.get(form, "other")


def company_info(ticker: str) -> dict | None:
    """Name, SIC industry and exchange for one US ticker.

    The industry is the load-bearing field: without it every US candidate has
    a NULL sector, which disables group-RS ranking across the whole market.
    """
    cik = cik_of(ticker)
    if cik is None:
        return None
    try:
        d = _submissions(cik)
    except EdgarUnavailable as e:
        log.info("EDGAR company_info %s: %s", ticker, e)
        return None
    exch = d.get("exchanges") or []
    return {
        "name": d.get("name"),
        "industry": d.get("sicDescription"),
        # SIC has no sector layer; the industry string is what the board shows
        # and what group-RS buckets on. Never invent a sector we do not have.
        "sector": d.get("sicDescription"),
        "exchange": exch[0] if exch else None,
        "cik": cik,
    }


def filings(ticker: str, limit: int = 30, forms: set[str] | None = None) -> list[dict]:
    """Recent filings, shaped exactly like klse_client.announcements_feed's
    items so db.save_counter_news stores them unchanged.

    The accession number is the item_id: SEC-unique, immutable, and already
    the dedupe key EDGAR itself uses.
    """
    cik = cik_of(ticker)
    if cik is None:
        raise EdgarUnavailable(f"{ticker} has no CIK (not an SEC registrant)")
    d = _submissions(cik)
    rec = (d.get("filings") or {}).get("recent") or {}
    accs = rec.get("accessionNumber") or []
    if not accs:
        return []

    out = []
    for i in range(len(accs)):
        form = (rec.get("form") or [""] * len(accs))[i]
        if forms and form not in forms:
            continue
        items = (rec.get("items") or [""] * len(accs))[i]
        acc = accs[i]
        doc = (rec.get("primaryDocument") or [""] * len(accs))[i]
        out.append({
            "item_id": acc,
            "title": _title_for(form, items),
            "url": (f"https://www.sec.gov/Archives/edgar/data/{cik}/"
                    f"{acc.replace('-', '')}/{doc}" if doc else
                    f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"),
            "source": "",          # filings have no publisher, same as Bursa
            "category": _category_for(form, items),
            "date": (rec.get("filingDate") or [""] * len(accs))[i],
            "form": form,
        })
        if len(out) >= limit:
            break
    return out


# Filings a trader actually needs; Form 4 floods (dozens a week on some
# counters) would otherwise crowd out the material events.
MATERIAL_FORMS = {"8-K", "10-Q", "10-K", "SC 13D", "SC 13D/A", "SC 13G",
                  "SC 13G/A", "424B5", "S-3", "S-3ASR", "DEF 14A", "20-F"}


def material_filings(ticker: str, limit: int = 30) -> list[dict]:
    """The §7.1 view: material events only, insider-transaction noise dropped."""
    return filings(ticker, limit=limit, forms=MATERIAL_FORMS)
