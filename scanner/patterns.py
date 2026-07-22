"""Candlestick patterns, volume behavior, and movement — with plain-English notes.

These are context, not signals on their own. A hammer at support inside a valid
base means something; a hammer in a downtrend mostly doesn't.
"""
from __future__ import annotations

import pandas as pd

try:  # TA-Lib: industry-standard pattern recognition; heuristics remain the fallback
    import talib
    _HAS_TALIB = True
except Exception:  # pragma: no cover
    _HAS_TALIB = False

# (talib function, bullish (name, note), bearish (name, note)) — sign of the
# talib output picks the side; None means that side can't occur for the pattern
_CDL = [
    ("CDLMORNINGSTAR",
     ("morning star", "3-bar reversal: big down bar, indecision, then a strong close into the first bar's body — buyers wrestled control at the low"), None),
    ("CDLEVENINGSTAR",
     None, ("evening star", "3-bar top: big up bar, indecision, then a strong close down into the first bar's body — sellers took over at the high")),
    ("CDL3WHITESOLDIERS",
     ("three white soldiers", "three consecutive strong closes, each opening inside the prior body — persistent, orderly buying"), None),
    ("CDL3BLACKCROWS",
     None, ("three black crows", "three consecutive weak closes — persistent selling pressure, tops often start this way")),
    ("CDLENGULFING",
     ("bullish engulfing", "today's up body completely swallowed yesterday's down body — buyers overwhelmed sellers"),
     ("bearish engulfing", "today's down body swallowed yesterday's up body — sellers took control")),
    ("CDLPIERCING",
     ("piercing line", "gapped down but closed above the midpoint of yesterday's down bar — dip buyers showed real size"), None),
    ("CDLDARKCLOUDCOVER",
     None, ("dark cloud cover", "gapped up but closed below the midpoint of yesterday's up bar — rally sold hard")),
    ("CDLHAMMER",
     ("hammer", "sellers pushed price down intraday but buyers took it back — demand showed up at the low"), None),
    ("CDLINVERTEDHAMMER",
     ("inverted hammer", "probe higher after a decline that mostly held — early sign sellers are losing conviction"), None),
    ("CDLSHOOTINGSTAR",
     None, ("shooting star", "buyers pushed up intraday but sellers slapped it back down — supply overhead")),
    ("CDLHANGINGMAN",
     None, ("hanging man", "hammer shape after a rise — intraday dumping absorbed this time, but a warning shot")),
    ("CDLHARAMI",
     ("bullish harami", "today's small body sat inside yesterday's big down body — selling pressure stalling"),
     ("bearish harami", "today's small body sat inside yesterday's big up body — buying pressure stalling")),
    ("CDLDOJI",
     ("doji", "open and close nearly equal — indecision; direction of the NEXT bar matters more"), None),
]
_MAX_CANDLE_PATTERNS = 4  # ordered by significance above; don't spam the panel


def _talib_last_bar(df: pd.DataFrame) -> list[dict]:
    o = df["Open"].values.astype(float)
    h = df["High"].values.astype(float)
    l = df["Low"].values.astype(float)
    c = df["Close"].values.astype(float)
    out = []
    for func, bull, bear in _CDL:
        v = int(getattr(talib, func)(o, h, l, c)[-1])
        if v > 0 and bull:
            name, note = bull
            out.append({"name": name, "bias": "neutral" if name == "doji" else "bullish", "note": note})
        elif v < 0 and bear:
            name, note = bear
            out.append({"name": name, "bias": "bearish", "note": note})
    return out[:_MAX_CANDLE_PATTERNS]


