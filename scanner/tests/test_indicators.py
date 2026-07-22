"""Unit tests for the screening engine using synthetic price data."""
import numpy as np
import pandas as pd
import pytest

from scanner import indicators, sectors


def make_df(closes: np.ndarray, volumes: np.ndarray | None = None) -> pd.DataFrame:
    n = len(closes)
    idx = pd.bdate_range(end="2026-07-08", periods=n)
    if volumes is None:
        volumes = np.full(n, 1_000_000.0)
    return pd.DataFrame({
        "Open": closes * 0.995,
        "High": closes * 1.01,
        "Low": closes * 0.99,
        "Close": closes,
        "Volume": volumes,
    }, index=idx)


def steady_uptrend(n: int = 300, start: float = 50.0, daily: float = 0.004) -> pd.DataFrame:
    closes = start * (1 + daily) ** np.arange(n)
    return make_df(closes)


def steady_downtrend(n: int = 300, start: float = 200.0, daily: float = 0.003) -> pd.DataFrame:
    closes = start * (1 - daily) ** np.arange(n)
    return make_df(closes)


class TestRSFormula:
    def test_exact_math(self):
        # Constructed so the offsets are known exactly
        closes = np.ones(300) * 100.0
        df = make_df(closes)
        # flat series: every ratio is 1 -> raw = 0.4+0.2+0.2+0.2 = 1.0
        assert indicators.rs_raw(df) == pytest.approx(1.0)

    def test_uptrend_beats_downtrend(self):
        up = indicators.rs_raw(steady_uptrend())
        down = indicators.rs_raw(steady_downtrend())
        assert up > 1.0 > down

    def test_insufficient_history(self):
        assert indicators.rs_raw(steady_uptrend(n=100)) is None

    def test_ranks_span(self):
        raw = {f"T{i}": 0.5 + i * 0.01 for i in range(100)}
        ranks = indicators.rs_ranks(raw)
        assert min(ranks.values()) >= 1 and max(ranks.values()) <= 99
        assert ranks["T99"] > ranks["T0"]


class TestTrendTemplate:
    def test_uptrend_passes_all(self):
        tt = indicators.trend_template(steady_uptrend(), rs_rank=90)
        assert tt["eligible"] and tt["pass_all"], tt

    def test_downtrend_fails(self):
        tt = indicators.trend_template(steady_downtrend(), rs_rank=90)
        assert tt["eligible"] and not tt["pass_all"]
        assert not tt["checks"]["price_above_150_200"]["pass"]

    def test_low_rs_fails_even_in_uptrend(self):
        tt = indicators.trend_template(steady_uptrend(), rs_rank=50)
        assert not tt["checks"]["rs_rank_ge_70"]["pass"]
        assert not tt["pass_all"]

    def test_values_are_reported(self):
        tt = indicators.trend_template(steady_uptrend(), rs_rank=90)
        c = tt["checks"]["above_52w_low_30pct"]
        assert c["price"] > c["low52"]
        assert "pct_above_low" in c


class TestVCPAndExtension:
    def test_extension_flags(self):
        df = steady_uptrend()
        price = float(df["Close"].iloc[-1])
        ext = indicators.extension_flags(df, pivot=price / 1.10)  # 10% past pivot
        assert ext["extended"] and ext["pct_above_pivot"] > 5

    def test_not_extended_near_pivot(self):
        # gentle uptrend stays close to its 50MA; pivot 2% above price
        df = steady_uptrend(daily=0.001)
        price = float(df["Close"].iloc[-1])
        ext = indicators.extension_flags(df, pivot=price * 1.02)
        assert not ext["extended"]

    def test_vcp_needs_data(self):
        assert indicators.detect_vcp(steady_uptrend(n=30))["vcp"] is False


class TestStopsAndExits:
    def test_stop_never_wider_than_8pct(self):
        df = steady_uptrend()
        entry = float(df["Close"].iloc[-1])
        stop = indicators.suggested_stop(df, entry)
        assert stop >= entry * 0.92 - 0.01
        assert stop < entry

    def test_stop_violation_triggers(self):
        df = steady_downtrend()
        price = float(df["Close"].iloc[-1])
        sig = indicators.exit_signals(df, entry=price * 2, stop=price * 1.5, pivot=None)
        assert sig["stop_violated"]["triggered"]
        assert sig["below_50ma"]["triggered"]

    def test_healthy_position_no_stop_signal(self):
        df = steady_uptrend()
        price = float(df["Close"].iloc[-1])
        sig = indicators.exit_signals(df, entry=price * 0.9, stop=price * 0.85, pivot=price * 0.9)
        assert not sig["stop_violated"]["triggered"]
        assert not sig["below_50ma"]["triggered"]
        assert not sig["failed_breakout"]["triggered"]


class TestSectors:
    def test_quadrants(self):
        assert sectors.classify_quadrant(0.02, 0.05) == "leading"
        assert sectors.classify_quadrant(-0.02, 0.05) == "weakening"
        assert sectors.classify_quadrant(0.02, -0.05) == "improving"
        assert sectors.classify_quadrant(-0.02, -0.05) == "lagging"

    def test_rotation_output(self):
        data = {etf: steady_uptrend() for etf in list(sectors.SECTOR_ETFS)[:3]}
        spy = steady_uptrend(daily=0.002)
        rows = sectors.sector_rotation(data, spy)
        assert len(rows) == 3
        assert rows[0]["rank"] == 1
        assert all(r["quadrant"] in {"leading", "weakening", "improving", "lagging"} for r in rows)


