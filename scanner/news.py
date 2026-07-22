"""Sector-rotation news via Yahoo Finance (yfinance) — the "why" behind moves.

US: headlines for the sector ETFs that lead or are rotating in/out.
MY: no sector ETFs exist, so the busiest Bursa sectors are explained through
their top-RS counters' headlines.
"""
from __future__ import annotations

import datetime as dt
import logging

import yfinance as yf

log = logging.getLogger(__name__)

MAX_HEADLINES = 3
MAX_AGE_DAYS = 14
MY_MAX_AGE_DAYS = 60  # Yahoo's Bursa coverage is sparse — allow older context


def _parse(items: list | None) -> list[dict]:
    """Normalize both yfinance news shapes (flat legacy and nested 'content')."""
    out = []
    for it in items or []:
        c = it.get("content") or it
        title = c.get("title")
        provider = c.get("provider")
        publisher = provider.get("displayName") if isinstance(provider, dict) else c.get("publisher")
        canonical = c.get("canonicalUrl")
        url = canonical.get("url") if isinstance(canonical, dict) else c.get("link")
        ts = c.get("pubDate") or c.get("providerPublishTime")
        if isinstance(ts, (int, float)):
            ts = dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")
        elif isinstance(ts, str):
            ts = ts[:10]
        else:
            ts = None
        if title and url:
            out.append({"title": title, "publisher": publisher, "url": url, "date": ts})
    return out


def _fresh(headlines: list[dict], max_age_days: int = MAX_AGE_DAYS) -> list[dict]:
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=max_age_days)).strftime("%Y-%m-%d")
    return [h for h in headlines if (h.get("date") or "") >= cutoff]


def _ticker_news(sym: str) -> list[dict]:
    try:
        return _parse(yf.Ticker(sym).news)
    except Exception as e:  # news is a nice-to-have; never fail the scan over it
        log.info("news fetch failed for %s: %s", sym, e)
        return []


def sector_rotation_news(sector_rows: list[dict], candidates: list[dict]) -> list[dict]:
    """[{market, sector, etf?, direction: leading|in|out, headlines: [...]}]"""
    out = []

    # US: every sector that's rotating (in or out) or ranked top-3
    for s in sector_rows:
        direction = ("in" if s.get("rotating_in") else
                     "out" if s.get("rotating_out") else
                     "leading" if s.get("rank", 99) <= 3 else None)
        if not direction:
            continue
        heads = _fresh(_ticker_news(s["etf"]))[:MAX_HEADLINES]
        if heads:
            out.append({"market": "US", "sector": s["sector"], "etf": s["etf"],
                        "direction": direction, "headlines": heads})

    # MY: top-3 sectors by candidate count, via their 2 strongest counters
    by_sector: dict[str, list[dict]] = {}
    for c in candidates:
        if c.get("market") == "MY" and c.get("sector"):
            by_sector.setdefault(c["sector"], []).append(c)
    for sec, cands in sorted(by_sector.items(), key=lambda kv: len(kv[1]), reverse=True)[:3]:
        top = sorted(cands, key=lambda c: c.get("rs_rank") or 0, reverse=True)[:2]
        heads: list[dict] = []
        for c in top:
            heads += _fresh(_ticker_news(c["ticker"]), MY_MAX_AGE_DAYS)
        seen, uniq = set(), []
        for h in sorted(heads, key=lambda h: h.get("date") or "", reverse=True):
            if h["url"] not in seen:
                seen.add(h["url"])
                uniq.append(h)
        if uniq:
            out.append({"market": "MY", "sector": sec, "etf": None,
                        "direction": "leading", "headlines": uniq[:MAX_HEADLINES]})
    return out
