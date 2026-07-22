"""Build plain-English reasoning + R-multiple targets for each candidate.

Note on "target price": Minervini doesn't forecast prices. The honest mechanical
equivalent is R-multiple targets — if you risk $1/share (entry minus stop), 2R
means the price where you've made $2 for that $1 risked. Sell into strength
around 2R-3R or trail the stop; these are profit-taking levels, not predictions.
"""
from __future__ import annotations

def targets(entry: float, stop: float) -> dict:
    risk = entry - stop
    if risk <= 0:
        return {"risk_per_share": None, "target_2r": None, "target_3r": None}
    return {
        "risk_per_share": round(risk, 2),
        "risk_pct": round(risk / entry * 100, 1),
        "target_2r": round(entry + 2 * risk, 2),
        "target_3r": round(entry + 3 * risk, 2),
    }


def build(c: dict) -> str:
    """One readable paragraph explaining WHY this stock is on the list."""
    checks = c.get("checks", {})
    vcp = c.get("vcp", {})
    ext = c.get("extension", {})
    tgt = c.get("targets", {})
    lines: list[str] = []

    setup = c.get("setup") or {}
    if c.get("bucket") == "forming":
        lines.append(
            f"NOT a buy yet — {setup.get('rules_passed', '?')}/8 Trend Template rules pass "
            f"({setup.get('progress_pct', '?')}% complete). This is an EARLY-DETECTION pick: "
            f"get it on your radar before the crowd sees it."
        )
        for n in setup.get("needs", []):
            lines.append(f"Missing: {n}.")
    if setup.get("pocket_pivot"):
        lines.append(
            "🔥 Pocket pivot today: an up day on volume bigger than any down day of the past "
            "2 weeks — institutional buying showing up INSIDE the base, often days before the breakout."
        )
    for key, label in (("ma20_bounce", "20MA"), ("ma50_bounce", "50MA")):
        b = setup.get(key)
        if b:
            lines.append(
                f"🪃 {label} bounce today: tagged the rising {label[:2]}-day line on light volume and "
                f"reclaimed it on a strong close — a low-risk pullback entry. Entry ~${b['trigger']} "
                f"(next open), stop ${b['stop']} (-{b['risk_pct']}% risk, under the pullback low)."
            )
    ep = setup.get("episodic_pivot")
    if ep:
        lines.append(
            f"⚡ EPISODIC PIVOT: gapped {ep['gap_pct']}% and closed +{ep['chg_pct']}% on "
            f"{ep['vol_x']}x volume out of neglect. Entry on a break above ${ep['trigger']}, "
            f"stop ${ep['stop']} (-{ep['risk_pct']}%). VERIFY THE CATALYST first — a gap this "
            f"size without earnings/news behind it is suspect."
        )
    burst = setup.get("momentum_burst")
    if burst:
        lines.append(
            f"💥 Momentum burst: +{burst['chg_pct']}% on {burst['vol_x']}x volume out of a quiet "
            f"base — early footprint of a new momentum phase; watch for a proper setup, don't chase."
        )
    if setup.get("tightening"):
        lines.append(
            f"Coiling now: the last 10 days' range is {setup.get('range_ratio')}x the prior 10 days' "
            f"while holding near the highs — the base is maturing; a pivot should form soon."
        )
    pat = c.get("patterns") or {}
    cur_sym = "$" if c.get("market") != "MY" else "RM"
    for cp in pat.get("chart_patterns", []):
        arrow = "bullish because" if cp["bias"] == "bullish" else "bearish because"
        pv = ""
        if cp.get("pivot"):
            # a trigger the price already cleared is history, not an entry —
            # presenting it as pending was flat-out wrong on the dashboard
            pv = (f" Watch {cur_sym}{cp['pivot']} as the trigger."
                  if cp["pivot"] > c["price"] else
                  f" Trigger {cur_sym}{cp['pivot']} already cleared — confirmation of the pattern, not an entry.")
        lines.append(f"Chart pattern — {cp['name'].upper()} ({cp['bias']}): {arrow} {cp['note']}.{pv}")
    if pat.get("narrative"):
        lines.append("Price action: " + pat["narrative"])

    # 1. Trend template
    low = checks.get("above_52w_low_30pct", {})
    high = checks.get("within_25pct_of_52w_high", {})
    if c.get("bucket") != "forming":
        lines.append(
        f"Passes all 8 Minervini Trend Template rules: price ${c['price']} is above its "
        f"rising 50/150/200-day moving averages, {low.get('pct_above_low', '?')}% above the "
        f"52-week low and only {high.get('pct_below_high', '?')}% below the 52-week high — "
        f"a confirmed Stage-2 uptrend."
    )

    # 2. Relative strength
    pool = c.get("rs_pool")
    if c.get("market") == "MY":
        lines.append(
            f"RS rank {c['rs_rank']}/99 — outperforming {c['rs_rank']}% of the "
            f"{pool or '~75'} Bursa counters we track over the past 3-12 months. "
            f"Note: this is a small, curated pool, so treat Bursa RS as a rough guide, "
            f"not the institutional-grade signal it is on the US side."
        )
    else:
        lines.append(
            f"RS rank {c['rs_rank']}/99 — outperforming {c['rs_rank']}% of "
            f"{('all ' + format(pool, ',') + ' liquid') if pool else 'all'} US stocks "
            f"over the past 3-12 months (institutional-quality momentum)."
        )

    # 2b. Setup quality, movement, group strength
    qbits = []
    if c.get("quality") is not None:
        qbits.append(f"setup quality {c['quality']}/100")
    if c.get("adr_pct") is not None:
        qbits.append(f"moves {c['adr_pct']}%/day on average")
    g = c.get("group_rs")
    if g is not None and c.get("industry"):
        tier = "leading" if g >= 75 else ("mid-pack" if g >= 40 else "lagging")
        qbits.append(f"industry group ({c['industry']}) RS {g}/99 — {tier}")
        if g < 40:
            qbits.append("⚠️ weak group: even strong stocks struggle when their group lags")
    if qbits:
        lines.append("Quality: " + "; ".join(qbits) + ".")

    # 3. VCP / setup
    contr = vcp.get("contractions_pct") or []
    if vcp.get("vcp"):
        seq = " → ".join(f"{x}%" for x in contr)
        vol_note = ""
        if vcp.get("vol_50d_avg"):
            ratio = round(vcp["vol_5d_avg"] / vcp["vol_50d_avg"] * 100)
            vol_note = f" and volume has dried up to {ratio}% of normal"
        lines.append(
            f"VCP detected: pullbacks tightened {seq}{vol_note} — supply is being "
            f"absorbed, which is what strong bases look like before a breakout. "
            f"Buy point (pivot): ${vcp.get('pivot')}."
        )
    elif contr:
        lines.append(
            f"Base still forming (pullback sequence {' → '.join(f'{x}%' for x in contr)}); "
            f"no valid pivot yet — watch, don't buy."
        )
    else:
        lines.append("Strong trend but no tight base/VCP detected — qualifies on trend and RS only.")

    # 4. Risk plan — only when the entry is actually actionable: a swing pick,
    # or a VALID VCP whose pivot price hasn't run past. A "pivot" without a
    # valid base is just the last swing high; printing it as an entry (even
    # 1% below market) contradicted the "watch, don't buy" line above it.
    pivot = c.get("pivot")
    actionable = bool(
        c.get("stop") and tgt.get("target_2r") and pivot
        and (c.get("bucket") == "swing"
             or (vcp.get("vcp") and c["price"] <= pivot * 1.02))
    )
    if actionable:
        lines.append(
            f"Plan: entry ~${pivot}, stop ${c['stop']} "
            f"(-{tgt.get('risk_pct')}% risk). Profit-taking levels: ${tgt['target_2r']} (2R) "
            f"and ${tgt['target_3r']} (3R) — sell into strength or trail the stop. "
            f"These are risk-based levels, not price predictions."
        )
    elif c.get("stop") and tgt.get("target_2r"):
        if pivot and c["price"] > pivot:
            lines.append(
                f"No actionable entry: price ${c['price']} has already cleared the last swing high "
                f"(${pivot}) without a valid setup — no defined edge here. Wait for a new base "
                f"and a fresh pivot before planning a trade."
            )
        elif pivot:
            lines.append(
                f"No actionable entry yet: the base is still forming — ${pivot} is the last "
                f"swing high, not a confirmed buy point. Levels are reference only until the "
                f"setup completes."
            )
        else:
            lines.append(
                "No actionable entry yet: no valid pivot — stop and target levels only become "
                "meaningful once a base completes and sets a real buy point."
            )

    # 4b. Support / resistance context
    lv = c.get("levels") or {}
    sup = [l for l in lv.get("supports", []) if l["strength"] == "strong"]
    res = [l for l in lv.get("resistances", []) if l["strength"] == "strong"]
    bits = []
    if sup:
        bits.append(f"strong support zone ${sup[0]['low']}-{sup[0]['high']} ({sup[0]['touches']} touches)")
    if res:
        bits.append(f"overhead resistance zone ${res[0]['low']}-{res[0]['high']} ({res[0]['touches']} touches)")
    else:
        if lv.get("resistances") == [] and lv:
            bits.append("no overhead resistance in the past year — blue-sky territory")
    if bits:
        lines.append("Levels: " + "; ".join(bits) + ".")

    # 5. Warnings
    if c.get("extended"):
        past = ext.get("pct_above_pivot")
        missed = vcp.get("pivot")
        why = (
            f"\u26A0\uFE0F EXTENDED — don't chase. "
            + (f"The proper entry was the pivot at ${missed}; price is already {past}% past it. "
               if missed and past is not None
               else f"Price is {ext.get('pct_above_ma50')}% above its 50-day MA. ")
            + "Why it matters: a valid stop still belongs under the base, so buying here makes "
            "your risk-per-share much bigger — either you risk way more than planned or you take "
            "a position too small to matter. And stocks routinely pull back to retest the "
            "breakout area, which would shake you out of a chase entry at a loss even when the "
            "stock ultimately works."
        )
        lines.append(why)
        lines.append(
            "If you still want in: (1) best option — wait for the next proper setup: a pullback "
            "that holds the rising 20-day MA on light volume, or a new 3-5 week base forming a "
            "fresh pivot; (2) if you absolutely must chase, size down so the wider stop still "
            "risks \u22641% of your account, and never pay more than ~5% above the original pivot "
            "— beyond that, let it go. Missing a move costs nothing; chasing one costs money."
        )
    earnings = c.get("earnings") or {}
    if earnings.get("high_risk"):
        lines.append(
            f"⚠️ Earnings on {earnings.get('date')} ({earnings.get('days_away')} days) — "
            f"a breakout can gap straight through your stop on results. High risk to enter now."
        )


    return "\n".join(lines)