class TestReasoningAndFundamentals:
    def test_targets_math(self):
        from scanner import reasoning
        t = reasoning.targets(entry=100.0, stop=92.0)
        assert t["risk_per_share"] == 8.0
        assert t["target_2r"] == 116.0
        assert t["target_3r"] == 124.0
        assert t["risk_pct"] == 8.0

    def test_targets_bad_stop(self):
        from scanner import reasoning
        t = reasoning.targets(entry=100.0, stop=105.0)
        assert t["target_2r"] is None

    def test_reasoning_text_contains_key_facts(self):
        from scanner import reasoning
        c = {
            "ticker": "TEST", "price": 142.5, "rs_rank": 94, "pivot": 145.3,
            "stop": 134.1, "extended": False,
            "checks": {
                "above_52w_low_30pct": {"pct_above_low": 34.0},
                "within_25pct_of_52w_high": {"pct_below_high": 4.0},
            },
            "vcp": {"vcp": True, "contractions_pct": [18.0, 9.0, 5.0],
                    "vol_5d_avg": 450000, "vol_50d_avg": 1000000, "pivot": 145.3},
            "extension": {}, "targets": reasoning.targets(145.3, 134.1),
            "earnings": {"high_risk": True, "date": "2026-07-14", "days_until": 4},
        }
        text = reasoning.build(c)
        assert "8 Minervini Trend Template rules" in text
        assert "RS rank 94" in text
        assert "18.0% → 9.0% → 5.0%" in text
        assert "2R" in text and "3R" in text

    def test_extended_reasoning_explains_chase(self):
        from scanner import reasoning
        c = {
            "ticker": "HOT", "price": 160.0, "rs_rank": 95, "pivot": 145.3,
            "stop": 147.2, "extended": True,
            "checks": {"above_52w_low_30pct": {"pct_above_low": 80.0},
                       "within_25pct_of_52w_high": {"pct_below_high": 1.0}},
            "vcp": {"vcp": True, "contractions_pct": [12.0, 6.0],
                    "vol_5d_avg": 50, "vol_50d_avg": 100, "pivot": 145.3},
            "extension": {"pct_above_pivot": 10.1, "pct_above_ma50": 28.0, "extended": True},
            "targets": reasoning.targets(145.3, 134.1), "earnings": None,
        }
        text = reasoning.build(c)
        assert "don't chase" in text
        assert "$145.3" in text                     # the missed entry
        assert "10.1% past it" in text              # how far it ran
        assert "If you still want in" in text       # the chase protocol
        assert "20-day MA" in text

    def test_no_fake_plan_when_pivot_already_cleared(self):
        # regression: "no valid pivot yet — watch, don't buy" was followed by
        # "Plan: entry ~RM5.15" while price was already 5.22 — a stale swing
        # high presented as a buyable entry
        from scanner import reasoning
        c = {
            "ticker": "STALE", "price": 5.22, "rs_rank": 85, "pivot": 5.15,
            "stop": 4.85, "extended": False, "bucket": "watchlist", "market": "MY",
            "checks": {"above_52w_low_30pct": {"pct_above_low": 30.6},
                       "within_25pct_of_52w_high": {"pct_below_high": 1.1}},
            "vcp": {"vcp": False, "contractions_pct": [4.1, 5.7, 8.0], "pivot": 5.15},
            "extension": {}, "targets": reasoning.targets(5.15, 4.85), "earnings": None,
        }
        text = reasoning.build(c)
        assert "No actionable entry" in text
        assert "Plan: entry" not in text
        plan = next(s for s in reasoning.build_sections(c) if s["key"] == "plan")
        assert any("No actionable entry" in line for line in plan["lines"])
        assert not any(line.startswith("Entry ~") for line in plan["lines"])

    def test_pattern_trigger_marked_cleared_when_price_above(self):
        from scanner import reasoning
        c = {
            "ticker": "DB", "price": 5.22, "rs_rank": 85, "pivot": None,
            "stop": 4.85, "extended": False, "bucket": "watchlist", "market": "MY",
            "checks": {"above_52w_low_30pct": {"pct_above_low": 30.6},
                       "within_25pct_of_52w_high": {"pct_below_high": 1.1}},
            "vcp": {"vcp": False, "contractions_pct": []},
            "extension": {}, "targets": reasoning.targets(5.15, 4.85), "earnings": None,
            "patterns": {"chart_patterns": [
                {"name": "double bottom", "bias": "bullish", "pivot": 4.99,
                 "note": "sellers failed twice at the same area"}]},
        }
        text = reasoning.build(c)
        assert "already cleared" in text
        assert "Watch RM4.99 as the trigger" not in text
        sections = reasoning.build_sections(c)
        signals = next(s for s in sections if s["key"] == "signals")
        assert any("already cleared" in line for line in signals["lines"])




class TestMa20Bounce:
    def _df(self, n=80):
        import numpy as np
        from scanner import indicators  # noqa: F401
        close = np.array([100 + 0.5 * i for i in range(n)], dtype=float)
        close[-4] = close[-5] - 1.5   # orderly 3-day pullback
        close[-3] = close[-4] - 1.5
        close[-2] = close[-3] - 1.0
        close[-1] = close[-2] + 2.5   # bounce day, strong close
        idx = pd.bdate_range("2025-01-02", periods=n)
        df = pd.DataFrame({"Open": close - 0.2, "High": close + 0.6, "Low": close - 0.6,
                           "Close": close, "Volume": np.full(n, 1_000_000.0)}, index=idx)
        ma20 = df["Close"].rolling(20).mean()
        # yesterday tags the line on LIGHT volume
        df.iloc[-2, df.columns.get_loc("Low")] = float(ma20.iloc[-2]) * 0.999
        df.iloc[-2, df.columns.get_loc("Volume")] = 500_000.0
        return df

    def test_detects_valid_bounce(self):
        from scanner import indicators
        df = self._df()
        mb = indicators.ma20_bounce(df)
        assert mb is not None
        assert mb["trigger"] == round(float(df["Close"].iloc[-1]), 2)
        assert mb["stop"] < mb["trigger"]
        assert 0 < mb["risk_pct"] <= 8
        # tag day was yesterday — the chart marker anchors to that date
        assert mb["tag_t"] == df.index[-2].strftime("%Y-%m-%d")

    def test_heavy_volume_tag_disqualifies(self):
        from scanner import indicators
        df = self._df()
        df.iloc[-2, df.columns.get_loc("Volume")] = 3_000_000.0  # distribution, not a gift
        assert indicators.ma20_bounce(df) is None

    def test_no_tag_no_bounce(self):
        import numpy as np
        from scanner import indicators
        n = 80
        close = np.array([100 + 0.5 * i for i in range(n)], dtype=float)  # never pulls back
        idx = pd.bdate_range("2025-01-02", periods=n)
        df = pd.DataFrame({"Open": close - 0.2, "High": close + 0.6, "Low": close - 0.6,
                           "Close": close, "Volume": np.full(n, 1_000_000.0)}, index=idx)
        assert indicators.ma20_bounce(df) is None

    def test_falling_ma_disqualifies(self):
        import numpy as np
        from scanner import indicators
        n = 80
        close = np.array([140 - 0.5 * i for i in range(n)], dtype=float)  # downtrend
        idx = pd.bdate_range("2025-01-02", periods=n)
        df = pd.DataFrame({"Open": close, "High": close + 0.6, "Low": close - 0.6,
                           "Close": close, "Volume": np.full(n, 1_000_000.0)}, index=idx)
        assert indicators.ma20_bounce(df) is None


