"""EODHD data client — the single source of price truth for US + Bursa.

Replaces v2's moomoo OpenD funnel (US-only, 300-kline-per-7-days quota, VPS
required) and v1's yfinance downloads (Bursa bars served a full day late).
One vendor, two exchanges, no VPS.

Three functions, per PLAN §3.2: `bulk_eod` (one call = a whole exchange's
session), `history` (per-ticker depth, yfinance-shaped so the ported
indicators run unchanged), `symbols` (directory incl. delisted, which is what
makes the backtest survivorship fix possible).

TICKER FORMATS — mapped ONLY here, at the vendor boundary. Everything inside
the scanner, the DB and the web app speaks v2's format forever:
    internal  AAPL        <->  vendor  AAPL.US
    internal  1155.KL     <->  vendor  1155.KLSE
Leaking a vendor code past this module is a bug: it would fork the ticker
namespace between candles, candidates and signal_outcomes, and receipts are
keyed on ticker (PLAN §6 — history must survive forever).

ADJUSTED PRICES: EODHD returns raw OHLC plus `adjusted_close`. We scale OHLC
by `adjusted_close / close` so a split never puts a cliff in a moving average
(un-adjusted stitching corrupts every MA — PLAN §3.3 calls that a P0 bug).
Volume is scaled by the same factor so dollar-volume stays continuous across
splits. That factor also carries dividend adjustments, which nudges historical
volume by a few percent a year — knowingly accepted: a few percent cannot flip
the 1.4x breakout-volume test, while a missed 4:1 split would be a 4x error.
"""
from __future__ import annotations

import logging
import os
import time

import pandas as pd
import requests

log = logging.getLogger(__name__)

BASE = "https://eodhd.com/api"
TIMEOUT = 60
RETRIES = 3

# market -> vendor exchange code
EXCHANGES = {"US": "US", "MY": "KLSE"}

# Instruments that are not `SYMBOL.EXCHANGE` stocks. Resolved in Phase 0:
# GSPC.INDX exists; there is NO raw KLCI index in the catalog, so the MY
# regime reads the KLCI ETF's PRICE and takes its VOLUME signal from
# aggregate exchange turnover instead (PLAN §12 Phase 0 result (e)).
INDEX_CODES = {"^GSPC": "GSPC.INDX"}


class DataUnavailable(RuntimeError):
    """Vendor has no data for this request. Callers must fail loudly, never
    silently scan on stale prices (PLAN §3.2 freshness gate)."""


def _token() -> str:
    tok = os.environ.get("EODHD_API_TOKEN")
    if not tok:
        raise RuntimeError("EODHD_API_TOKEN is not set")
    return tok


def to_vendor(ticker: str) -> str:
    """Internal ticker -> vendor code. `1155.KL` -> `1155.KLSE`, `AAPL` -> `AAPL.US`."""
    if ticker in INDEX_CODES:
        return INDEX_CODES[ticker]
    if ticker.endswith(".KL"):
        return ticker[:-3] + ".KLSE"
    return ticker if "." in ticker else ticker + ".US"


def to_internal(code: str, exchange: str) -> str:
    """Vendor code (or a bare bulk-response symbol) -> internal ticker."""
    base = code.split(".", 1)[0]
    return base + ".KL" if exchange == "KLSE" else base


def _get(path: str, **params) -> list | dict:
    """GET with backoff on 5xx and transport errors. 4xx is not retried — a
    bad symbol or a plan-tier refusal will not fix itself, and retrying it
    burns the daily call budget."""
    params.update(api_token=_token(), fmt="json")
    url = f"{BASE}/{path}"
    last = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT)
            if r.status_code == 404:
                raise DataUnavailable(f"404 {path}")
            if r.status_code < 500:
                r.raise_for_status()
                return r.json()
            last = f"HTTP {r.status_code}"
        except DataUnavailable:
            raise
        except Exception as e:  # transport, JSON, 4xx
            if isinstance(e, requests.HTTPError) and e.response is not None \
                    and e.response.status_code < 500:
                raise
            last = str(e)
        if attempt < RETRIES - 1:
            time.sleep(2 ** attempt)
    raise DataUnavailable(f"{path} failed after {RETRIES} attempts: {last}")


def _adjust(df: pd.DataFrame) -> pd.DataFrame:
    """Scale OHLCV by adjusted_close/close. See the module docstring."""
    close = pd.to_numeric(df["Close"], errors="coerce")
    adj = pd.to_numeric(df["AdjClose"], errors="coerce")
    factor = (adj / close).where(close > 0).fillna(1.0)
    for col in ("Open", "High", "Low", "Close"):
        df[col] = pd.to_numeric(df[col], errors="coerce") * factor
    # volume in post-split share terms, so dollar-volume is continuous
    df["Volume"] = (pd.to_numeric(df["Volume"], errors="coerce") / factor).round()
    return df.drop(columns=["AdjClose"])