SECTION_DEFS = [
    ("signals", "Signals right now", "fire"),
    ("trend", "Trend & momentum", "info"),
    ("setup", "Setup & base", "info"),
    ("plan", "Trade plan", "plan"),
    ("levels", "Key levels", "info"),
    ("warnings", "Warnings", "warn"),
]


def build_sections(c: dict) -> list[dict]:
    """Grouped, readable version of the reasoning. Each section:
    {key, title, tone, lines}. Empty sections are dropped."""
    checks = c.get("checks", {})
    vcp = c.get("vcp", {})
    ext = c.get("extension", {})
    tgt = c.get("targets", {})
    setup = c.get("setup") or {}
    pat = c.get("patterns") or {}
    cur = "RM" if c.get("market") == "MY" else "$"
    S = {k: [] for k, _, _ in SECTION_DEFS}

    # --- signals right now
    if setup.get("pocket_pivot"):
        S["signals"].append("🔥 Pocket pivot today — up day on volume bigger than any down day of the "
                            "past 2 weeks. Institutional buying inside the base, often days before a breakout.")
    for key, label in (("ma20_bounce", "20-day"), ("ma50_bounce", "50-day")):
        b = setup.get(key)
        if b:
            S["signals"].append(f"🪃 {label} MA bounce — tagged the rising line on light volume, "
                                f"reclaimed on a strong close. Entry ~{cur}{b['trigger']} (next open) "
                                f"· stop {cur}{b['stop']} (-{b['risk_pct']}%).")
    ep = setup.get("episodic_pivot")
    if ep:
        S["signals"].append(f"⚡ EPISODIC PIVOT — gapped {ep['gap_pct']}%, closed +{ep['chg_pct']}% on "
                            f"{ep['vol_x']}x volume out of neglect. Entry above {cur}{ep['trigger']} · "
                            f"stop {cur}{ep['stop']} (-{ep['risk_pct']}%). Verify the catalyst first — "
                            f"no catalyst, no trade.")
    burst = setup.get("momentum_burst")
    if burst:
        S["signals"].append(f"💥 Momentum burst — +{burst['chg_pct']}% on {burst['vol_x']}x volume from "
                            f"a quiet base. Early footprint; watch for a setup, don't chase.")
    if setup.get("tightening"):
        S["signals"].append(f"🌀 Coiling — last 10 days' range is {setup.get('range_ratio')}x the prior 10 "
                            f"while holding near highs. The base is maturing.")
    for cp in pat.get("chart_patterns", []):
        pv = ""
        if cp.get("pivot"):
            pv = (f" Trigger: {cur}{cp['pivot']}." if cp["pivot"] > c["price"] else
                  f" Trigger {cur}{cp['pivot']} already cleared — confirmation, not an entry.")
        S["signals"].append(f"{'📈' if cp['bias'] == 'bullish' else '📉'} {cp['name'].upper()} "
                            f"({cp['bias']}) — {cp['note']}.{pv}")
    if pat.get("narrative"):
        S["signals"].append("Price action: " + pat["narrative"])

    # --- trend & momentum
    low = checks.get("above_52w_low_30pct", {})
    high = checks.get("within_25pct_of_52w_high", {})
    if c.get("bucket") == "forming":
        S["trend"].append(f"⚠️ NOT a buy yet — {setup.get('rules_passed', '?')}/8 Trend Template rules pass "
                          f"({setup.get('progress_pct', '?')}% ready). Early-detection radar only.")
        for n in setup.get("needs", []):
            S["trend"].append(f"Missing: {n}.")
    else:
        S["trend"].append(f"✅ All 8 Trend Template rules pass: price {cur}{c['price']} above rising "
                          f"50/150/200-day MAs, {low.get('pct_above_low', '?')}% above the 52-week low, "
                          f"{high.get('pct_below_high', '?')}% below the 52-week high — confirmed Stage-2 uptrend.")
    pool = c.get("rs_pool")
    if c.get("market") == "MY":
        S["trend"].append(f"RS {c['rs_rank']}/99 vs the {pool or '~75'} Bursa counters tracked "
                          f"(small pool — rough guide, not a US-grade signal).")
    else:
        S["trend"].append(f"RS {c['rs_rank']}/99 — stronger than {c['rs_rank']}% of "
                          f"{format(pool, ',') if pool else 'all'} liquid US stocks over 3-12 months.")
    g = c.get("group_rs")
    if g is not None and c.get("industry"):
        tier = "leading" if g >= 75 else ("mid-pack" if g >= 40 else "lagging")
        S["trend"].append(f"Industry group ({c['industry']}): RS {g}/99 — {tier}."
                          + (" ⚠️ Weak group: even strong stocks struggle when their group lags." if g < 40 else ""))
    bc = setup.get("base_count")
    if bc and bc.get("count"):
        S["trend"].append(f"Base #{bc['count']} of this uptrend — {bc['stage']}."
                          + (" ⚠️ Late-stage bases fail more often; demand extra setup quality and keep the stop tight."
                             if bc["count"] >= 3 else ""))

    # --- setup & base
    ant = setup.get("anticipation")
    if ant:
        S["setup"].append(f"🎯 Maturing setup — {ant['pct_to_pivot']}% below the pivot, anticipation "
                          f"score {ant['score']}/100. Get the order ready before the crowd sees the breakout.")
    if c.get("quality") is not None:
        S["setup"].append(f"Setup quality {c['quality']}/100 · moves {c.get('adr_pct', '?')}%/day on average.")
    contr = vcp.get("contractions_pct") or []
    if vcp.get("vcp"):
        seq = " → ".join(f"{x}%" for x in contr)
        vol_note = ""
        if vcp.get("vol_50d_avg"):
            ratio = round(vcp["vol_5d_avg"] / vcp["vol_50d_avg"] * 100)
            vol_note = f", volume dried up to {ratio}% of normal"
        S["setup"].append(f"VCP: pullbacks tightened {seq}{vol_note} — supply absorbed. "
                          f"Buy point (pivot): {cur}{vcp.get('pivot')}.")
    elif contr:
        S["setup"].append(f"Base still forming (pullbacks: {' → '.join(f'{x}%' for x in contr)}); "
                          f"no valid pivot yet — watch, don't buy.")
    else:
        S["setup"].append("No tight base/VCP detected — qualifies on trend and RS only.")

    # --- plan (same actionability gate as build() — no stale-pivot "entries")
    ee = setup.get("early_entry")
    if ee:
        S["plan"].append(f"⚡ EARLY entry available: {cur}{ee['trigger']} trigger, stop {cur}{ee['stop']} "
                         f"(-{ee['risk_pct']}%). {ee['note']}")
    pivot = c.get("pivot")
    actionable = bool(
        c.get("stop") and tgt.get("target_2r") and pivot
        and (c.get("bucket") == "swing"
             or (vcp.get("vcp") and c["price"] <= pivot * 1.02))
    )
    if actionable:
        S["plan"].append(f"Entry ~{cur}{pivot} · stop {cur}{c['stop']} "
                         f"(-{tgt.get('risk_pct')}% risk) · targets {cur}{tgt['target_2r']} (2R) / "
                         f"{cur}{tgt['target_3r']} (3R). Sell into strength or trail. "
                         f"Risk-based levels, not predictions.")
    elif c.get("stop") and tgt.get("target_2r"):
        if pivot and c["price"] > pivot:
            S["plan"].append(f"⏳ No actionable entry — price {cur}{c['price']} already cleared the last "
                             f"swing high ({cur}{pivot}) without a valid setup. Wait for a new base and "
                             f"a fresh pivot; the levels here are reference only.")
        elif pivot:
            S["plan"].append(f"⏳ No actionable entry yet — base still forming; {cur}{pivot} is the last "
                             f"swing high, not a confirmed buy point. Levels are reference only until "
                             f"the setup completes.")
        else:
            S["plan"].append("⏳ No actionable entry yet — no valid pivot. Stop/targets become meaningful "
                             "once a base completes and sets a real buy point.")

    # --- levels
    lv = c.get("levels") or {}
    sup = [l for l in lv.get("supports", []) if l.get("strength") == "strong"]
    res = [l for l in lv.get("resistances", []) if l.get("strength") == "strong"]
    if sup:
        S["levels"].append(f"Strong support zone {cur}{sup[0]['low']}–{sup[0]['high']} "
                           f"({sup[0]['touches']} touches, weighted {sup[0].get('weighted_touches', sup[0]['touches'])} — recent count ×2).")
    if res:
        S["levels"].append(f"Overhead resistance zone {cur}{res[0]['low']}–{res[0]['high']} "
                           f"({res[0]['touches']} touches, weighted {res[0].get('weighted_touches', res[0]['touches'])} — recent count ×2).")
    elif lv and not lv.get("resistances"):
        S["levels"].append("No overhead resistance in the past year — blue-sky territory (ideal for breakouts).")

    # --- warnings
    if c.get("extended"):
        S["warnings"].append(f"EXTENDED — already {ext.get('pct_above_pivot') or ext.get('pct_above_ma50')}% "
                             f"past the buy point. Chasing wrecks risk/reward; wait for a pullback that holds "
                             f"the 21/50-day MA or a new base — or size DOWN so the wider stop still risks ≤1%.")

    return [{"key": k, "title": t, "tone": tone, "lines": S[k]}
            for k, t, tone in SECTION_DEFS if S[k]]