class TestMa50Bounce:
    def test_detects_valid_bounce_at_50ma(self):
        import numpy as np
        from scanner import indicators
        n = 140
        close = np.array([100 + 0.5 * i for i in range(n)], dtype=float)
        close[-4] = close[-5] - 2.0
        close[-3] = close[-4] - 2.0
        close[-2] = close[-3] - 1.5
        close[-1] = close[-2] + 3.0
        idx = pd.bdate_range("2025-01-02", periods=n)
        df = pd.DataFrame({"Open": close - 0.2, "High": close + 0.6, "Low": close - 0.6,
                           "Close": close, "Volume": np.full(n, 1_000_000.0)}, index=idx)
        ma50 = df["Close"].rolling(50).mean()
        df.iloc[-2, df.columns.get_loc("Low")] = float(ma50.iloc[-2]) * 0.999
        df.iloc[-2, df.columns.get_loc("Volume")] = 500_000.0
        mb = indicators.ma50_bounce(df)
        assert mb is not None
        assert mb["stop"] < mb["trigger"]
        assert mb["risk_pct"] <= 10


class TestEpisodicPivot:
    def _flat_then_gap(self, vol_x=4.0, gap=1.05, close_mult=1.08):
        import numpy as np
        n = 80
        close = np.full(n, 100.0)
        idx = pd.bdate_range("2025-01-02", periods=n)
        df = pd.DataFrame({"Open": close, "High": close + 0.4, "Low": close - 0.4,
                           "Close": close, "Volume": np.full(n, 1_000_000.0)}, index=idx)
        df.iloc[-1] = [100.0 * gap, 100.0 * close_mult + 0.5, 100.0 * gap - 0.5,
                       100.0 * close_mult, 1_000_000.0 * vol_x]
        return df

    def test_detects_gap_on_volume_from_neglect(self):
        from scanner import indicators
        ep = indicators.episodic_pivot(self._flat_then_gap())
        assert ep is not None
        assert ep["vol_x"] >= 3
        assert ep["gap_pct"] >= 4
        assert ep["stop"] < ep["trigger"]

    def test_normal_volume_disqualifies(self):
        from scanner import indicators
        assert indicators.episodic_pivot(self._flat_then_gap(vol_x=1.5)) is None

    def test_already_running_is_not_neglect(self):
        import numpy as np
        from scanner import indicators
        n = 80  # +50% over the window: momentum, not neglect
        close = np.array([100 * (1.006 ** i) for i in range(n)])
        idx = pd.bdate_range("2025-01-02", periods=n)
        df = pd.DataFrame({"Open": close, "High": close * 1.004, "Low": close * 0.996,
                           "Close": close, "Volume": np.full(n, 1_000_000.0)}, index=idx)
        df.iloc[-1] = [close[-2] * 1.05, close[-2] * 1.09, close[-2] * 1.045,
                       close[-2] * 1.08, 4_000_000.0]
        assert indicators.episodic_pivot(df) is None


class TestMomentumBurstAndAnticipation:
    def test_burst_from_quiet_base(self):
        import numpy as np
        from scanner import indicators
        n = 80
        close = np.full(n, 100.0)
        close[-1] = 104.5
        idx = pd.bdate_range("2025-01-02", periods=n)
        vol = np.full(n, 1_000_000.0); vol[-1] = 2_000_000.0
        df = pd.DataFrame({"Open": close - 0.2, "High": close + 0.5, "Low": close - 0.5,
                           "Close": close, "Volume": vol}, index=idx)
        b = indicators.momentum_burst(df)
        assert b is not None and b["chg_pct"] >= 4 and b["vol_x"] >= 1.5

    def test_noisy_base_disqualifies(self):
        import numpy as np
        from scanner import indicators
        n = 80  # already moving ±: 5-day pre-burst drift way over 3%
        close = np.array([100 + (i % 2) * 6 for i in range(n)], dtype=float)
        close[-1] = close[-2] * 1.05
        idx = pd.bdate_range("2025-01-02", periods=n)
        df = pd.DataFrame({"Open": close, "High": close + 0.5, "Low": close - 0.5,
                           "Close": close, "Volume": np.full(n, 1_000_000.0)}, index=idx)
        assert indicators.momentum_burst(df) is None

    def test_anticipation_scores_ready_bases_high(self):
        from scanner import indicators
        vcp = {"contractions_pct": [15.0, 8.0, 3.0], "volume_dry_up": True}
        a = indicators.anticipation(vcp, True, price=98.0, pivot=100.0)
        assert a is not None
        assert a["pct_to_pivot"] == 2.0
        assert a["score"] >= 80

    def test_anticipation_none_when_far_or_broken_out(self):
        from scanner import indicators
        vcp = {"contractions_pct": [15.0, 8.0], "volume_dry_up": False}
        assert indicators.anticipation(vcp, False, price=90.0, pivot=100.0) is None  # 10% away
        assert indicators.anticipation(vcp, False, price=101.0, pivot=100.0) is None  # above pivot
        assert indicators.anticipation({"contractions_pct": [15.0]}, True, 98.0, 100.0) is None


