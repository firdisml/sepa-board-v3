"""Core screening math: Minervini Trend Template, RS ranking, VCP detection.

All functions take a pandas DataFrame with columns: Open, High, Low, Close, Volume
indexed by date (daily bars, oldest first). Every check returns the actual
computed numbers so the dashboard can show WHY something passed or failed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN_BARS = 260      # full template: ~1 trading year + buffer (200MA + slope check)
MIN_BARS_IPO = 126  # young stocks/IPOs: 6 months is enough for the reduced template


def bollinger_bands(df: pd.DataFrame, window: int = 20, n_std: float = 2.0) -> pd.DataFrame:
    """Standard Bollinger Bands: 20-day SMA +/- 2 standard deviations.

    Returns a DataFrame (mid, upper, lower, bandwidth) indexed like df.
    Bandwidth = (upper-lower)/mid — a direct number for "squeeze" (bandwidth
    near a multi-month low = volatility coiled, a move is brewing) vs a
    "walk" (price rides the upper band tight through a strong trend).
    """
    mid = df["Close"].rolling(window).mean()
    std = df["Close"].rolling(window).std()
    upper = mid + n_std * std
    lower = mid - n_std * std
    bandwidth = (upper - lower) / mid
    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower, "bandwidth": bandwidth})


def moving_averages(df: pd.DataFrame) -> dict:
    close = df["Close"]
    return {
        "ma50": float(close.rolling(50).mean().iloc[-1]),
        "ma150": float(close.rolling(150).mean().iloc[-1]),
        "ma200": float(close.rolling(200).mean().iloc[-1]),
        # 200MA one month (22 sessions) ago, to test that it's rising
        "ma200_22d_ago": float(close.rolling(200).mean().iloc[-23]),
    }


def rs_raw(df: pd.DataFrame) -> float | None:
    """IBD-style weighted momentum score.

    RS_raw = 0.4*(C/C63) + 0.2*(C/C126) + 0.2*(C/C189) + 0.2*(C/C252)

    Young stocks (>= 126 bars but < a full year) are scored on whichever
    windows exist, with the weights renormalized — IBD does the same for
    IPOs. Comparable enough to rank in the same pool; below 126 bars the
    number is mostly noise, so None.
    """
    close = df["Close"]
    if len(close) < MIN_BARS_IPO:
        return None
    c = close.iloc[-1]
    windows = [(0.4, 64), (0.2, 127), (0.2, 190), (0.2, 253)]
    score, wsum = 0.0, 0.0
    for w, back in windows:
        if len(close) >= back:
            base = close.iloc[-back]
            if base <= 0:
                return None
            score += w * (c / base)
            wsum += w
    return float(score / wsum) if wsum > 0 else None


def rs_ranks(raw_scores: dict[str, float]) -> dict[str, int]:
    """Percentile-rank raw RS scores across the universe -> 1..99."""
    s = pd.Series(raw_scores).dropna()
    if s.empty:
        return {}
    pct = s.rank(pct=True)
    return {t: max(1, min(99, int(round(p * 99)))) for t, p in pct.items()}


def trend_template(df: pd.DataFrame, rs_rank: int | None) -> dict:
    """Evaluate the Trend Template. Returns per-rule pass/fail + values.

    Full 8-rule template needs MIN_BARS. Stocks with 126..MIN_BARS bars
    (recent IPOs — some of the best SEPA trades come off primary bases) get a
    reduced template: only rules whose MAs/windows exist are evaluated, the
    52w range becomes range-since-listing, and the result carries ipo=True
    with rules_total = number of rules actually checked.
    """
    n = len(df)
    if n < MIN_BARS_IPO:
        return {"eligible": False, "reason": f"only {n} bars, need {MIN_BARS_IPO}"}
    ipo = n < MIN_BARS

    price = float(df["Close"].iloc[-1])
    close = df["Close"]
    ma50 = float(close.rolling(50).mean().iloc[-1])
    # 52-week range from intraday highs/lows (consistent with VCP/pivot math),
    # or the full range since listing for young stocks
    span = min(n, 252)
    low52 = float(df["Low"].iloc[-span:].min())
    high52 = float(df["High"].iloc[-span:].max())

    checks: dict[str, dict] = {}
    if not ipo:
        ma = moving_averages(df)
        checks["price_above_150_200"] = {
            "pass": price > ma["ma150"] and price > ma["ma200"],
            "price": price, "ma150": round(ma["ma150"], 2), "ma200": round(ma["ma200"], 2),
        }
        checks["ma150_above_ma200"] = {
            "pass": ma["ma150"] > ma["ma200"],
            "ma150": round(ma["ma150"], 2), "ma200": round(ma["ma200"], 2),
        }
        checks["ma200_rising_1m"] = {
            "pass": ma["ma200"] > ma["ma200_22d_ago"],
            "ma200_now": round(ma["ma200"], 2), "ma200_22d_ago": round(ma["ma200_22d_ago"], 2),
        }
        checks["ma50_above_150_200"] = {
            "pass": ma["ma50"] > ma["ma150"] and ma["ma50"] > ma["ma200"],
            "ma50": round(ma["ma50"], 2), "ma150": round(ma["ma150"], 2), "ma200": round(ma["ma200"], 2),
        }
    elif n >= 152:
        # young stock with 150MA available: use it where the full set can't be
        ma150 = float(close.rolling(150).mean().iloc[-1])
        checks["price_above_150_200"] = {
            "pass": price > ma150, "price": price, "ma150": round(ma150, 2),
            "note": "IPO: 200MA unavailable, 150MA only",
        }
        checks["ma50_above_150_200"] = {
            "pass": ma50 > ma150, "ma50": round(ma50, 2), "ma150": round(ma150, 2),
            "note": "IPO: 200MA unavailable, 150MA only",
        }

    checks["price_above_ma50"] = {"pass": price > ma50, "price": price, "ma50": round(ma50, 2)}
    checks["above_52w_low_30pct"] = {
        "pass": price >= low52 * 1.30,
        "price": price, "low52": round(low52, 2),
        "pct_above_low": round((price / low52 - 1) * 100, 1),
        **({"note": "IPO: low since listing"} if ipo else {}),
    }
    checks["within_25pct_of_52w_high"] = {
        "pass": price >= high52 * 0.75,
        "price": price, "high52": round(high52, 2),
        "pct_below_high": round((1 - price / high52) * 100, 1),
        **({"note": "IPO: high since listing"} if ipo else {}),
    }
    checks["rs_rank_ge_70"] = {"pass": rs_rank is not None and rs_rank >= 70, "rs_rank": rs_rank}

    return {
        "eligible": True,
        "ipo": ipo,
        "pass_all": all(c["pass"] for c in checks.values()),
        "checks": checks,
    }


def _swings(df: pd.DataFrame, window: int = 5) -> tuple[list[tuple], list[tuple]]:
    """Very simple swing high/low detection over the given window."""
    highs, lows = [], []
    h, l = df["High"].values, df["Low"].values
    for i in range(window, len(df) - window):
        if h[i] == max(h[i - window : i + window + 1]):
            highs.append((i, float(h[i])))
        if l[i] == min(l[i - window : i + window + 1]):
            lows.append((i, float(l[i])))
    return highs, lows


def detect_vcp(df: pd.DataFrame, lookback: int = 75) -> dict:
    """Heuristic VCP detection over the last `lookback` bars.

    Looks for >=2 successive contractions where each pullback (swing high ->
    following swing low) is shallower than the previous, plus volume dry-up.
    Returns pivot = most recent swing high (the buy point).
    """
    d = df.iloc[-lookback:]
    if len(d) < 40:
        return {"vcp": False, "reason": "not enough bars"}

    highs, lows = _swings(d)
    contractions = []
    swings = []  # [{t, p} high, {t, p} low] per contraction — chart geometry
    # each swing high pairs with the DEEPEST low of its own pullback (the lows
    # before the NEXT swing high) — pairing every high with the first low
    # anywhere ahead double-counted a single pullback for consecutive highs
    # and understated depth when the pullback bottomed on its second leg
    for k, (hi_idx, hi) in enumerate(highs):
        next_hi_idx = highs[k + 1][0] if k + 1 < len(highs) else len(d)
        between = [(lo_idx, lo) for lo_idx, lo in lows if hi_idx < lo_idx < next_hi_idx]
        if not between:
            continue
        lo_idx, lo = min(between, key=lambda x: x[1])
        depth = (hi - lo) / hi * 100
        if 0 < depth < 40:
            contractions.append(round(depth, 1))
            swings.append([
                {"t": d.index[int(hi_idx)].strftime("%Y-%m-%d"), "p": round(float(hi), 2)},
                {"t": d.index[int(lo_idx)].strftime("%Y-%m-%d"), "p": round(float(lo), 2)},
            ])

    # real VCPs are lumpy — demanding every contraction strictly smaller than
    # the last rejected textbook setups over one noisy 12.1%-after-12.0% pair.
    # Required instead: overall tightening (last < first), a genuinely tight
    # final contraction (<= 12%), and at most ONE out-of-order pair.
    violations = sum(
        1 for i in range(len(contractions) - 1) if contractions[i + 1] >= contractions[i]
    )
    shrinking = (
        len(contractions) >= 2
        and contractions[-1] < contractions[0]
        and contractions[-1] <= 12
        and violations <= 1
    )
    vol5 = float(d["Volume"].iloc[-5:].mean())
    # dry-up baseline: the 50 sessions BEFORE this week — including the quiet
    # week being measured in its own baseline made dry-ups harder to detect
    vol50 = float(df["Volume"].iloc[-55:-5].mean()) if len(df) >= 55 else 0.0
    dry_up = vol50 > 0 and vol5 < 0.6 * vol50
    pivot = max((hv for _, hv in highs[-2:]), default=None) if highs else None

    return {
        "vcp": bool(shrinking and dry_up and pivot),
        "contractions_pct": contractions,
        "swings": swings,
        "volume_dry_up": dry_up,
        "vol_5d_avg": int(vol5),
        "vol_50d_avg": int(vol50),
        "pivot": round(pivot, 2) if pivot else None,
    }


def extension_flags(df: pd.DataFrame, pivot: float | None) -> dict:
    """'Don't chase' checks: distance from pivot and from 50MA."""
    price = float(df["Close"].iloc[-1])
    ma50 = float(df["Close"].rolling(50).mean().iloc[-1])
    pct_from_pivot = round((price / pivot - 1) * 100, 1) if pivot else None
    pct_from_ma50 = round((price / ma50 - 1) * 100, 1)
    return {
        "pct_above_pivot": pct_from_pivot,
        "pct_above_ma50": pct_from_ma50,
        "extended": bool(
            (pct_from_pivot is not None and pct_from_pivot > 5) or pct_from_ma50 > 25
        ),
    }


