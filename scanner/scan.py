"""Nightly scan entry point — US market, data from moomoo OpenD.

session check -> moomoo server-side funnel (FREE) -> kline for survivors
-> RS ranks -> trend template -> buckets (swing / position / watchlist /
forming) -> patterns, setup progress, S/R, targets, reasoning -> institutional
sponsorship + whale capital flow -> save to Postgres.

Requires OpenD running (see moomoo_client). Runs on the VPS, NOT GitHub
Actions, because OpenD must be reachable.

Run: python -m scanner.scan   (env: DATABASE_URL, OPEND_HOST/OPEND_PORT,
MM_* funnel knobs, SCAN_FORCE, SCAN_MIN_ADR)
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import sys
import time

import pandas as pd
import pandas_market_calendars as mcal
# yfinance is kept ONLY for next-earnings dates: moomoo's earnings calendar
# needs a market-wide query with fiddly enums, while this is a free per-ticker
# lookup with no quota cost. All price/fundamental data comes from moomoo.
import yfinance as yf
from moomoo import Market as mm_Market, RET_OK

from . import (db, fundamentals, indicators, moomoo_client as mc, news, patterns,
               performance, reasoning, sectors)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scan")

MIN_ADR_PCT = float(os.environ.get("SCAN_MIN_ADR", 2.5))
CHUNK = 200

# US only. Bursa was dropped when the data source moved to moomoo OpenD, which
# does not serve Malaysian market data at all (its Authority table lists MY as
# Unsupported, and a live probe returned "no permission" on every MY call).
MARKETS = {
    "US": {
        "calendar": "NYSE",
        "indices": ["SPY", "QQQ"],
        "currency": "$",
        "min_adr": MIN_ADR_PCT,
    },
}

CAPS = {"swing": 20, "position": 30, "watchlist": 20, "forming": 15}


def session_today(calendar_name: str) -> str | None:
    cal = mcal.get_calendar(calendar_name)
    today = dt.datetime.now(dt.timezone.utc).date()
    sched = cal.schedule(start_date=today - dt.timedelta(days=7), end_date=today)
    if sched.empty:
        return None
    last = sched.index[-1].date()
    return last.isoformat() if last == today else None


def mc_sym(code: str) -> str:
    """'US.NVDA' -> 'NVDA' so tickers match the existing DB/web format."""
    return code.split(".", 1)[1] if "." in code else code


def download_batch(tickers: list[str], period: str = "2y", ctx=None) -> dict[str, pd.DataFrame]:
    """moomoo-backed replacement for the old yfinance bulk download.

    Costs 1 kline quota per unique ticker per 7 days. Used for sector ETFs and
    open journal positions; the main funnel pulls its own history.
    """
    years = 2 if period == "2y" else 1
    out: dict[str, pd.DataFrame] = {}
    own_ctx = ctx is None
    if own_ctx:
        cm = mc.quote_ctx()
        ctx = cm.__enter__()
    try:
        for t in tickers:
            code = t if "." in t else "US." + t
            df = mc.history(ctx, code, years=years)
            if df is not None and len(df) >= indicators.MIN_BARS:
                out[mc_sym(code)] = df
    finally:
        if own_ctx:
            cm.__exit__(None, None, None)
    return out


def distribution_days(df: pd.DataFrame, window: int = 25) -> int | None:
    """O'Neil: down >=0.2% on volume higher than the prior day = institutions
    selling. 5-6 of these inside ~25 sessions usually precedes a correction.

    A dist day also EXPIRES once the index rallies 5%+ above that day's close
    (O'Neil's removal rule) — otherwise a market that already recovered keeps
    carrying stale sell signals.
    """
    d = df.iloc[-(window + 1):]
    closes, vols = d["Close"].values, d["Volume"].values
    if len(closes) < window or float(vols[1:].max() or 0) <= 0:
        return None  # some indices (e.g. ^KLSE) report no usable volume
    current = float(closes[-1])
    return int(sum(
        1 for i in range(1, len(closes))
        if closes[i] < closes[i - 1] * 0.998 and vols[i] > vols[i - 1] > 0
        and current < closes[i] * 1.05  # not yet rallied away from it
    ))


def follow_through_day(df: pd.DataFrame, lookback: int = 40) -> dict | None:
    """O'Neil follow-through day: after a real decline (6%+ off the high), a
    1.5%+ up day on rising volume on day 4+ of a rally attempt — the classic
    signal that a correction is over and a new uptrend is being attempted."""
    d = df.iloc[-lookback:]
    closes, vols = d["Close"], d["Volume"]
    low_i = int(closes.values.argmin())
    if low_i < 5 or low_i >= len(d) - 3:
        return None
    prior_high = float(closes.iloc[:low_i].max())
    if float(closes.iloc[low_i]) > prior_high * 0.94:
        return None  # never really corrected — an FTD means nothing
    for i in range(low_i + 3, len(d)):
        chg = float(closes.iloc[i] / closes.iloc[i - 1] - 1)
        if chg >= 0.015 and float(vols.iloc[i]) > float(vols.iloc[i - 1]) > 0:
            return {"date": d.index[i].strftime("%Y-%m-%d"), "pct": round(chg * 100, 1),
                    "day_of_rally": i - low_i + 1, "recent": i >= len(d) - 5}
    return None


def market_regime(index_data: dict[str, pd.DataFrame], indices: list[str]) -> dict:
    def health(df: pd.DataFrame) -> dict:
        price = float(df["Close"].iloc[-1])
        ma50 = float(df["Close"].rolling(50).mean().iloc[-1])
        ma200 = float(df["Close"].rolling(200).mean().iloc[-1])
        return {"price": round(price, 2), "ma50": round(ma50, 2), "ma200": round(ma200, 2),
                "above_50": price > ma50, "above_200": price > ma200,
                "dist_days": distribution_days(df),
                "follow_through": follow_through_day(df)}

    per_index, score, checks = {}, 0, 0
    for ix in indices:
        if ix in index_data:
            h = health(index_data[ix])
            per_index[ix] = h
            score += int(h["above_50"]) + int(h["above_200"])
            checks += 2
    if checks == 0:
        return {"light": "yellow", "note": "index data unavailable", "indices": {},
                "exposure": {"risk_pct": 0.5, "rule": "Half size — regime unknown (no index data)"}}
    light = "green" if score == checks else ("yellow" if score >= checks / 2 else "red")
    # heavy distribution overrides MA health: institutions selling into strength
    dist_max = max((h.get("dist_days") or 0) for h in per_index.values())
    note = {"green": "Confirmed uptrend", "yellow": "Caution — mixed signals",
            "red": "Downtrend — consider staying in cash"}[light]
    if dist_max >= 6:
        light = {"green": "yellow", "yellow": "red", "red": "red"}[light]
        note = {"yellow": "Caution — heavy distribution despite healthy trend",
                "red": "Downtrend/heavy distribution — consider staying in cash"}[light]
    # a fresh follow-through day is the O'Neil "all clear" after a correction:
    # upgrade a red light one notch (still cautious — FTDs fail too)
    ft_recent = any((h.get("follow_through") or {}).get("recent") for h in per_index.values())
    if light == "red" and ft_recent and dist_max < 6:
        light = "yellow"
        note = "Follow-through day — new uptrend attempt; start small, let it prove itself"
    # exposure ladder: the light as an explicit sizing RULE, not just a color
    exposure = {
        "green": {"risk_pct": 1.0, "rule": "Full size — 1% risk per trade, all buckets open"},
        "yellow": {"risk_pct": 0.5, "rule": "Half size — 0.5% risk, skip forming-bucket entries"},
        "red": {"risk_pct": 0.0, "rule": "No new entries — manage exits only"},
    }[light]
    return {"light": light, "indices": per_index, "note": note, "exposure": exposure}


def market_breadth(liquid: dict[str, pd.DataFrame]) -> dict | None:
    """Breadth across the whole liquid universe — indices can mask rot: when
    3 mega-caps hold SPY up while 60% of stocks sit under their 200-day MA,
    breakouts fail. New 52-week highs vs lows is the same tell."""
    above200 = above50 = highs = lows = counted = 0
    for df in liquid.values():
        if len(df) < 200:
            continue
        close = df["Close"]
        price = float(close.iloc[-1])
        counted += 1
        above200 += price > float(close.rolling(200).mean().iloc[-1])
        above50 += price > float(close.rolling(50).mean().iloc[-1])
        if price >= float(df["High"].iloc[-252:].max()) * 0.998:
            highs += 1
        if price <= float(df["Low"].iloc[-252:].min()) * 1.002:
            lows += 1
    if not counted:
        return None
    return {"pct_above_200ma": round(above200 / counted * 100),
            "pct_above_50ma": round(above50 / counted * 100),
            "new_highs": highs, "new_lows": lows, "universe": counted}


def bucket_candidate(df: pd.DataFrame, vcp: dict, ext: dict) -> str:
    price = float(df["Close"].iloc[-1])
    pivot = vcp.get("pivot")
    vol_today = float(df["Volume"].iloc[-1])
    # baseline = the PRIOR 50 days: today's own surge must not inflate the
    # average it's being measured against (a true 2x day read as ~1.96x)
    vol50 = float(df["Volume"].iloc[-51:-1].mean()) if len(df) >= 51 else 0.0
    breakout_today = pivot and price >= pivot and vol50 > 0 and vol_today > 1.4 * vol50
    near_pivot = pivot and abs(price / pivot - 1) <= 0.05
    if vcp.get("vcp"):
        if (breakout_today or near_pivot) and not ext["extended"]:
            return "swing"
        return "watchlist"  # valid VCP but price not at the buy point yet
    if len(vcp.get("contractions_pct") or []) >= 2:
        return "watchlist"  # base building: contractions found, dry-up/pivot pending
    return "position"


def _with_tactic_markers(pat: dict, df: pd.DataFrame, setup: dict) -> dict:
    """Every dated event the 'Why it's on the board' text talks about gets a
    chart marker — the chart should show what the words claim."""
    markers = pat.setdefault("chart_markers", [])
    last_t = df.index[-1].strftime("%Y-%m-%d")
    if setup.get("pocket_pivot"):
        markers.append({"t": last_t, "position": "belowBar",
                        "shape": "arrowUp", "text": "pocket pivot"})
    for key, label in (("ma20_bounce", "20MA"), ("ma50_bounce", "50MA")):
        b = setup.get(key)
        if not b:
            continue
        if b.get("tag_t"):
            markers.append({"t": b["tag_t"], "position": "belowBar",
                            "shape": "circle", "text": f"{label} tag"})
        markers.append({"t": last_t, "position": "belowBar",
                        "shape": "arrowUp", "text": f"{label} bounce"})
    if setup.get("episodic_pivot"):
        markers.append({"t": last_t, "position": "belowBar",
                        "shape": "arrowUp", "text": "episodic pivot"})
    if setup.get("momentum_burst"):
        markers.append({"t": last_t, "position": "belowBar",
                        "shape": "arrowUp", "text": "4% burst"})
    if setup.get("buyable_gap_up"):
        markers.append({"t": last_t, "position": "belowBar",
                        "shape": "arrowUp", "text": "buyable gap-up"})
    return pat


def build_candidate(t: str, df: pd.DataFrame, rank: int, tt: dict, market: str,
                    mcfg: dict) -> dict:
    vcp = indicators.detect_vcp(df)
    price = float(df["Close"].iloc[-1])
    pivot = vcp.get("pivot")
    # a "pivot" more than 20% overhead is a stale swing high from a prior
    # run-up, not a buyable base top — price would have to rally 25%+ just to
    # reach the buy point. Treating it as the entry poisoned every downstream
    # number (the 8%-of-entry stop floor even landed ABOVE the market price).
    if pivot and price < pivot * 0.80:
        pivot = None
    ext = indicators.extension_flags(df, pivot)
    entry = pivot or price
    adr = indicators.adr_pct(df)
    quality = indicators.quality_score(df, vcp)
    passed, failed = indicators.rule_results(tt)

    if tt["pass_all"]:
        bucket = bucket_candidate(df, vcp, ext)
        if bucket == "swing" and adr < mcfg["min_adr"]:
            bucket = "watchlist"
    else:
        bucket = "forming"

    early = indicators.early_entry(df, pivot)
    # Zanger's breakout-volume rule: today's volume vs the PRIOR 50 days'
    # average (excluding today, which would dilute its own baseline)
    vol50 = float(df["Volume"].iloc[-51:-1].mean()) if len(df) >= 51 else 0.0
    vol_ratio = round(float(df["Volume"].iloc[-1]) / vol50, 2) if vol50 > 0 else None
    stop_val = indicators.suggested_stop(df, entry)
    # stop quality: a stop inside the daily range gets hit by noise, not by
    # being wrong — 1.5x+ ATR distance is beyond normal wiggle
    atr = indicators._atr(df)
    stop_atr = (round((entry - stop_val) / atr, 2)
                if stop_val and atr and atr > 0 and entry > stop_val else None)
    # buyable gap-up (O'Neil/Kacher): full gap out of a base on 2x+ volume with
    # a strong close — institutional urgency; a VALID entry, not "extended"
    gap_up = None
    if pivot and len(df) >= 2:
        last, prev = df.iloc[-1], df.iloc[-2]
        if (float(last["Low"]) > float(prev["High"]) and (vol_ratio or 0) >= 2
                and float(last["Close"]) > float(last["Open"])
                and float(last["Close"]) >= pivot * 0.98):
            gap_up = {"gap_pct": round((float(last["Open"]) / float(prev["Close"]) - 1) * 100, 1),
                      "stop": round(float(last["Low"]), 2), "vol_ratio": vol_ratio}
    pat = patterns.analyze(df)
    warnings = indicators.setup_warnings(df, pivot, tt.get("checks"), pat.get("volume"))
    rules_total = len(tt["checks"])  # 8 full template; fewer on the IPO path
    setup = {
        "early_entry": early,
        "vol_ratio_today": vol_ratio,
        "stop_atr_ratio": stop_atr,
        "buyable_gap_up": gap_up,
        "rules_passed": passed, "rules_total": rules_total,
        "progress_pct": int(round(passed / rules_total * 100)) if rules_total else 0,
        "failed_rules": failed,
        "ipo": bool(tt.get("ipo")),
        "needs": indicators.what_needs_to_happen(tt, price),
        "pocket_pivot": indicators.pocket_pivot(df),
        # pullback-bounce entries only mean something in a confirmed uptrend
        "ma20_bounce": indicators.ma20_bounce(df) if tt["pass_all"] else None,
        "ma50_bounce": indicators.ma50_bounce(df) if tt["pass_all"] else None,
        # EP fires on neglect — deliberately NOT trend-gated
        "episodic_pivot": indicators.episodic_pivot(df),
        "base_count": indicators.base_count(df),
        "warnings": warnings,
        **indicators.tightening_now(df),
    }
    # an EP day trivially satisfies the burst conditions — keep the stronger label
    setup["momentum_burst"] = (None if setup["episodic_pivot"]
                               else indicators.momentum_burst(df))
    setup["anticipation"] = indicators.anticipation(
        vcp, setup.get("tightening", False), price, pivot)
    return {
        "ticker": t, "market": market, "price": round(price, 2), "rs_rank": rank,
        "pivot": pivot, "stop": stop_val,
        "bucket": bucket, "adr_pct": adr, "quality": quality,
        "extended": ext["extended"], "checks": tt["checks"],
        "vcp": vcp, "extension": ext, "sector": None, "setup": setup,
        "patterns": _with_tactic_markers(pat, df, setup),
        "levels": indicators.support_resistance(df),
    }


def scan_market(market: str, mcfg: dict, ctx=None) -> tuple[list[dict], dict, dict, dict]:
    """Returns (candidates, regime, ranks, price_data) for the US market.

    Data comes from moomoo OpenD. The server-side funnel (screener + snapshot,
    both quota-FREE) does price/liquidity/52-week filtering and a proxy trend
    template, so we only spend historical-kline quota on survivors.

    RS CAVEAT: previously RS was a percentile across the whole downloaded
    universe (~1,800 names) specifically to avoid pre-filter bias. moomoo's
    kline quota (300 stocks / 7 days) makes that impossible, so RS here is a
    percentile WITHIN the funnel set. It is a relative sort among already-strong
    stocks, not a market-wide RS rank — `rs_pool` is stored so the UI can say so.
    """
    snap = mc.screen_us(ctx)
    if snap.empty:
        log.error("[%s] moomoo funnel returned nothing", market)
        return [], {}, {}, {}

    codes = list(snap["code"])
    log.info("[%s] pulling history for %d funnel survivors", market, len(codes))
    klines = mc.histories(ctx, codes, years=2)
    data = {mc_sym(c): df for c, df in klines.items()}

    index_data = {}
    for ix in mcfg["indices"]:
        df = mc.history(ctx, "US." + ix, years=2)
        if df is not None:
            index_data[ix] = df

    raw = {t: r for t, df in data.items() if (r := indicators.rs_raw(df)) is not None}
    ranks = indicators.rs_ranks(raw)
    log.info("[%s] RS ranked %d of %d (funnel pool, not market-wide)",
             market, len(ranks), len(data))
    liquid = data

    candidates = []
    for t, df in liquid.items():
        rank = ranks.get(t)
        if not rank or rank < 55:  # 55+ considered; <70 can only reach "forming"
            continue
        tt = indicators.trend_template(df, rank)
        if not tt.get("eligible"):
            continue
        passed, _ = indicators.rule_results(tt)
        # near-miss "90% setups" kept — relative to the rule count actually
        # evaluated (IPO path checks fewer than 8)
        if tt["pass_all"] or passed >= len(tt["checks"]) - 2:
            candidates.append(build_candidate(t, df, rank, tt, market, mcfg))

    for c in candidates:
        c["rs_pool"] = len(ranks)
    candidates.sort(key=lambda c: (c["quality"] if c["bucket"] == "swing" else c["rs_rank"]),
                    reverse=True)
    capped, counts = [], {b: 0 for b in CAPS}
    for c in candidates:
        if counts[c["bucket"]] < CAPS[c["bucket"]]:
            counts[c["bucket"]] += 1
            capped.append(c)
    log.info("[%s] candidates: %s", market, counts)

    # carry the free snapshot metrics (52w distance, market cap) onto candidates
    smap = {mc_sym(r["code"]): r for _, r in snap.iterrows()}
    for c in capped:
        s = smap.get(c["ticker"])
        if s is not None:
            c["setup"]["pct_off_52w_high"] = round(float(s["pct_off_high"]), 2)
            mv = s.get("total_market_val")
            c["market_cap"] = None if pd.isna(mv) else float(mv)

    regime = market_regime(index_data, mcfg["indices"])
    # breadth from two FREE screener counts across the liquid universe — the
    # funnel set is strong by construction, so local breadth would read ~100%
    regime["breadth"] = mc.market_breadth(ctx)
    return capped, regime, ranks, {**liquid, **index_data}


def _earnings_info(t: str) -> dict | None:
    """Next earnings date via yfinance; high_risk when within 7 days — a
    breakout right before earnings can gap straight through the stop."""
    try:
        cal = yf.Ticker(t).calendar
        dates = (cal or {}).get("Earnings Date") if hasattr(cal, "get") else None
        if not dates:
            return None
        d0 = dates[0]
        # yfinance returns datetime/Timestamp here more often than date —
        # (datetime - date) raises TypeError, the bare except swallowed it,
        # and the "breakout right before earnings" flag silently never fired
        if hasattr(d0, "date") and not isinstance(d0, dt.date):
            d0 = d0.date()
        elif isinstance(d0, dt.datetime):
            d0 = d0.date()
        days = (d0 - dt.date.today()).days
        if days < -1:
            return None  # stale: last report already passed
        return {"date": d0.isoformat(), "days_away": days, "high_risk": days <= 7}
    except Exception:
        return None


def enrich(ctx, conn, candidates: list[dict], ranks_by_market: dict[str, dict]) -> None:
    """Sector/industry (moomoo plates, cached in ticker_meta) + group RS +
    fundamentals + the moomoo SEPA layer: institutional sponsorship and whale
    capital flow. US only — Bursa was dropped with the moomoo migration."""
    meta = db.load_ticker_meta(conn)
    fresh = {}

    def _meta_for(t: str) -> dict:
        """name + industry from moomoo (basicinfo + owner plate).

        get_owner_plate is rate-limited (10 calls / 30s); without the throttle
        most lookups failed silently and only ~25% of candidates got a sector.
        """
        code = "US." + t
        name = industry = None
        try:
            ret, info = ctx.get_stock_basicinfo(market=mm_Market.US, code_list=[code])
            if ret == RET_OK and len(info):
                name = info.iloc[0]["name"]
        except Exception as e:
            log.debug("basicinfo(%s): %s", t, e)
        for attempt in range(2):
            try:
                time.sleep(3.2)  # plate queries: 10 per 30s
                ret, plates = ctx.get_owner_plate([code])
                if ret == RET_OK and len(plates):
                    ind = plates[plates["plate_type"].astype(str).str.contains(
                        "INDUSTRY", case=False, na=False)]
                    row = (ind if len(ind) else plates).iloc[0]
                    industry = row["plate_name"]
                    break
                log.debug("owner_plate(%s) ret=%s %s", t, ret, str(plates)[:80])
            except Exception as e:
                log.debug("owner_plate(%s) attempt %d: %s", t, attempt, e)
        return {"industry": industry, "sector": industry, "name": name}

    for c in candidates:
        t = c["ticker"]
        m = meta.get(t) or {}
        if not m.get("name") or not m.get("industry"):
            m = _meta_for(t)
            if m.get("name"):
                fresh[t] = m
                meta[t] = m
        c["sector"] = m.get("sector")
        c["industry"] = m.get("industry")
        c["name"] = m.get("name")

    if fresh:
        db.save_ticker_meta(conn, fresh)

    group_rs_by_market = {
        mkt: indicators.industry_group_rs(
            ranks, {t: (meta.get(t) or {}).get("industry") for t in ranks})
        for mkt, ranks in ranks_by_market.items()
    }

    for c in candidates:
        code = "US." + c["ticker"]
        c["group_rs"] = group_rs_by_market.get(c["market"], {}).get(c.get("industry"))
        c["news"], c["earnings"] = [], _earnings_info(c["ticker"])
        c["fundamentals"] = fundamentals.fetch(c["ticker"])
        # --- the SEPA layer moomoo unlocks (O'Neil institutional sponsorship) ---
        c["setup"]["institutional"] = mc.institutional(ctx, code)
        c["setup"]["capital_flow"] = mc.capital_flow(ctx, code)
        entry = c.get("pivot") or c["price"]
        c["targets"] = reasoning.targets(entry, c["stop"]) if c.get("stop") else {}
        c["reasoning"] = reasoning.build(c)
        c["reasoning_sections"] = reasoning.build_sections(c)


def evaluate_open_positions(conn, run_date: str, data: dict) -> None:
    """Nightly exit-signal check + last-price update for open journal positions."""
    with conn.cursor() as cur:
        cur.execute("""SELECT id, ticker, entry_price, stop_price, pivot,
                              (CURRENT_DATE - entry_date) AS days_held
                       FROM positions WHERE status='open'""")
        rows = cur.fetchall()
    for pid, ticker, entry, stop, pivot, days_held in rows:
        df = data.get(ticker)
        if df is None:
            # download_batch, not raw yf.download: newer yfinance returns
            # (ticker, field) MultiIndex columns for single tickers, which
            # blew up exit_signals mid-run — download_batch normalizes it
            df = download_batch([ticker]).get(ticker)
        if df is None or len(df) < 60:
            continue
        sig = indicators.exit_signals(df, float(entry), float(stop),
                                      float(pivot) if pivot else None,
                                      days_held=int(days_held))
        price = round(float(df["Close"].iloc[-1]), 2)
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO position_signals (position_id, run_date, signals)
                   VALUES (%s,%s,%s)
                   ON CONFLICT (position_id, run_date) DO UPDATE SET signals = EXCLUDED.signals""",
                (pid, run_date, __import__("json").dumps(sig)),
            )
            cur.execute("UPDATE positions SET last_price = %s WHERE id = %s", (price, pid))
    conn.commit()