class TestBaseCount:
    def _staircase(self):
        import numpy as np
        # 200 flat-ish (below later MA), then rise / base / rise / base / rise
        seg = []
        seg += [100.0 - 0.02 * i for i in range(200)]          # drifts down: origin zone
        p = seg[-1]
        for _ in range(50): p *= 1.01; seg.append(p)            # advance 1
        for _ in range(25): seg.append(p * (1 - 0.001))         # base 1 (no new high)
        for _ in range(30): p *= 1.01; seg.append(p)            # advance 2
        for _ in range(25): seg.append(p * (1 - 0.001))         # base 2
        for _ in range(20): p *= 1.01; seg.append(p)            # advance 3 (current)
        close = np.array(seg)
        idx = pd.bdate_range("2023-01-02", periods=len(close))
        return pd.DataFrame({"Open": close, "High": close * 1.002, "Low": close * 0.998,
                             "Close": close, "Volume": np.full(len(close), 1e6)}, index=idx)

    def test_counts_two_bases(self):
        from scanner import indicators
        bc = indicators.base_count(self._staircase())
        assert bc is not None
        assert bc["count"] == 2
        assert bc["stage"].startswith("early")

    def test_base_geometry_for_chart(self):
        from scanner import indicators
        df = self._staircase()
        bc = indicators.base_count(df)
        bases = bc["bases"]
        assert [b["n"] for b in bases] == [1, 2]
        # both staircase bases broke out (advances 2 and 3 followed them)
        assert all(b["end"] is not None for b in bases)
        # spans are chronological and each start precedes its end
        assert all(b["start"] < b["end"] for b in bases)
        assert bases[0]["end"] <= bases[1]["start"]
        # dates are real bars of this frame
        days = set(d.strftime("%Y-%m-%d") for d in df.index)
        for b in bases:
            assert b["start"] in days and b["end"] in days

    def test_forming_base_has_open_end(self):
        from scanner import indicators
        df = self._staircase()
        # freeze the last 20 sessions at the peak: a 3rd base still forming
        peak = float(df["Close"].iloc[-1])
        ext = df.iloc[-20:].copy()
        ext.index = pd.bdate_range(df.index[-1] + pd.Timedelta(days=1), periods=20)
        for col in ("Open", "High", "Low", "Close"):
            ext[col] = peak * 0.995
        bc = indicators.base_count(pd.concat([df, ext]))
        assert bc["count"] == 3
        assert bc["bases"][-1]["end"] is None

    def test_short_history_none(self):
        from scanner import indicators
        df = self._staircase().iloc[-100:]
        assert indicators.base_count(df) is None


class TestSupportResistance:
    def _range_bound(self, n=250):
        # oscillates between ~90 (support) and ~110 (resistance), many touches
        x = np.arange(n)
        closes = 100 + 10 * np.sin(x / 8.0)
        return make_df(closes)

    def test_finds_levels_both_sides(self):
        df = self._range_bound()
        lv = indicators.support_resistance(df)
        assert lv["supports"] and lv["resistances"]
        price = float(df["Close"].iloc[-1])
        assert all(l["price"] < price for l in lv["supports"])
        assert all(l["price"] > price for l in lv["resistances"])

    def test_repeated_touches_are_strong(self):
        lv = indicators.support_resistance(self._range_bound())
        strengths = [l["strength"] for l in lv["supports"] + lv["resistances"]]
        assert "strong" in strengths

    def test_nearest_first(self):
        df = self._range_bound()
        lv = indicators.support_resistance(df)
        price = float(df["Close"].iloc[-1])
        gaps = [price - l["price"] for l in lv["supports"]]
        assert gaps == sorted(gaps)

    def test_short_history(self):
        lv = indicators.support_resistance(steady_uptrend(n=40))
        assert lv == {"supports": [], "resistances": []}


class TestProfitabilityUpgrades:
    def test_adr_pct(self):
        df = make_df(np.full(60, 100.0))  # High=101, Low=99 daily -> ~2.02%
        assert indicators.adr_pct(df) == pytest.approx(2.02, abs=0.05)

    def test_quality_score_bounds_and_ordering(self):
        df = steady_uptrend()
        tight = {"vcp": True, "contractions_pct": [15.0, 8.0, 3.0],
                 "volume_dry_up": True, "pivot": float(df["Close"].iloc[-1])}
        loose = {"vcp": True, "contractions_pct": [30.0, 25.0, 20.0],
                 "volume_dry_up": False, "pivot": float(df["Close"].iloc[-1]) * 0.8}
        q1, q2 = indicators.quality_score(df, tight), indicators.quality_score(df, loose)
        assert 0 <= q2 < q1 <= 100

    def test_time_stop(self):
        df = make_df(np.full(300, 100.0))
        sig = indicators.exit_signals(df, entry=99.5, stop=92.0, pivot=None, days_held=6)
        assert sig["time_stop"]["triggered"]
        sig2 = indicators.exit_signals(df, entry=99.5, stop=92.0, pivot=None, days_held=2)
        assert not sig2["time_stop"]["triggered"]
        # progressing position: price well above entry -> no time stop
        sig3 = indicators.exit_signals(df, entry=90.0, stop=85.0, pivot=None, days_held=10)
        assert not sig3["time_stop"]["triggered"]

    def test_group_rs(self):
        ranks = {f"A{i}": 90 + i % 5 for i in range(5)} | {f"B{i}": 20 + i % 5 for i in range(5)}
        inds = {t: ("Semis" if t.startswith("A") else "Retail") for t in ranks}
        g = indicators.industry_group_rs(ranks, inds)
        assert g["Semis"] > g["Retail"]
        # groups under min_members are excluded
        g2 = indicators.industry_group_rs({"X": 50}, {"X": "Solo"})
        assert g2 == {}