def suggested_stop(df: pd.DataFrame, entry: float) -> float:
    """Stop = higher of (recent swing low, entry - 8%). Never wider than 8%.

    If price has run well past the pivot for 10+ sessions, the recent swing
    low can sit ABOVE the entry — a "stop" that triggers instantly. Fall back
    to the 8% floor in that case.
    """
    recent_low = float(df["Low"].iloc[-10:].min())
    floor = entry * 0.92
    stop = max(recent_low, floor)
    if stop >= entry:
        stop = floor
    return round(stop, 2)


def exit_signals(df: pd.DataFrame, entry: float, stop: float, pivot: float | None,
                 days_held: int | None = None) -> dict:
    """Minervini-style exit rules for open positions (daily-close based)."""
    close = df["Close"]
    price = float(close.iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    vol = df["Volume"]
    vol50_prior = float(vol.iloc[-51:-1].mean()) if len(vol) >= 51 else 0.0
    heavy = vol50_prior > 0 and float(vol.iloc[-1]) > 1.5 * vol50_prior

    weekly = df["Close"].resample("W").last().dropna()
    wvol = df["Volume"].resample("W").sum().dropna()
    down_weeks_heavy = False
    if len(weekly) >= 4:
        last3 = weekly.iloc[-3:].values
        down = sum(1 for i in range(1, 3) if last3[i] < last3[i - 1])
        vol_up = float(wvol.iloc[-2:].mean()) > float(wvol.iloc[-8:-2].mean()) if len(wvol) >= 8 else False
        down_weeks_heavy = down >= 2 and vol_up

    climax = False
    if len(close) >= 11:
        gain_2w = price / float(close.iloc[-11]) - 1
        climax = gain_2w >= 0.25

    time_stop = bool(days_held is not None and days_held >= 5 and price < entry * 1.02
                     and price >= stop)
    return {
        "time_stop": {"triggered": time_stop, "days_held": days_held,
                       "note": "no follow-through within 5 sessions — dead breakouts usually resolve down"},
        "stop_violated": {"triggered": price < stop, "price": price, "stop": stop},
        "below_50ma": {"triggered": price < ma50, "price": price, "ma50": round(ma50, 2), "heavy_volume": heavy},
        "failed_breakout": {
            "triggered": pivot is not None and entry >= pivot and price < pivot,
            "price": price, "pivot": pivot,
        },
        "distribution_weeks": {"triggered": down_weeks_heavy},
        "climax_run": {"triggered": climax, "gain_2w_pct": round((price / float(close.iloc[-11]) - 1) * 100, 1) if len(close) >= 11 else None},
        # Zanger levels: 1% under the pivot = exit immediately; +15% within the
        # first ~3 weeks = sell 20-30% into the thrust
        "zanger_failure": {
            "triggered": bool(pivot and entry >= pivot * 0.97 and price < pivot * 0.99),
            "price": price, "line": round(pivot * 0.99, 2) if pivot else None,
        },
        "sell_strength": {
            "triggered": bool(pivot and price >= pivot * 1.15
                              and (days_held is None or days_held <= 21)),
            "price": price, "zone_low": round(pivot * 1.15, 2) if pivot else None,
        },
    }


def _atr(df: pd.DataFrame, days: int = 14) -> float:
    h, l, pc = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return float(tr.rolling(days).mean().iloc[-1])


def support_resistance(df: pd.DataFrame, lookback: int = 250, max_levels: int = 2) -> dict:
    """Support/resistance as ZONES (price areas), not lines.

    1. Swing highs/lows over the lookback are clustered when within
       max(1.5%, 0.5*ATR) of each other — the cluster's min/max define the
       zone, padded by 0.25*ATR (wicks matter).
    2. Touches = distinct VISITS of price into the zone: consecutive bars
       intersecting the zone count as ONE touch; a new touch needs 3+ bars
       spent outside first. This counts wick tags, not just swing points.
    3. Strength = visits with the last ~3 months weighted 2x; 'strong' >= 3.

    Returns up to max_levels supports below price and resistances above,
    nearest first, each as {low, high, price (mid), touches, ...}.
    """
    d = df.iloc[-lookback:]
    if len(d) < 60:
        return {"supports": [], "resistances": []}
    price = float(d["Close"].iloc[-1])
    atr = _atr(d)
    tol = max(price * 0.015, atr * 0.5)
    pad = atr * 0.25

    highs, lows = _swings(d, window=5)
    points = sorted([p for _, p in highs] + [p for _, p in lows])
    if not points:
        return {"supports": [], "resistances": []}

    clusters, cur = [], [points[0]]
    for p in points[1:]:
        if p - cur[-1] <= tol:
            cur.append(p)
        else:
            clusters.append(cur)
            cur = [p]
    clusters.append(cur)

    bars_h, bars_l = d["High"].values, d["Low"].values
    n = len(d)
    recent_cut = n - 63

    levels = []
    for cl in clusters:
        zlo, zhi = min(cl) - pad, max(cl) + pad
        visits, weighted, last_idx = 0, 0, None
        outside = 99
        for i in range(n):
            inside = bars_l[i] <= zhi and bars_h[i] >= zlo
            if inside:
                if outside >= 3:
                    visits += 1
                    weighted += 2 if i >= recent_cut else 1
                last_idx = i
                outside = 0
            else:
                outside += 1
        if visits == 0:
            continue
        mid = round((zlo + zhi) / 2, 2)
        levels.append({
            "price": mid,                      # zone midpoint (back-compat)
            "low": round(zlo, 2), "high": round(zhi, 2),
            "touches": visits,
            "weighted_touches": weighted,
            "strength": "strong" if weighted >= 3 else "minor",
            "last_touch_bars_ago": n - 1 - last_idx,
        })

    # fewer, better zones: strong zones first, then nearest — max 2 per side
    def pick(cands, keyfn):
        cands = sorted(cands, key=keyfn)
        strong = [l for l in cands if l["strength"] == "strong"]
        rest = [l for l in cands if l["strength"] != "strong"]
        return (strong + rest)[:max_levels]

    supports = pick([l for l in levels if l["high"] < price], lambda l: price - l["high"])
    resistances = pick([l for l in levels if l["low"] > price], lambda l: l["low"] - price)
    return {"supports": supports, "resistances": resistances}

def adr_pct(df: pd.DataFrame, days: int = 20) -> float:
    """Average daily range %: mean of (High/Low - 1) over the last `days` bars.
    Swing traders want movers — a 1%/day stock takes months to pay."""
    d = df.iloc[-days:]
    return round(float((d["High"] / d["Low"] - 1).mean() * 100), 2)


def quality_score(df: pd.DataFrame, vcp: dict) -> int:
    """Breakout setup quality, 0-100. Not all pivots are equal.

    Components: base depth (25), last-contraction tightness (25),
    volume confirmation/dry-up (30), pivot proximity to 52w high (20).
    """
    score = 0.0
    base = df.iloc[-75:]
    price = float(df["Close"].iloc[-1])

    # 1) base depth: <15% ideal, >35% junk
    depth = (float(base["High"].max()) - float(base["Low"].min())) / float(base["High"].max()) * 100
    score += 25 * max(0.0, min(1.0, (35 - depth) / 20))

    # 2) tightness of final contraction
    contr = vcp.get("contractions_pct") or []
    if contr:
        last = contr[-1]
        score += 25 * max(0.0, min(1.0, (12 - last) / 9))  # <=3% -> full marks

    # 3) volume: dry-up in base, surge today (vs the PRIOR 50 days — today's
    # own spike must not inflate the baseline it's measured against)
    vol50 = float(df["Volume"].iloc[-51:-1].mean()) if len(df) >= 51 else 0.0
    if vol50 > 0:
        if vcp.get("volume_dry_up"):
            score += 15
        surge = float(df["Volume"].iloc[-1]) / vol50
        score += 15 * max(0.0, min(1.0, (surge - 1.0) / 1.0))  # 2x avg -> full marks

    # 4) pivot near the 52-week high (little overhead supply) — intraday High,
    # same definition the trend template and VCP pivot use
    high52 = float(df["High"].iloc[-252:].max())
    pivot = vcp.get("pivot") or price
    score += 20 * max(0.0, min(1.0, 1 - (high52 - pivot) / high52 / 0.15))

    return int(round(max(0, min(100, score))))


def industry_group_rs(ranks: dict[str, int], industry_map: dict[str, str | None],
                      min_members: int = 3) -> dict[str, int]:
    """Percentile-rank industry groups (1-99) by median member RS rank.

    O'Neil: roughly half a stock's move is its group + the market. A mediocre
    stock in a leading group often beats a great stock in a dead one.
    """
    groups: dict[str, list[int]] = {}
    for t, rank in ranks.items():
        ind = industry_map.get(t)
        if ind:
            groups.setdefault(ind, []).append(rank)
    scored = {ind: float(np.median(r)) for ind, r in groups.items() if len(r) >= min_members}
    if not scored:
        return {}
    s = pd.Series(scored).rank(pct=True)
    return {ind: max(1, min(99, int(round(p * 99)))) for ind, p in s.items()}


def rule_results(tt: dict) -> tuple[int, list[str]]:
    """(rules passed, failed rule keys) from a trend_template result."""
    checks = tt.get("checks", {})
    failed = [k for k, v in checks.items() if not v["pass"]]
    return len(checks) - len(failed), failed


def what_needs_to_happen(tt: dict, price: float) -> list[str]:
    """Plain-English gap descriptions for each failed trend template rule."""
    msgs = []
    for k in rule_results(tt)[1]:
        v = tt["checks"][k]
        if k == "price_above_ma50":
            msgs.append(f"needs to reclaim the 50-day MA at ${v['ma50']} (price ${v['price']})")
        elif k == "price_above_150_200":
            msgs.append(f"needs to clear the 150/200-day MAs (${v['ma150']}/${v['ma200']})")
        elif k == "ma50_above_150_200":
            msgs.append("50-day MA still below the longer MAs — needs more time trending up")
        elif k == "ma150_above_ma200":
            msgs.append("150-day MA still below 200-day — trend structure not fully turned yet")
        elif k == "ma200_rising_1m":
            msgs.append("200-day MA still flat/falling — needs another few weeks of strength")
        elif k == "above_52w_low_30pct":
            msgs.append(f"only {v['pct_above_low']}% above its 52-week low — needs 30%+")
        elif k == "within_25pct_of_52w_high":
            msgs.append(f"still {v['pct_below_high']}% below its 52-week high — needs to get within 25%")
        elif k == "rs_rank_ge_70":
            msgs.append(f"RS rank {v['rs_rank']} — needs 70+ (keep outperforming)")
    return msgs


def setup_warnings(df: pd.DataFrame, pivot: float | None, checks: dict,
                   vol_profile: dict | None) -> list[dict]:
    """Bearish signs a setup already on the board is breaking down — whether
    you're holding it or just watching. Checked regardless of bucket, so a
    swing pick that curdles overnight doesn't just quietly vanish."""
    warnings = []
    price = float(df["Close"].iloc[-1])
    ma50 = float(df["Close"].rolling(50).mean().iloc[-1])

    if checks and not checks.get("price_above_ma50", {}).get("pass", True):
        warnings.append({"code": "below_50ma", "severity": "high",
            "title": "Trend broken: price below the 50-day MA",
            "what": f"Price ({round(price, 2)}) is now below its 50-day moving average "
                    f"({round(ma50, 2)}).",
            "why": "The 50-day MA is the line institutions use as their short-term trend "
                   "filter — the whole Trend Template is built on price staying above it. "
                   "Losing it means the uptrend this setup depended on has broken, not just "
                   "pulled back.",
            "do": "If you're in the trade, this is one of the five standard sell signals — "
                  "don't wait for the stop, especially on heavy volume. If you're only "
                  "watching, this pick no longer qualifies as a buy until it reclaims the line."})

    if pivot and len(df) >= 12:
        recent = df.iloc[-10:-1]  # exclude today so "recent trigger" means a PRIOR bar
        if bool((recent["Close"] >= pivot).any()) and price < pivot * 0.99:
            warnings.append({"code": "failed_breakout", "severity": "high",
                "title": "Failed breakout: back below the pivot",
                "what": f"Price cleared the pivot ({round(pivot, 2)}) within the last 10 "
                        f"sessions but has since fallen back below it, to {round(price, 2)}.",
                "why": "A breakout is supposed to mean demand overwhelmed the sellers waiting "
                       "at that level. Falling back below it says that demand didn't hold — "
                       "the buyers who chased the break are now underwater and often become "
                       "forced sellers themselves, which is why failed breakouts fall hard.",
                "do": "If you bought the breakout, this is the textbook cut-losses-fast "
                      "situation — don't average down or wait it out. If you haven't entered, "
                      "treat this pivot as invalidated; wait for a new base to form."})

    if vol_profile and vol_profile.get("distribution_days", 0) >= vol_profile.get("accumulation_days", 0) + 3:
        warnings.append({"code": "distribution", "severity": "medium",
            "title": "Heavy distribution: selling outweighs buying",
            "what": f"{vol_profile['distribution_days']} distribution days (down on above-"
                    f"average volume) vs only {vol_profile.get('accumulation_days', 0)} "
                    f"accumulation days in the last {vol_profile.get('window', 25)} sessions.",
            "why": "Volume reveals who's trading, not just what price did. Persistent "
                   "heavy-volume down days mean institutions are unloading shares into "
                   "any strength — the exact footprint that precedes a real breakdown, "
                   "even while the chart still looks intact.",
            "do": "Tighten your mental stop and don't add to the position. This alone "
                  "isn't a sell signal, but it removes the benefit of the doubt on any "
                  "other weakness you see."})

    if len(df) >= 11:
        gain_2w = price / float(df["Close"].iloc[-11]) - 1
        if gain_2w >= 0.25:
            warnings.append({"code": "climax_run", "severity": "medium",
                "title": "Climax run: parabolic, exhaustion risk",
                "what": f"Up {round(gain_2w * 100)}% in just the last two weeks.",
                "why": "Moves this fast are driven by euphoria, not the gradual institutional "
                       "accumulation that supports a healthy trend — and euphoria reverses "
                       "just as fast as it built, often on the single best-looking up day "
                       "(a 'climax top').",
                "do": "Don't chase it here. If you're already in, this is a normal cue to sell "
                      "into strength and bank some gains rather than hoping for more."})

    return warnings


def pocket_pivot(df: pd.DataFrame, lookback: int = 10) -> bool:
    """Early entry signal INSIDE a base (O'Neil/Kacher): an up day closing in
    the top third of its range on volume greater than any down-day volume of
    the past `lookback` sessions — institutional buying before the breakout.

    Kacher's actual rules, all enforced:
    - up/down days are measured close-to-close (a gap-down that closes green
      intraday is still a DOWN day)
    - invalid below the 50-day MA (that volume signature in a downtrend is
      short-covering, not accumulation)
    - must occur AT the 10-day MA (day's low within ~2% of it) — a pocket
      pivot extended above the 10-day line is invalid, which is exactly how
      every strong up day after a big run was misfiring the signal
    """
    if len(df) < 52:
        return False
    last = df.iloc[-1]
    h, l, c, v = (float(last[x]) for x in ("High", "Low", "Close", "Volume"))
    prev_close = float(df["Close"].iloc[-2])
    ma50 = float(df["Close"].rolling(50).mean().iloc[-1])
    ma10 = float(df["Close"].rolling(10).mean().iloc[-1])
    if c <= prev_close or c <= ma50:
        return False
    if (h - l) <= 0 or (c - l) / (h - l) < 0.62:
        return False
    if l > ma10 * 1.02:
        return False  # extended above the 10-day line — not a pocket pivot
    down_mask = (df["Close"] < df["Close"].shift(1)).iloc[-(lookback + 1):-1]
    down_vols = df["Volume"].iloc[-(lookback + 1):-1][down_mask]
    return bool(len(down_vols) > 0 and v > float(down_vols.max()))


def _ma_bounce(df: pd.DataFrame, window: int, min_bars: int, rising_lag: int,
               tag_days: int, stop_span: int, risk_cap: float) -> dict | None:
    """Pullback-bounce entry at a rising MA — the classic low-risk 'second
    chance' entry in a confirmed uptrend (caller enforces the Trend Template;
    this only validates the bounce itself).

    All conditions, EOD:
    - MA rising (vs `rising_lag` sessions ago)
    - price RESPECTS the line: closed above it on >=30 of the last 40
      sessions — a stock that ignores its MA gives no meaning to a bounce
    - a pullback TAGGED the line within the last `tag_days` sessions
      (low <= that day's MA * 1.005) on volume below the prior 50-day
      average — weak hands leaving, not institutions distributing
    - today reclaimed it: close above the MA, up on the day, close in the
      top half of the range

    Entry = today's close (buy next open live); stop = the pullback low.
    Risk beyond `risk_cap`% means the pullback wasn't orderly — invalid.
    """
    if len(df) < min_bars:
        return None
    close, low, high, vol = df["Close"], df["Low"], df["High"], df["Volume"]
    ma = close.rolling(window).mean()
    price = float(close.iloc[-1])
    m_now = float(ma.iloc[-1])
    if pd.isna(ma.iloc[-(rising_lag + 1)]) or m_now <= float(ma.iloc[-(rising_lag + 1)]):
        return None
    if int((close.iloc[-40:] > ma.iloc[-40:]).sum()) < 30:
        return None
    vol50 = float(vol.iloc[-51:-1].mean()) if len(vol) >= 51 else 0.0
    if vol50 <= 0:
        return None
    tag_i = None
    for i in range(2, 2 + tag_days):  # yesterday backwards
        m = float(ma.iloc[-i])
        if not pd.isna(m) and float(low.iloc[-i]) <= m * 1.005:
            if float(vol.iloc[-i]) < vol50:
                tag_i = i
            break  # nearest tag decides; a heavy-volume tag disqualifies
    if tag_i is None:
        return None
    h, l = float(high.iloc[-1]), float(low.iloc[-1])
    up_day = price > float(close.iloc[-2])
    reclaim = price > m_now
    strong_close = (h - l) > 0 and (price - l) >= 0.5 * (h - l)
    if not (up_day and reclaim and strong_close):
        return None
    stop = float(low.iloc[-stop_span:].min())  # under the whole pullback
    if stop >= price:
        return None
    risk_pct = round((price - stop) / price * 100, 1)
    if risk_pct > risk_cap:
        return None
    return {"trigger": round(price, 2), "stop": round(stop, 2), "risk_pct": risk_pct,
            "ma": round(m_now, 2),
            "tag_t": df.index[-tag_i].strftime("%Y-%m-%d"),  # the pullback-tag day, for the chart
            "note": (f"Pullback-bounce at the rising {window}-day MA: light-volume "
                     f"tag, reclaimed on a strong close. Entry next open; stop under "
                     f"the pullback low.")}


def ma20_bounce(df: pd.DataFrame) -> dict | None:
    """Swing-timeframe bounce at the rising 20-day MA."""
    return _ma_bounce(df, window=20, min_bars=60, rising_lag=5,
                      tag_days=4, stop_span=6, risk_cap=8.0)


def ma50_bounce(df: pd.DataFrame) -> dict | None:
    """Position-timeframe bounce at the rising 50-day MA — the '10-week line'
    second-chance entry; pullbacks are deeper so the risk cap is wider."""
    return _ma_bounce(df, window=50, min_bars=120, rising_lag=10,
                      tag_days=5, stop_span=7, risk_cap=10.0)


def episodic_pivot(df: pd.DataFrame) -> dict | None:
    """Qullamaggie-style Episodic Pivot, EOD version: a violent gap on massive
    volume out of NEGLECT (the stock wasn't already running). The catalyst
    (earnings/news) is NOT verified here — the AI layer checks it via web
    search; a gap this size without a findable catalyst is suspect.

    Entry = break above the gap-day high; stop = the gap-day low.
    """
    if len(df) < 70:
        return None
    last, prev = df.iloc[-1], df.iloc[-2]
    o, h, l, c = (float(last[x]) for x in ("Open", "High", "Low", "Close"))
    pc, ph = float(prev["Close"]), float(prev["High"])
    vol50 = float(df["Volume"].iloc[-51:-1].mean())
    if vol50 <= 0 or pc <= 0 or h <= l:
        return None
    gap_pct = (o / pc - 1) * 100
    chg_pct = (c / pc - 1) * 100
    vol_x = float(last["Volume"]) / vol50
    if not ((o > ph or gap_pct >= 4) and chg_pct >= 6 and vol_x >= 3 and c >= o):
        return None
    c63 = float(df["Close"].iloc[-64])
    neglect = bool(c63 > 0 and pc / c63 <= 1.10)  # wasn't already up >10% in 3m
    if not neglect:
        return None
    risk_pct = round((h - l) / h * 100, 1)
    if risk_pct > 12:
        return None  # gap-day range too wild for a defined-risk entry
    return {"trigger": round(h, 2), "stop": round(l, 2), "risk_pct": risk_pct,
            "gap_pct": round(gap_pct, 1), "chg_pct": round(chg_pct, 1),
            "vol_x": round(vol_x, 1),
            "note": ("Episodic pivot: violent gap on massive volume out of neglect. "
                     "Entry on a break above the gap-day high, stop at its low. "
                     "VERIFY THE CATALYST first — no catalyst, no trade.")}


def momentum_burst(df: pd.DataFrame) -> dict | None:
    """Stockbee-style 4% burst: a range-expansion day on rising volume out of
    a QUIET period — often the first footprint of a new momentum phase. A
    watch flag, not an entry: it feeds attention, the setup comes later."""
    if len(df) < 60:
        return None
    close, vol = df["Close"], df["Volume"]
    c, pc = float(close.iloc[-1]), float(close.iloc[-2])
    chg_pct = (c / pc - 1) * 100
    vol50 = float(vol.iloc[-51:-1].mean())
    if vol50 <= 0:
        return None
    vol_x = float(vol.iloc[-1]) / vol50
    quiet = abs(float(close.iloc[-2]) / float(close.iloc[-7]) - 1) <= 0.03
    ma20 = float(close.rolling(20).mean().iloc[-1])
    if not (chg_pct >= 4 and vol_x >= 1.5 and float(vol.iloc[-1]) > float(vol.iloc[-2])
            and quiet and c <= ma20 * 1.15):
        return None
    return {"chg_pct": round(chg_pct, 1), "vol_x": round(vol_x, 1),
            "note": ("4%+ burst on expanding volume out of a quiet base — early "
                     "momentum footprint; watch for a proper setup to form.")}


def anticipation(vcp: dict, tightening: bool, price: float, pivot: float | None) -> dict | None:
    """Breakout-anticipation score (0-100): how close to READY is this base?
    Requires 2+ contractions and price 0-6% below the pivot. Components:
    proximity to pivot (30), final-contraction tightness (30), volume
    dry-up (20), coiling now (20)."""
    contr = vcp.get("contractions_pct") or []
    if not pivot or len(contr) < 2 or price >= pivot:
        return None
    dist = (pivot - price) / pivot
    if dist > 0.06:
        return None
    score = (30 * (1 - dist / 0.06)
             + 30 * max(0.0, min(1.0, (12 - contr[-1]) / 12))
             + (20 if vcp.get("volume_dry_up") else 0)
             + (20 if tightening else 0))
    return {"score": int(round(score)), "pct_to_pivot": round(dist * 100, 1)}


def base_count(df: pd.DataFrame) -> dict | None:
    """Stage-analysis base count: how many consolidations has this uptrend
    already built? O'Neil: 3rd and later bases fail far more often — by then
    the move is obvious and the late money is already in.

    Heuristic: uptrend origin = the last close below the 200MA (capped ~2
    years back); from there, every stretch of 15+ sessions without a new
    high counts as one base.

    Also returns the bases' chart geometry: each base spans from the high it
    formed under (start) to the day a new high broke it (end; None while the
    base is still forming) — so the chart can shade exactly what the stage
    analysis counted.
    """
    if len(df) < MIN_BARS:
        return None
    close = df["Close"]
    ma200 = close.rolling(200).mean()
    below = (close < ma200).fillna(False).values
    n = len(df)
    window = min(n, 504)
    idxs = np.where(below[-window:])[0]
    start = n - window + (int(idxs[-1]) + 1 if len(idxs) else 0)
    highs = df["High"].values[start:]
    if len(highs) < 20:
        return {"count": 0, "stage": "new uptrend", "uptrend_sessions": len(highs),
                "bases": []}

    def day(j: int) -> str:
        return df.index[start + j].strftime("%Y-%m-%d")

    run_high = float(highs[0])
    run_i = 0
    bars_since, count, in_base = 0, 0, False
    bases, cur = [], None
    for j in range(1, len(highs)):
        h = highs[j]
        if h > run_high:
            run_high = float(h)
            if in_base and cur:
                cur["end"] = day(j)  # breakout: first new high ends the base
                bases.append(cur)
                cur = None
            run_i = j
            bars_since, in_base = 0, False
        else:
            bars_since += 1
            if bars_since >= 15 and not in_base:
                count += 1
                in_base = True
                cur = {"n": count, "start": day(run_i)}  # the high it formed under
    if in_base and cur:
        cur["end"] = None  # still forming
        bases.append(cur)
    stage = ("early (1st-2nd base)" if count <= 2 else
             "late (3rd base)" if count == 3 else "very late (4th+ base)")
    return {"count": count, "stage": stage, "uptrend_sessions": len(highs),
            "bases": bases}


def tightening_now(df: pd.DataFrame) -> dict:
    """Is price coiling RIGHT NOW? Last 10-day range vs the prior 10-day range,
    while holding near the highs — the tell that a base is maturing."""
    if len(df) < 25:
        return {"tightening": False}
    last10 = df.iloc[-10:]
    prev10 = df.iloc[-20:-10]
    r1 = float(last10["High"].max() - last10["Low"].min())
    r0 = float(prev10["High"].max() - prev10["Low"].min())
    price = float(df["Close"].iloc[-1])
    hi = float(df["High"].iloc[-40:].max())
    near_highs = price >= hi * 0.90
    ratio = round(r1 / r0, 2) if r0 > 0 else None
    return {"tightening": bool(r0 > 0 and r1 < 0.6 * r0 and near_highs),
            "range_ratio": ratio, "near_highs": near_highs}


def early_entry(df: pd.DataFrame, pivot: float | None) -> dict | None:
    """Minervini-style 'cheat' entry: the short-term high INSIDE the base.

    When price is still below the main pivot, clearing the last ~5 sessions'
    high on volume is an earlier, lower-risk-per-share entry — take a partial
    position there with the stop under the recent low, add the rest at the
    real pivot. Only offered when a base exists and price sits 2%+ below pivot.
    """
    if not pivot or len(df) < 30:
        return None
    price = float(df["Close"].iloc[-1])
    if price >= pivot * 0.98:
        return None  # already at the pivot — no early entry left
    if price < pivot * 0.80:
        # more than 20% below the pivot isn't "inside the base" anymore — a cheat
        # entry that far down is just buying weakness, not an early breakout
        return None
    trigger = float(df["High"].iloc[-5:].max())
    stop = float(df["Low"].iloc[-8:].min())
    if trigger >= pivot or trigger <= price * 0.995 or stop >= trigger * 0.99:
        return None
    risk_pct = round((trigger - stop) / trigger * 100, 1)
    stop_capped = risk_pct > 8
    if stop_capped:
        stop = round(trigger * 0.94, 2)
        risk_pct = 6.0
    pct_below_pivot = round((pivot - price) / pivot * 100, 1)
    reasons = [
        f"Price is {pct_below_pivot}% below the main pivot — a base exists but the "
        f"breakout hasn't happened yet, so there's room for an earlier entry",
        f"Trigger {round(trigger, 2)} is the highest high of the last 5 sessions: "
        f"clearing it proves buyers are already pushing price up inside the base",
        (f"Stop {round(stop, 2)} capped at 6% below the trigger (the natural swing "
         f"low was too far away to risk)" if stop_capped else
         f"Stop {round(stop, 2)} sits under the lowest low of the last 8 sessions — "
         f"only {risk_pct}% risk per share, tighter than waiting for the pivot"),
        "Half position only: cheat entries fail more often than pivot buys — "
        "add the second half when price clears the main pivot",
    ]
    return {"trigger": round(trigger, 2), "stop": round(stop, 2), "risk_pct": risk_pct,
            "pct_below_pivot": pct_below_pivot, "reasons": reasons,
            "note": ("Cheat entry: clearing the short-term high inside the base — start with a "
                     "HALF position here (tighter stop, earlier), add the rest at the main pivot. "
                     "Fails more often than pivot buys, which is why it's half-size.")}