def _heuristic_last_bar(df: pd.DataFrame) -> list[dict]:
    """Fallback when TA-Lib isn't available — simpler shape rules."""
    out = []
    o, h, l, c = (float(df[x].iloc[-1]) for x in ("Open", "High", "Low", "Close"))
    po, pc = float(df["Open"].iloc[-2]), float(df["Close"].iloc[-2])
    rng = h - l
    body = abs(c - o)
    if rng <= 0:
        return []
    if body / rng < 0.15:
        out.append({"name": "doji", "bias": "neutral",
                    "note": "open and close nearly equal — indecision; direction of the NEXT bar matters more"})
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    if lower_wick > 2 * body and upper_wick < body:
        out.append({"name": "hammer", "bias": "bullish",
                    "note": "sellers pushed price down intraday but buyers took it back — demand showed up at the low"})
    if upper_wick > 2 * body and lower_wick < body:
        out.append({"name": "shooting star", "bias": "bearish",
                    "note": "buyers pushed up intraday but sellers slapped it back down — supply overhead"})
    if c > o and pc < po and c > po and o < pc:
        out.append({"name": "bullish engulfing", "bias": "bullish",
                    "note": "today's up bar completely swallowed yesterday's down bar — buyers overwhelmed sellers"})
    if c < o and pc > po and c < po and o > pc:
        out.append({"name": "bearish engulfing", "bias": "bearish",
                    "note": "today's down bar swallowed yesterday's up bar — sellers took control"})
    return out


def last_bar_patterns(df: pd.DataFrame) -> list[dict]:
    """Candlestick patterns on the most recent daily bar (TA-Lib when available),
    plus range/gap structures TA-Lib doesn't classify (inside day, NR7, gap up)."""
    if len(df) < 15:
        return []
    out = _talib_last_bar(df) if _HAS_TALIB else _heuristic_last_bar(df)

    h, l = float(df["High"].iloc[-1]), float(df["Low"].iloc[-1])
    ph, pl = float(df["High"].iloc[-2]), float(df["Low"].iloc[-2])
    if h < ph and l > pl:
        out.append({"name": "inside day", "bias": "neutral",
                    "note": "today's whole range fit inside yesterday's — volatility contracting, often precedes a decisive move"})
    ranges = (df["High"] - df["Low"]).iloc[-7:]
    if float(ranges.iloc[-1]) == float(ranges.min()):
        out.append({"name": "NR7", "bias": "neutral",
                    "note": "narrowest range of the last 7 days — coiled spring; expansion usually follows contraction"})
    if l > ph:
        out.append({"name": "gap up", "bias": "bullish",
                    "note": "opened and stayed above yesterday's entire range — urgent buying, often institutional"})
    return out


def volume_profile(df: pd.DataFrame, window: int = 25) -> dict:
    """Accumulation vs distribution over the last `window` sessions.

    Accumulation day: close up on above-average volume (institutions buying).
    Distribution day: close down on above-average volume (institutions selling).
    """
    d = df.iloc[-window:]
    # each day compares against its OWN trailing 50-day average (shifted so a
    # day never sits inside its own baseline) — using today's average for all
    # 25 days quietly leaked future volume into past days' classification
    avg_series = df["Volume"].rolling(50).mean().shift(1).iloc[-window:]
    if len(d) < window or avg_series.isna().all() or float(avg_series.max() or 0) <= 0:
        return {"accumulation_days": 0, "distribution_days": 0, "verdict": "insufficient data"}
    closes = d["Close"].values
    vols = d["Volume"].values
    avgs = avg_series.values
    acc = dist = 0
    for i in range(1, len(d)):
        if pd.notna(avgs[i]) and vols[i] > avgs[i]:
            if closes[i] > closes[i - 1]:
                acc += 1
            elif closes[i] < closes[i - 1]:
                dist += 1
    if acc >= dist + 3:
        verdict = "under accumulation — big-volume days are mostly UP days (institutions building positions)"
    elif dist >= acc + 3:
        verdict = "under distribution — big-volume days are mostly DOWN days (institutions unloading); be careful"
    else:
        verdict = "volume balanced — no clear institutional footprint either way"
    return {"accumulation_days": acc, "distribution_days": dist, "window": window, "verdict": verdict}


def movement(df: pd.DataFrame) -> dict:
    c = df["Close"]
    price = float(c.iloc[-1])
    hi20, lo20 = float(df["High"].iloc[-20:].max()), float(df["Low"].iloc[-20:].min())
    pos = (price - lo20) / (hi20 - lo20) * 100 if hi20 > lo20 else 50.0
    return {
        "chg_5d_pct": round((price / float(c.iloc[-6]) - 1) * 100, 1) if len(c) > 6 else None,
        "chg_20d_pct": round((price / float(c.iloc[-21]) - 1) * 100, 1) if len(c) > 21 else None,
        "pos_in_20d_range_pct": round(pos, 0),
    }