class TestEarlyDetectionAndPatterns:
    def test_rule_results_and_needs(self):
        tt = indicators.trend_template(steady_downtrend(), rs_rank=90)
        passed, failed = indicators.rule_results(tt)
        assert passed + len(failed) == 8 and failed
        msgs = indicators.what_needs_to_happen(tt, 100.0)
        assert msgs and all(isinstance(m, str) for m in msgs)

    def test_pocket_pivot(self):
        closes = np.full(60, 100.0)
        vols = np.full(60, 1_000_000.0)
        df = make_df(closes, vols)
        # craft: prior down day vols small; last bar big up close near high
        df.iloc[-1, df.columns.get_loc("Open")] = 100.0
        df.iloc[-1, df.columns.get_loc("Close")] = 103.0
        df.iloc[-1, df.columns.get_loc("High")] = 103.5
        df.iloc[-1, df.columns.get_loc("Low")] = 99.8
        df.iloc[-1, df.columns.get_loc("Volume")] = 5_000_000
        df.iloc[-3, df.columns.get_loc("Open")] = 101.0
        df.iloc[-3, df.columns.get_loc("Close")] = 99.0  # a REAL down day (close < prior close)
        assert indicators.pocket_pivot(df)

    def test_tightening(self):
        # wide range then tight near highs
        closes = np.concatenate([100 + 8 * np.sin(np.arange(20)), np.full(10, 107.0)])
        closes = np.concatenate([np.full(20, 100.0), closes])
        df = make_df(closes)
        t = indicators.tightening_now(df)
        assert "tightening" in t

    def test_patterns_engulfing_and_volume(self):
        from scanner import patterns
        closes = np.full(60, 100.0)
        df = make_df(closes)
        # yesterday down bar, today up bar engulfing it
        df.iloc[-2, df.columns.get_loc("Open")] = 101.0
        df.iloc[-2, df.columns.get_loc("Close")] = 100.0
        df.iloc[-1, df.columns.get_loc("Open")] = 99.5
        df.iloc[-1, df.columns.get_loc("Close")] = 101.5
        df.iloc[-1, df.columns.get_loc("High")] = 101.6
        df.iloc[-1, df.columns.get_loc("Low")] = 99.4
        names = [p["name"] for p in patterns.last_bar_patterns(df)]
        assert "bullish engulfing" in names
        out = patterns.analyze(df)
        assert out["narrative"] and "volume" in out
        assert out["volume"]["verdict"]

    def test_klse_universe_loads(self):
        from scanner import universe
        t = universe.fetch_klse()
        assert len(t) > 50 and all(x.endswith(".KL") for x in t)


class TestScanFixes:
    def test_single_ticker_multiindex_normalized(self):
        # simulate yf.download(group_by="ticker") shape for a 1-ticker batch
        import pandas as pd
        from scanner import scan
        base = steady_uptrend()
        multi = base.copy()
        multi.columns = pd.MultiIndex.from_product([["^KLSE"], base.columns])
        # replicate the normalization branch
        df = multi
        if isinstance(df.columns, pd.MultiIndex):
            df = df["^KLSE"] if "^KLSE" in df.columns.get_level_values(0) else df.droplevel(0, axis=1)
        assert "Close" in df.columns
        assert scan.market_regime({"^KLSE": df}, ["^KLSE"])["light"] in {"green", "yellow", "red"}

    def test_watchlist_reachable(self):
        from scanner import scan
        df = steady_uptrend()
        # completed VCP, but price 10% below pivot -> watchlist, not position
        vcp = {"vcp": True, "pivot": float(df["Close"].iloc[-1]) * 1.10, "contractions_pct": [12.0, 6.0]}
        ext = {"extended": False}
        assert scan.bucket_candidate(df, vcp, ext) == "watchlist"
        # base building without dry-up -> watchlist
        vcp2 = {"vcp": False, "pivot": None, "contractions_pct": [14.0, 8.0]}
        assert scan.bucket_candidate(df, vcp2, ext) == "watchlist"


class TestSRZones:
    def test_zones_have_bounds_and_wick_touches_count(self):
        # oscillating series: repeated visits to top/bottom areas
        x = np.arange(250)
        closes = 100 + 10 * np.sin(x / 8.0)
        df = make_df(closes)
        lv = indicators.support_resistance(df)
        allz = lv["supports"] + lv["resistances"]
        assert allz, "should find zones"
        for z in allz:
            assert z["low"] < z["high"], "zone must be a range, not a line"
            assert z["low"] <= z["price"] <= z["high"]
            assert z["touches"] >= 1
        # repeated oscillation -> at least one strong zone
        assert any(z["strength"] == "strong" for z in allz)

    def test_visits_not_bars(self):
        # price sits inside a zone for 10 straight bars -> that's ONE touch, not 10
        closes = np.concatenate([np.linspace(80, 100, 120), np.full(10, 100.0),
                                 np.linspace(100, 90, 60), np.full(60, 92.0)])
        df = make_df(closes)
        lv = indicators.support_resistance(df)
        for z in lv["supports"] + lv["resistances"]:
            assert z["touches"] <= 12  # sane visit counts, not bar counts