def bulk_eod(exchange: str, date: str | None = None) -> pd.DataFrame:
    """One call = every symbol's session on `exchange`. ~100 call-units.

    Returns columns: ticker (INTERNAL format), d (date), o/h/l/c, v.

    NOTE (Phase 0 finding (b)): the bulk response contains only counters that
    actually TRADED that day — 937 of 1,073 KLSE symbols on the probe date.
    Thin ACE counters legitimately skip sessions, so a missing ticker is NOT
    evidence of stale data. The freshness gate keys on the response's modal
    bar date, never on any single symbol.
    """
    params = {}
    if date:
        params["date"] = date
    rows = _get(f"eod-bulk-last-day/{exchange}", **params)
    if not rows:
        raise DataUnavailable(f"bulk EOD {exchange} {date or 'latest'} returned nothing")

    df = pd.DataFrame(rows)
    missing = {"code", "date", "open", "high", "low", "close", "volume"} - set(df.columns)
    if missing:
        raise DataUnavailable(f"bulk EOD {exchange} missing columns: {sorted(missing)}")

    out = pd.DataFrame({
        "ticker": [to_internal(c, exchange) for c in df["code"].astype(str)],
        "d": pd.to_datetime(df["date"]).dt.date,
        "o": pd.to_numeric(df["open"], errors="coerce"),
        "h": pd.to_numeric(df["high"], errors="coerce"),
        "l": pd.to_numeric(df["low"], errors="coerce"),
        "c": pd.to_numeric(df["close"], errors="coerce"),
        "v": pd.to_numeric(df["volume"], errors="coerce"),
    })
    # A row without a close is unusable; a row without volume is a halted or
    # untraded name — both would poison the MAs they touch.
    out = out.dropna(subset=["c", "v"])
    out = out[out["c"] > 0]
    out["v"] = out["v"].astype("int64")
    log.info("bulk_eod %s: %d rows, modal bar date %s",
             exchange, len(out), out["d"].mode().iat[0] if len(out) else "—")
    return out.reset_index(drop=True)


def history(ticker: str, years: int = 2) -> pd.DataFrame:
    """Per-ticker daily history, shaped exactly like v2's `download_batch`
    output: columns Open/High/Low/Close/Volume, DatetimeIndex, oldest first.

    The ported indicators/patterns modules consume this shape untouched —
    that identity is Phase 1's acceptance criterion (PLAN §12).
    """
    start = (pd.Timestamp.utcnow().normalize() - pd.DateOffset(years=years)).date()
    rows = _get(f"eod/{to_vendor(ticker)}", period="d", **{"from": str(start)})
    if not rows:
        raise DataUnavailable(f"no history for {ticker}")

    df = pd.DataFrame(rows)
    df = pd.DataFrame({
        "Open": df["open"], "High": df["high"], "Low": df["low"],
        "Close": df["close"], "AdjClose": df.get("adjusted_close", df["close"]),
        "Volume": df["volume"],
    }).set_index(pd.DatetimeIndex(pd.to_datetime(df["date"])))
    df.index.name = "Date"
    df = _adjust(df).dropna(subset=["Close"]).sort_index()
    return df[~df.index.duplicated(keep="last")]


def symbols(exchange: str, include_delisted: bool = True) -> pd.DataFrame:
    """Exchange symbol directory. Columns: ticker (internal), name, type,
    delisted (bool).

    Common stock only — ETFs, funds, warrants, preferreds and rights are not
    SEPA candidates and would pollute the RS percentile pool.

    Delisted symbols are the point of paying for this: 58,735 of them on the
    US directory at Phase 0. Without them every backtest silently tests
    "stocks that still exist in 2026" (PLAN §9 upgrade A).
    """
    frames = []
    live = pd.DataFrame(_get(f"exchange-symbol-list/{exchange}"))
    live["delisted"] = False
    frames.append(live)
    if include_delisted:
        try:
            dead = pd.DataFrame(_get(f"exchange-symbol-list/{exchange}", delisted="1"))
            if not dead.empty:
                dead["delisted"] = True
                frames.append(dead)
        except DataUnavailable as e:
            log.warning("delisted directory unavailable for %s: %s", exchange, e)

    df = pd.concat(frames, ignore_index=True)
    if "Type" in df.columns:
        df = df[df["Type"].astype(str).str.lower() == "common stock"]
    out = pd.DataFrame({
        "ticker": [to_internal(c, exchange) for c in df["Code"].astype(str)],
        "name": df.get("Name", pd.Series(dtype=str)).astype(str),
        "type": df.get("Type", pd.Series(dtype=str)).astype(str),
        "delisted": df["delisted"].astype(bool),
    }).drop_duplicates(subset=["ticker"], keep="first")
    log.info("symbols %s: %d common stocks (%d delisted)",
             exchange, len(out), int(out["delisted"].sum()))
    return out.reset_index(drop=True)


def splits(ticker: str, since: str) -> pd.DataFrame:
    """Split events since `since` (YYYY-MM-DD). Drives the warehouse re-pull
    that PLAN §3.3 mandates — a split inside the rolling window invalidates
    every stored bar for that ticker."""
    try:
        rows = _get(f"splits/{to_vendor(ticker)}", **{"from": since})
    except DataUnavailable:
        return pd.DataFrame(columns=["date", "split"])
    return pd.DataFrame(rows or [], columns=["date", "split"])