def main() -> int:
    enabled = [m.strip().upper() for m in os.environ.get("SCAN_MARKETS", "US").split(",")]
    # SCAN_FORCE=1 skips the trading-calendar check — for manual local runs on
    # weekends/holidays; data is simply the latest completed session's
    force = os.environ.get("SCAN_FORCE", "").lower() in ("1", "true", "yes")
    # DRY_RUN=1 runs the full pipeline but never writes to the database
    dry = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
    active = {m: MARKETS[m] for m in enabled
              if m in MARKETS and (force or session_today(MARKETS[m]["calendar"]))}
    if not active:
        log.info("No enabled market traded today — exiting.")
        return 0
    run_date = dt.datetime.now(dt.timezone.utc).date().isoformat()
    log.info("Scanning markets %s for %s%s", list(active), run_date,
             "  [DRY RUN]" if dry else "")

    all_candidates, regimes, ranks_by_market, all_data = [], {}, {}, {}
    with mc.quote_ctx() as ctx:
        log.info("kline quota at start: %s", mc.kline_quota(ctx))
        for market, mcfg in active.items():
            cands, regime, ranks, data = scan_market(market, mcfg, ctx)
            all_candidates += cands
            regimes[market] = regime
            ranks_by_market[market] = ranks
            all_data.update(data)

        if "SPY" not in all_data:
            log.error("Missing SPY data — aborting to avoid a garbage run.")
            return 1
        all_data.update(download_batch(list(sectors.SECTOR_ETFS), ctx=ctx))

        if dry:
            log.info("[DRY RUN] %d candidates; skipping enrich/save", len(all_candidates))
            for c in all_candidates[:12]:
                log.info("  %-6s %-10s RS %-3s Q %-4s %s",
                         c["ticker"], c["bucket"], c["rs_rank"], c.get("quality"),
                         c.get("sector") or "")
            log.info("kline quota at end: %s", mc.kline_quota(ctx))
            return 0

        conn = db.connect()
        db.apply_migrations(conn)
        enrich(ctx, conn, all_candidates, ranks_by_market)
        log.info("kline quota after run: %s", mc.kline_quota(ctx))

    # last ~130 daily bars for the dashboard chart
    for c in all_candidates:
        df = all_data.get(c["ticker"])
        if df is None:
            c["candles"] = []
            continue
        # MAs computed on the FULL history (a 200MA needs 200+ prior bars),
        # then sliced alongside the last 130 candles for the chart
        ma20 = df["Close"].rolling(20).mean()
        ma50 = df["Close"].rolling(50).mean()
        ma150 = df["Close"].rolling(150).mean()
        ma200 = df["Close"].rolling(200).mean()
        bb = indicators.bollinger_bands(df)
        # per-day PRIOR 50-day volume average (shifted: a day never sits inside
        # its own baseline) — lets the chart flag high-volume days honestly
        v50 = df["Volume"].rolling(50).mean().shift(1)
        d = df.iloc[-130:]
        # RS line: stock/index ratio normalized to 1.0 at the window start —
        # an RS line making new highs BEFORE price is institutional confirmation
        bench = all_data.get("SPY")
        rs_map = {}
        if bench is not None:
            ratio = (df["Close"] / bench["Close"].reindex(df.index).ffill()).iloc[-130:]
            base = float(ratio.iloc[0]) if pd.notna(ratio.iloc[0]) else None
            if base and base > 0:
                rs_map = {i: round(float(v) / base, 4) for i, v in ratio.items() if pd.notna(v)}
        c["candles"] = [
            {"t": i.strftime("%Y-%m-%d"), "o": round(float(r["Open"]), 2),
             "h": round(float(r["High"]), 2), "l": round(float(r["Low"]), 2),
             "c": round(float(r["Close"]), 2), "v": int(r["Volume"]),
             "m20": round(float(ma20.loc[i]), 2) if pd.notna(ma20.loc[i]) else None,
             "m50": round(float(ma50.loc[i]), 2) if pd.notna(ma50.loc[i]) else None,
             "m150": round(float(ma150.loc[i]), 2) if pd.notna(ma150.loc[i]) else None,
             "m200": round(float(ma200.loc[i]), 2) if pd.notna(ma200.loc[i]) else None,
             "rs": rs_map.get(i),
             "bbu": round(float(bb["upper"].loc[i]), 2) if pd.notna(bb["upper"].loc[i]) else None,
             "bbl": round(float(bb["lower"].loc[i]), 2) if pd.notna(bb["lower"].loc[i]) else None,
             "v50": int(v50.loc[i]) if pd.notna(v50.loc[i]) else None}
            for i, r in d.iterrows()
        ]

    sector_rows = (sectors.sector_rotation(all_data, all_data["SPY"])
                   if "SPY" in all_data else [])
    sector_news = news.sector_rotation_news(sector_rows, all_candidates)
    log.info("Sector news: %d sectors with headlines", len(sector_news))

    try:
        db.save_run(conn, run_date, regimes, all_candidates, sector_rows, sector_news)
        evaluate_open_positions(conn, run_date, all_data)
        performance.evaluate_and_store(conn, all_data, run_date)
    finally:
        conn.close()

    log.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