class TestChartPatterns:
    def _series(self, closes):
        return make_df(np.array(closes, dtype=float))

    def test_double_bottom(self):
        from scanner import patterns
        closes = list(np.linspace(120, 100, 40)) + list(np.linspace(100, 110, 15)) + \
                 list(np.linspace(110, 100.5, 15)) + list(np.linspace(100.5, 113, 30))
        df = self._series([150]*60 + closes)  # pad history
        names = [p["name"] for p in patterns.chart_patterns(df)]
        assert "double bottom" in names

    def test_cup_and_handle(self):
        from scanner import patterns
        x = np.linspace(0, np.pi, 90)
        cup = 100 - 20 * np.sin(x)          # 100 -> 80 -> 100 rounded
        handle = np.linspace(99, 96, 10)
        df = self._series(list(np.full(30, 98.0)) + list(cup) + list(handle))
        # dampen handle volume
        df.iloc[-10:, df.columns.get_loc("Volume")] = 400_000
        names = [p["name"] for p in patterns.chart_patterns(df)]
        assert "cup and handle" in names

    def test_bull_flag(self):
        from scanner import patterns
        closes = list(np.full(80, 50.0)) + list(np.linspace(50, 65, 12)) + list(np.linspace(65, 62.5, 6))
        df = self._series(closes)
        names = [p["name"] for p in patterns.chart_patterns(df)]
        assert "bull flag" in names

    def test_biases_present(self):
        from scanner import patterns
        for fn in (patterns.chart_patterns,):
            out = fn(steady_uptrend())
            for p in out:
                assert p["bias"] in {"bullish", "bearish"} and p["note"]

    def test_snr_max_two_per_side(self):
        x = np.arange(250)
        df = make_df(100 + 10 * np.sin(x / 8.0))
        lv = indicators.support_resistance(df)
        assert len(lv["supports"]) <= 2 and len(lv["resistances"]) <= 2


class TestEarlyEntry:
    def test_early_entry_below_pivot(self):
        # flat base at ~100, pivot 106 well above -> cheat trigger near recent high
        closes = np.concatenate([np.linspace(70, 100, 200), 100 + np.random.RandomState(1).uniform(-1, 1, 40)])
        df = make_df(closes)
        ee = indicators.early_entry(df, pivot=106.0)
        assert ee and ee["trigger"] < 106.0 and ee["stop"] < ee["trigger"]
        assert ee["risk_pct"] <= 8

    def test_no_early_entry_at_pivot(self):
        df = steady_uptrend()
        price = float(df["Close"].iloc[-1])
        assert indicators.early_entry(df, pivot=price * 1.01) is None

    def test_no_early_entry_too_far_below_pivot(self):
        # pivot more than 20% overhead -> too deep in the base for a cheat entry
        closes = np.concatenate([np.linspace(70, 100, 200), 100 + np.random.RandomState(1).uniform(-1, 1, 40)])
        df = make_df(closes)
        assert indicators.early_entry(df, pivot=130.0) is None

    def test_markers_exported(self):
        from scanner import patterns
        out = patterns.analyze(steady_uptrend())
        assert "chart_markers" in out


class TestBollingerBands:
    def test_bands_widen_with_volatility(self):
        flat = make_df(np.full(60, 100.0))
        bb_flat = indicators.bollinger_bands(flat)
        assert bb_flat["upper"].iloc[-1] == pytest.approx(bb_flat["lower"].iloc[-1], abs=0.01)

        volatile = make_df(100 + 10 * np.sin(np.arange(60) / 2.0))
        bb_vol = indicators.bollinger_bands(volatile)
        assert bb_vol["upper"].iloc[-1] - bb_vol["lower"].iloc[-1] > 5

    def test_mid_band_is_the_sma(self):
        df = steady_uptrend(n=60)
        bb = indicators.bollinger_bands(df, window=20)
        expected = float(df["Close"].rolling(20).mean().iloc[-1])
        assert bb["mid"].iloc[-1] == pytest.approx(expected)

    def test_nan_before_window_fills(self):
        df = steady_uptrend(n=30)
        bb = indicators.bollinger_bands(df, window=20)
        assert pd.isna(bb["upper"].iloc[5])
        assert pd.notna(bb["upper"].iloc[-1])


class TestVolumeBaselines:
    def test_dry_up_baseline_excludes_the_quiet_week(self):
        # last-5 volume at 58% of the TRUE prior average = a genuine dry-up.
        # The old baseline included the quiet week in its own average (reading
        # 58% as 60.5%) and missed exactly these boundary cases.
        closes = np.linspace(50, 100, 240)
        vols = np.concatenate([np.full(235, 1e6), np.full(5, 0.58e6)])
        vcp = indicators.detect_vcp(make_df(closes, vols))
        assert vcp["volume_dry_up"] is True

    def test_no_dry_up_when_volume_normal(self):
        closes = np.linspace(50, 100, 240)
        vcp = indicators.detect_vcp(make_df(closes, np.full(240, 1e6)))
        assert vcp["volume_dry_up"] is False


class TestVcpContractionPairing:
    def test_one_pullback_one_contraction(self):
        # oscillating base: every contraction must pair a swing high with a low
        # that comes AFTER it and BEFORE the next swing high — the old pairing
        # let consecutive highs share one low (double-counting a pullback)
        x = np.arange(120)
        closes = 100 + 8 * np.sin(x / 6.0) + x * 0.05
        vcp = indicators.detect_vcp(make_df(closes))
        pairs = [(s[0]["t"], s[1]["t"]) for s in vcp["swings"]]
        assert pairs, "oscillating series should produce contractions"
        highs = [h for h, _ in pairs]
        lows = [l for _, l in pairs]
        assert all(h < l for h, l in pairs)          # low strictly after its high
        assert highs == sorted(highs)                 # chronological
        assert len(set(lows)) == len(lows)            # no low counted twice