def analyze(df: pd.DataFrame) -> dict:
    pats = last_bar_patterns(df)
    chart = chart_patterns(df)
    vol = volume_profile(df)
    mov = movement(df)
    bits = []
    if mov.get("chg_5d_pct") is not None:
        bits.append(f"{'up' if mov['chg_5d_pct'] >= 0 else 'down'} {abs(mov['chg_5d_pct'])}% over 5 days")
    if mov.get("pos_in_20d_range_pct") is not None:
        bits.append(f"sitting at the {'top' if mov['pos_in_20d_range_pct'] >= 70 else ('bottom' if mov['pos_in_20d_range_pct'] <= 30 else 'middle')} of its 20-day range")
    if chart:
        bits.append("chart pattern: " + ", ".join(f"{p['name']} ({p['bias']})" for p in chart))
    if pats:
        bits.append("latest candle: " + ", ".join(p["name"] for p in pats))
    bits.append(vol["verdict"])
    last_t = df.index[-1].strftime("%Y-%m-%d")
    markers = [{"t": last_t, "position": "belowBar" if p["bias"] != "bearish" else "aboveBar",
                "shape": "arrowUp" if p["bias"] == "bullish" else ("arrowDown" if p["bias"] == "bearish" else "circle"),
                "text": p["name"]} for p in pats]
    return {"patterns": pats, "chart_patterns": chart, "volume": vol, "movement": mov,
            "chart_markers": markers,
            "narrative": "; ".join(bits).capitalize() + "."}


# ---------- Multi-bar chart patterns ----------

def _swings_pat(df: pd.DataFrame, window: int = 5):
    highs, lows = [], []
    h, l = df["High"].values, df["Low"].values
    for i in range(window, len(df) - window):
        if h[i] == max(h[i - window : i + window + 1]):
            highs.append((i, float(h[i])))
        if l[i] == min(l[i - window : i + window + 1]):
            lows.append((i, float(l[i])))
    return highs, lows


def _t(d, i):
    return d.index[int(i)].strftime("%Y-%m-%d")