class TestPocketPivot:
    @staticmethod
    def _base(n=60, level=100.0):
        # flat base with real close-to-close down days (alternating ±0.5)
        closes = level + 0.5 * np.array([1 if i % 2 == 0 else -1 for i in range(n)])
        return make_df(closes, np.full(n, 1e6))

    @staticmethod
    def _signal_bar(df, close_mult=1.03, low=None, vol=9e6):
        last = float(df["Close"].iloc[-1])
        bar = pd.DataFrame({"Open": [last], "High": [last * close_mult * 1.005],
                            "Low": [low if low is not None else last * 0.998],
                            "Close": [last * close_mult], "Volume": [vol]},
                           index=[df.index[-1] + pd.Timedelta(days=1)])
        return pd.concat([df, bar])

    def test_fires_in_a_proper_base(self):
        # up close-to-close, top of range, at the 10-day line, volume above
        # every down day of the past 10 sessions -> valid pocket pivot
        df = self._signal_bar(self._base())
        assert indicators.pocket_pivot(df) is True

    def test_invalid_when_extended_above_10ma(self):
        # same volume signature but AFTER a 5-day vertical run: the day's low
        # is far above the 10-day MA — Kacher calls this an extended (invalid) PP
        base = self._base()
        run = base
        for _ in range(5):
            run = self._signal_bar(run, close_mult=1.04, vol=1.1e6)
        df = self._signal_bar(run, close_mult=1.04)
        last_low = float(df["Low"].iloc[-1])
        ma10 = float(df["Close"].rolling(10).mean().iloc[-1])
        assert last_low > ma10 * 1.02  # setup precondition: genuinely extended
        assert indicators.pocket_pivot(df) is False

    def test_invalid_below_50ma(self):
        # explosive volume bar in a downtrend below the 50-day MA:
        # short-covering, not a pocket pivot
        down = steady_downtrend(n=120)
        df = self._signal_bar(down, close_mult=1.03)
        assert float(df["Close"].iloc[-1]) < float(df["Close"].rolling(50).mean().iloc[-1])
        assert indicators.pocket_pivot(df) is False

    def test_gap_down_green_candle_is_not_an_up_day(self):
        # closes below yesterday's close (down day close-to-close) even though
        # the candle itself is green intraday — must NOT fire
        base = self._base()
        last = float(base["Close"].iloc[-1])
        bar = pd.DataFrame({"Open": [last * 0.94], "High": [last * 0.985],
                            "Low": [last * 0.935], "Close": [last * 0.98], "Volume": [9e6]},
                           index=[base.index[-1] + pd.Timedelta(days=1)])
        df = pd.concat([base, bar])
        assert indicators.pocket_pivot(df) is False


class TestSetupWarnings:
    def test_failed_breakout_warning(self):
        # uptrend, pivot near the top, but the last bar has pulled back below it
        closes = np.concatenate([np.linspace(50, 100, 200),
                                 [101, 102, 103, 97, 96]])  # triggered then failed
        df = make_df(closes)
        checks = {"price_above_ma50": {"pass": True}}
        warn = indicators.setup_warnings(df, pivot=100.0, checks=checks, vol_profile=None)
        codes = [w["code"] for w in warn]
        assert "failed_breakout" in codes
        fb = next(w for w in warn if w["code"] == "failed_breakout")
        assert fb["title"] and fb["what"] and fb["why"] and fb["do"]  # fully explained, not a bare label

    def test_below_50ma_warning(self):
        df = steady_downtrend(n=260)
        checks = {"price_above_ma50": {"pass": False}}
        warn = indicators.setup_warnings(df, pivot=None, checks=checks, vol_profile=None)
        assert any(w["code"] == "below_50ma" for w in warn)

    def test_no_warnings_on_healthy_uptrend(self):
        df = steady_uptrend(n=260)
        checks = {"price_above_ma50": {"pass": True}}
        vol_profile = {"accumulation_days": 10, "distribution_days": 2, "window": 25}
        warn = indicators.setup_warnings(df, pivot=None, checks=checks, vol_profile=vol_profile)
        assert warn == []

    def test_distribution_warning(self):
        df = steady_uptrend(n=260)
        checks = {"price_above_ma50": {"pass": True}}
        vol_profile = {"accumulation_days": 2, "distribution_days": 8, "window": 25}
        warn = indicators.setup_warnings(df, pivot=None, checks=checks, vol_profile=vol_profile)
        assert any(w["code"] == "distribution" for w in warn)


class TestHighTightFlag:
    @staticmethod
    def _htf_df(flag_low_pct=0.88):
        # 60 flat bars at 50, a ~35-bar pole doubling to 100, then an 8-bar
        # quiet flag holding near the high on lower volume
        base = np.full(60, 50.0)
        pole = np.linspace(50, 100, 35)
        flag = np.linspace(100, 100 * flag_low_pct, 8)
        closes = np.concatenate([base, pole, flag])
        vols = np.concatenate([np.full(60, 1e6), np.full(35, 3e6), np.full(8, 8e5)])
        return make_df(closes, vols)

    def test_detects_high_tight_flag(self):
        from scanner import patterns
        out = patterns.chart_patterns(self._htf_df())
        names = [p["name"] for p in out]
        assert "high tight flag" in names
        assert "bull flag" not in names  # HTF supersedes the plain flag
        htf = next(p for p in out if p["name"] == "high tight flag")
        assert htf["pivot"] == pytest.approx(101.0, rel=0.02)  # peak high (close*1.01)
        assert len(htf["lines"]) == 2

    def test_no_htf_when_pullback_too_deep(self):
        from scanner import patterns
        out = patterns.chart_patterns(self._htf_df(flag_low_pct=0.70))  # 30% drop
        assert "high tight flag" not in [p["name"] for p in out]


class TestSignalGrade:
    @staticmethod
    def _bars(rows):
        idx = pd.bdate_range(start="2026-06-01", periods=len(rows))
        return pd.DataFrame([{"Open": o, "High": h, "Low": l, "Close": c, "Volume": 1e6}
                             for o, h, l, c in rows], index=idx)

    def test_win_hits_target_before_stop(self):
        from scanner import performance
        df = self._bars([(100, 101, 99, 100), (102, 106, 101, 105), (106, 112, 105, 111)])
        g = performance.grade(df, trigger=105.0, stop=100.0, target=112.0)
        assert g["triggered"] and g["outcome"] == "win"
        assert g["r_multiple"] == pytest.approx(1.4)

    def test_loss_stop_first_and_same_day_conservative(self):
        from scanner import performance
        # trigger day, then a bar that spans BOTH stop and target -> loss
        df = self._bars([(100, 106, 99, 105), (105, 120, 94, 96)])
        g = performance.grade(df, trigger=105.0, stop=95.0, target=115.0)
        assert g["outcome"] == "loss" and g["r_multiple"] == -1.0

    def test_never_triggered(self):
        from scanner import performance
        df = self._bars([(100, 101, 99, 100)] * 20)
        g = performance.grade(df, trigger=110.0, stop=100.0, target=120.0)
        assert not g["triggered"] and g["outcome"] == "never_triggered"


class TestDistributionDays:
    def test_counts_down_days_on_rising_volume(self):
        from scanner import scan
        closes = [100.0]
        vols = [1e6]
        for i in range(30):
            down = i % 2 == 0
            closes.append(closes[-1] * (0.99 if down else 1.005))
            vols.append(vols[-1] * (1.3 if down else 0.7))
        df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                           "Close": closes, "Volume": vols},
                          index=pd.bdate_range(end="2026-07-08", periods=len(closes)))
        n = scan.distribution_days(df)
        assert n is not None and n >= 6

    def test_none_without_volume(self):
        from scanner import scan
        df = make_df(np.full(40, 100.0), np.zeros(40))
        assert scan.distribution_days(df) is None


class TestFollowThroughDay:
    def test_detects_ftd_after_correction(self):
        from scanner import scan
        # decline 100 -> 85 (15%), 3 quiet rally days, then day 4 = +2% on volume
        closes = np.concatenate([np.linspace(100, 85, 20),
                                 [85.2, 85.4, 85.6, 87.4], np.full(3, 87.5)])
        vols = np.concatenate([np.full(20, 1e6), [8e5, 8e5, 8e5, 1.5e6], np.full(3, 9e5)])
        ft = scan.follow_through_day(make_df(closes, vols))
        assert ft is not None and ft["day_of_rally"] >= 4 and ft["pct"] >= 1.5

    def test_no_ftd_without_real_correction(self):
        from scanner import scan
        # shallow 3% dip then strength — an FTD needs a real decline first
        closes = np.concatenate([np.linspace(100, 97, 20), [97.2, 97.4, 97.6, 99.5],
                                 np.full(3, 99.6)])
        vols = np.concatenate([np.full(20, 1e6), [8e5, 8e5, 8e5, 1.5e6], np.full(3, 9e5)])
        assert scan.follow_through_day(make_df(closes, vols)) is None


class TestZangerExitSignals:
    def test_failure_and_strength_lines(self):
        df = steady_uptrend()
        price = float(df["Close"].iloc[-1])
        # bought at the pivot, now 1.5% below it -> failure line triggered
        sig = indicators.exit_signals(df, entry=price * 1.015, stop=price * 0.94,
                                      pivot=price * 1.015, days_held=3)
        assert sig["zanger_failure"]["triggered"]
        # price 16% above the pivot within 3 weeks -> sell-strength triggered
        sig2 = indicators.exit_signals(df, entry=price / 1.16, stop=price * 0.8,
                                       pivot=price / 1.16, days_held=10)
        assert sig2["sell_strength"]["triggered"]


class TestCandlestickPatterns:
    @staticmethod
    def _ohlc(rows):
        idx = pd.bdate_range(end="2026-07-08", periods=len(rows))
        return pd.DataFrame([{"Open": o, "High": h, "Low": l, "Close": c, "Volume": 1e6}
                             for o, h, l, c in rows], index=idx)

    def test_talib_available(self):
        from scanner import patterns
        assert patterns._HAS_TALIB, "TA-Lib should be installed (requirements.txt)"

    def test_bullish_engulfing_detected(self):
        from scanner import patterns
        rows = [(100, 101, 99, 100.2)] * 20
        rows += [(100, 100.5, 97.8, 98)]          # down bar
        rows += [(97.5, 102.5, 97.2, 102)]         # up bar engulfing it
        pats = patterns.last_bar_patterns(self._ohlc(rows))
        assert "bullish engulfing" in [p["name"] for p in pats]

    def test_plain_up_bar_is_not_a_pattern(self):
        from scanner import patterns
        # ordinary up bars in a steady climb: no reversal pattern should fire
        rows = [(100 + i * 0.5, 101 + i * 0.5, 99.8 + i * 0.5, 100.8 + i * 0.5)
                for i in range(25)]
        pats = patterns.last_bar_patterns(self._ohlc(rows))
        names = [p["name"] for p in pats]
        for wrong in ("hammer", "shooting star", "bullish engulfing", "bearish engulfing", "doji"):
            assert wrong not in names


class TestNewsParse:
    def test_parses_nested_content_shape(self):
        from scanner import news
        items = [{"content": {"title": "Chips rally", "provider": {"displayName": "Reuters"},
                              "canonicalUrl": {"url": "https://x/1"}, "pubDate": "2026-07-10T12:00:00Z"}}]
        out = news._parse(items)
        assert out == [{"title": "Chips rally", "publisher": "Reuters",
                        "url": "https://x/1", "date": "2026-07-10"}]

    def test_parses_legacy_flat_shape(self):
        from scanner import news
        items = [{"title": "Banks slip", "publisher": "WSJ", "link": "https://x/2",
                  "providerPublishTime": 1783850000}]
        out = news._parse(items)
        assert out[0]["title"] == "Banks slip" and out[0]["url"] == "https://x/2"
        assert out[0]["date"] and len(out[0]["date"]) == 10

    def test_fresh_filters_old(self):
        from scanner import news
        old = {"title": "x", "url": "u", "date": "2020-01-01"}
        assert news._fresh([old]) == []