def chart_patterns(df: pd.DataFrame) -> list[dict]:
    """Detect classic multi-bar patterns over the last ~120 bars.

    Each result: {name, bias, note, pivot?}. Heuristic — treat as context that
    strengthens or weakens the Minervini setup, not as standalone signals.
    """
    d = df.iloc[-120:]
    if len(d) < 45:
        return []
    out = []
    price = float(d["Close"].iloc[-1])
    highs, lows = _swings_pat(d)
    n = len(d)

    # --- Double bottom (W): two lows within 3%, 10+ bars apart, middle peak >=6% up
    for i in range(len(lows) - 1):
        (i1, l1), (i2, l2) = lows[i], lows[i + 1]
        if i2 - i1 >= 10 and abs(l1 - l2) / l1 <= 0.03:
            between = [p for j, p in highs if i1 < j < i2]
            if between and max(between) >= min(l1, l2) * 1.06 and price > max(between) * 0.97:
                mid_j, mid_p = max(((j, p) for j, p in highs if i1 < j < i2), key=lambda x: x[1])
                out.append({"name": "double bottom", "bias": "bullish", "pivot": round(max(between), 2),
                            "lines": [[{"t": _t(d, i1), "p": round(l1, 2)}, {"t": _t(d, mid_j), "p": round(mid_p, 2)},
                                       {"t": _t(d, i2), "p": round(l2, 2)}]],
                            "note": "sellers failed twice at the same area (W shape) — demand defended that "
                                    "zone both times; clearing the middle peak confirms the reversal"})
                break

    # --- Double top (M): mirror — bearish
    for i in range(len(highs) - 1):
        (i1, h1), (i2, h2) = highs[i], highs[i + 1]
        if i2 - i1 >= 10 and abs(h1 - h2) / h1 <= 0.03 and i2 >= n - 40:
            between = [p for j, p in lows if i1 < j < i2]
            if between and min(between) <= max(h1, h2) * 0.94 and price < max(h1, h2) * 0.99:
                mid_j, mid_p = min(((j, p) for j, p in lows if i1 < j < i2), key=lambda x: x[1])
                out.append({"name": "double top", "bias": "bearish",
                            "lines": [[{"t": _t(d, i1), "p": round(h1, 2)}, {"t": _t(d, mid_j), "p": round(mid_p, 2)},
                                       {"t": _t(d, i2), "p": round(h2, 2)}]],
                            "note": "buyers failed twice at the same area (M shape) — supply capped it both "
                                    "times; breaking the middle low confirms the top"})
                break

    # --- Cup and handle: decline 12-35%, rounded low mid-cup, recovery near left rim, small tight handle
    if len(d) >= 60:
        cup = d.iloc[-110:-10] if len(d) >= 110 else d.iloc[:-10]
        if len(cup) >= 40:
            left_rim = float(cup["High"].iloc[: len(cup) // 4].max())
            low = float(cup["Low"].min())
            low_pos = int(cup["Low"].values.argmin())
            depth = (left_rim - low) / left_rim
            right = float(cup["Close"].iloc[-1])
            handle = d.iloc[-10:]
            handle_drop = (float(handle["High"].max()) - float(handle["Low"].min())) / float(handle["High"].max())
            if (0.12 <= depth <= 0.35 and len(cup) // 5 <= low_pos <= 4 * len(cup) // 5
                    and right >= left_rim * 0.95 and handle_drop <= 0.12
                    and float(handle["Volume"].mean()) < float(d["Volume"].mean())):
                cup_off = len(d) - len(d.iloc[-110:-10] if len(d) >= 110 else d.iloc[:-10]) - 10
                out.append({"name": "cup and handle", "bias": "bullish", "pivot": round(left_rim, 2),
                            "lines": [[{"t": _t(cup, 0), "p": round(left_rim, 2)},
                                       {"t": _t(cup, low_pos), "p": round(low, 2)},
                                       {"t": _t(cup, len(cup) - 1), "p": round(right, 2)},
                                       {"t": _t(handle, len(handle) - 1), "p": round(float(handle["Low"].min()), 2)}]],
                            "note": "rounded recovery back to the old high with a quiet, shallow pullback "
                                    "(the handle) — weak holders shaken out on low volume; the classic "
                                    "O'Neil launch pattern. Buy point is the handle high / left rim"})

    # --- Ascending triangle: flat top (2+ highs within 2%) + rising lows
    if len(highs) >= 2 and len(lows) >= 2:
        recent_h = [p for j, p in highs if j >= n - 60]
        recent_l = [(j, p) for j, p in lows if j >= n - 60]
        if len(recent_h) >= 2 and len(recent_l) >= 2:
            flat_top = abs(max(recent_h) - min(recent_h)) / max(recent_h) <= 0.02
            rising = all(recent_l[k + 1][1] > recent_l[k][1] for k in range(len(recent_l) - 1))
            if flat_top and rising and price >= min(recent_h) * 0.94:
                lows_line = [{"t": _t(d, j), "p": round(p, 2)} for j, p in recent_l]
                out.append({"name": "ascending triangle", "bias": "bullish", "pivot": round(max(recent_h), 2),
                            "lines": [lows_line,
                                      [{"t": lows_line[0]["t"], "p": round(max(recent_h), 2)},
                                       {"t": _t(d, n - 1), "p": round(max(recent_h), 2)}]],
                            "note": "buyers keep stepping in at higher prices (rising lows) while a seller "
                                    "sits at a fixed level (flat top) — when that supply is absorbed, the "
                                    "break tends to be sharp"})

    # --- Head & shoulders: middle peak >=3% above shoulders, shoulders within 4%
    if len(highs) >= 3:
        h3 = highs[-3:]
        (j1, s1), (j2, hd), (j3, s2) = h3
        if hd > s1 * 1.03 and hd > s2 * 1.03 and abs(s1 - s2) / s1 <= 0.04 and j3 >= n - 30:
            neck = [p for j, p in lows if j1 < j < j3]
            if neck and price < hd * 0.97:
                neckline = round(min(neck), 2)
                out.append({"name": "head and shoulders", "bias": "bearish",
                            "lines": [[{"t": _t(d, j1), "p": round(s1, 2)}, {"t": _t(d, j2), "p": round(hd, 2)},
                                       {"t": _t(d, j3), "p": round(s2, 2)}],
                                      [{"t": _t(d, j1), "p": neckline}, {"t": _t(d, n - 1), "p": neckline}]],
                            "note": "a higher high (head) that couldn't hold, followed by a LOWER high "
                                    "(right shoulder) — buyers are exhausting; breaking the neckline "
                                    "between the shoulders confirms distribution"})

    # --- High tight flag (Zanger/O'Neil): ~2x run in <=8 weeks, then a 1-3 week
    # pullback holding within 25% of the high on drying volume. Rarest and most
    # explosive continuation pattern; supersedes a plain bull flag.
    if len(d) >= 55:
        hi = d["High"].values
        pk_i = int(hi[-30:].argmax()) + n - 30
        peak = float(hi[pk_i])
        flag_len = n - 1 - pk_i
        if 4 <= flag_len <= 15:
            pole = d.iloc[max(0, pk_i - 40):pk_i]
            flag = d.iloc[pk_i:]
            if len(pole) >= 15:
                pole_low = float(pole["Low"].min())
                pole_low_i = max(0, pk_i - 40) + int(pole["Low"].values.argmin())
                gain = peak / pole_low - 1
                drop = (peak - float(flag["Low"].min())) / peak
                if (gain >= 0.90 and drop <= 0.25 and price >= peak * 0.78
                        and float(flag["Volume"].mean()) < float(pole["Volume"].mean())):
                    out.append({"name": "high tight flag", "bias": "bullish", "pivot": round(peak, 2),
                                "lines": [[{"t": _t(d, pole_low_i), "p": round(pole_low, 2)},
                                           {"t": _t(d, pk_i), "p": round(peak, 2)}],
                                          [{"t": _t(d, pk_i), "p": round(peak, 2)},
                                           {"t": _t(d, n - 1), "p": round(float(flag["Low"].min()), 2)}]],
                                "note": f"up {round(gain * 100)}% in under 8 weeks, then only a "
                                        f"{round(drop * 100)}% pullback on drying volume — the high tight "
                                        f"flag (Zanger/O'Neil), the rarest and most explosive continuation "
                                        f"pattern; buy the break over {round(peak, 2)}"})

    # --- Bull flag: >=20% pop in <=15 bars, then 3-10 bars of tight downward drift
    if len(d) >= 30 and not any(p["name"] == "high tight flag" for p in out):
        for span in (10, 15):
            pole_end = n - 8
            pole_start = pole_end - span
            if pole_start < 0:
                continue
            gain = float(d["Close"].iloc[pole_end]) / float(d["Close"].iloc[pole_start]) - 1
            flag = d.iloc[pole_end:]
            if len(flag) >= 3 and gain >= 0.20:
                drop = (float(flag["High"].max()) - float(flag["Low"].min())) / float(flag["High"].max())
                if drop <= 0.10 and price >= float(flag["Low"].min()):
                    out.append({"name": "bull flag", "bias": "bullish",
                                "pivot": round(float(flag["High"].max()), 2),
                                "lines": [[{"t": _t(d, pole_start), "p": round(float(d["Close"].iloc[pole_start]), 2)},
                                           {"t": _t(d, pole_end), "p": round(float(d["Close"].iloc[pole_end]), 2)}],
                                          [{"t": _t(flag, 0), "p": round(float(flag["High"].max()), 2)},
                                           {"t": _t(flag, len(flag) - 1), "p": round(float(flag["Low"].min()), 2)}]],
                                "note": "sharp advance followed by a quiet, shallow drift — winners resting, "
                                        "not selling off; flags tend to resolve in the direction of the pole"})
                    break

    # dedupe by name
    seen, unique = set(), []
    for p in out:
        if p["name"] not in seen:
            seen.add(p["name"])
            unique.append(p)
    return unique
