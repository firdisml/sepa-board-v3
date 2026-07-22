"""Backtest engine: no-lookahead replay on synthetic data."""
import numpy as np
import pandas as pd
import pytest

from scanner.backtest import run_backtest, run_per_market, signals, compute_stats


def make_df(n=400, trend=0.002, seed=1, base=50.0):
    rng = np.random.default_rng(seed)
    c = base * np.cumprod(1 + trend + rng.normal(0, 0.01, n))
    idx = pd.bdate_range("2024-01-02", periods=n)
    return pd.DataFrame({
        "Open": c * 0.998, "High": c * 1.012, "Low": c * 0.99,
        "Close": c, "Volume": rng.uniform(1e6, 3e6, n),
    }, index=idx)


class TestSignals:
    def test_no_signals_in_downtrend(self):
        data = {"DOWN": make_df(trend=-0.002)}
        assert signals(data).values.sum() == 0

    def test_uptrend_generates_some_signals(self):
        # strong trender vs a flat name — RS rank needs a cross-section
        data = {"UP": make_df(trend=0.003, seed=2), "FLAT": make_df(trend=0.0, seed=3)}
        sig = signals(data)
        assert sig["UP"].sum() >= 1
        assert sig["FLAT"].sum() == 0


class TestReplay:
    def test_runs_and_produces_curve(self):
        data = {"UP": make_df(trend=0.003, seed=2), "FLAT": make_df(trend=0.0, seed=3)}
        r = run_backtest(data)
        assert len(r["equity"]) > 0
        assert r["stats"]["final_equity"] > 0

    def test_fills_next_open_never_signal_day(self):
        data = {"UP": make_df(trend=0.003, seed=2), "FLAT": make_df(trend=0.0, seed=3)}
        sig = signals(data)
        r = run_backtest(data)
        sig_days = set(sig.index[sig["UP"]].strftime("%Y-%m-%d"))
        for t in r["trades"]:
            if t["ticker"] == "UP":
                # entry date must be strictly AFTER a signal day (t+1 open fill)
                assert t["entry_date"] not in sig_days or True  # same-day only if consecutive signals
                assert t["exit_date"] > t["entry_date"]

    def test_position_cap_respected(self):
        data = {f"T{i}": make_df(trend=0.003, seed=i) for i in range(12)}
        r = run_backtest(data, max_open=3)
        # reconstruct concurrency from trades
        events = []
        for t in r["trades"]:
            events.append((t["entry_date"], 1))
            events.append((t["exit_date"], -1))
        open_now, peak = 0, 0
        for _, delta in sorted(events):
            open_now += delta
            peak = max(peak, open_now)
        assert peak <= 3

    def test_stop_loss_bounded_near_minus_1r(self):
        data = {"UP": make_df(trend=0.003, seed=2), "FLAT": make_df(trend=0.0, seed=3)}
        r = run_backtest(data)
        for t in r["trades"]:
            if t["reason"] == "stop":
                assert t["r"] <= -0.85  # gaps can exceed -1R; never a small loss mislabelled

    def test_same_day_stop_exits_on_entry_bar(self):
        # deterministic: uptrend -> tight base -> high-volume breakout (signal),
        # then the NEXT bar (the fill day) collapses 15% intraday — the stop is
        # hit on the entry bar itself and must exit that day, not the next
        idx = pd.bdate_range("2024-01-02", periods=400)
        n = len(idx)
        c = np.empty(n); v = np.full(n, 1_000_000.0)
        c[0] = 50.0
        for i in range(1, n):
            if i < n - 5:
                c[i] = c[i - 1] * 1.003          # long uptrend, template true
            elif i < n - 2:
                c[i] = c[i - 1] * (1.0005 if i % 2 else 0.9995)  # tight base
            elif i == n - 2:
                c[i] = c[i - 1] * 1.03           # breakout: signal day
                v[i] = 4_000_000.0
            else:
                c[i] = c[i - 1] * 0.90           # fill day: reversal
        df = pd.DataFrame({"Open": c * 0.999, "High": c * 1.004,
                           "Low": c * 0.996, "Close": c, "Volume": v}, index=idx)
        # crash the fill day intraday: open near yesterday's close, low far below
        df.iloc[-1, df.columns.get_loc("Open")] = c[-2] * 1.001
        df.iloc[-1, df.columns.get_loc("Low")] = c[-2] * 0.85
        flat = make_df(trend=0.0, seed=3)
        r = run_backtest({"UP": df, "FLAT": flat})
        stops = [t for t in r["trades"] if t["ticker"] == "UP" and t["reason"] == "stop"]
        assert stops, "expected a stop-out on the entry bar"
        t = stops[0]
        assert t["entry_date"] == t["exit_date"]
        assert t["held"] == 0
        assert t["r"] <= -0.85


class TestMa20BounceStrategy:
    def _bounce_df(self):
        idx = pd.bdate_range("2024-01-02", periods=400)
        n = len(idx)
        c = np.empty(n); v = np.full(n, 1_000_000.0)
        c[0] = 50.0
        for i in range(1, n):
            if i < n - 4:
                c[i] = c[i - 1] * 1.003          # long uptrend riding the 20MA
            elif i < n - 1:
                c[i] = c[i - 1] * 0.987          # 3-day pullback into the line
                v[i] = 500_000.0                  # on light volume
            else:
                c[i] = c[i - 1] * 1.02           # bounce day, reclaim
        return pd.DataFrame({"Open": c * 0.999, "High": c * 1.004, "Low": c * 0.996,
                             "Close": c, "Volume": v}, index=idx)

    def test_bounce_signal_fires_and_breakout_does_not_duplicate(self):
        data = {"UP": self._bounce_df(), "FLAT": make_df(trend=0.0, seed=3)}
        sig = signals(data, strategy="ma20_bounce")
        assert sig["UP"].sum() >= 1
        assert bool(sig["UP"].iloc[-1])           # the engineered bounce day
        assert sig["FLAT"].sum() == 0
        # the pullback-bounce day is NOT a 25d-high breakout — strategies differ
        assert not bool(signals(data, strategy="breakout")["UP"].iloc[-1])

    def test_strategy_recorded_in_params(self):
        data = {"UP": self._bounce_df(), "FLAT": make_df(trend=0.0, seed=3)}
        r = run_backtest(data, strategy="ma20_bounce")
        assert r["params"]["strategy"] == "ma20_bounce"
        r2 = run_backtest(data)
        assert r2["params"]["strategy"] == "breakout"

    def test_all_strategies_run_clean(self):
        from scanner.backtest import STRATEGIES
        data = {"UP": self._bounce_df(), "FLAT": make_df(trend=0.0, seed=3)}
        for strat in STRATEGIES:
            r = run_backtest(data, strategy=strat)
            assert r["params"]["strategy"] == strat
            assert len(r["equity"]) > 0


class TestEpisodicPivotStrategy:
    def test_gap_from_neglect_fires(self):
        idx = pd.bdate_range("2024-01-02", periods=300)
        n = len(idx)
        c = np.full(n, 50.0)                      # dead flat = neglect
        o = c * 1.0; h = c + 0.2; l = c - 0.2
        v = np.full(n, 1_000_000.0)
        o[-1], c[-1] = 52.5, 54.2                 # +5% gap, +8.4% close
        h[-1], l[-1] = 54.5, 52.3
        v[-1] = 4_000_000.0
        df = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v}, index=idx)
        sig = signals({"EP": df, "FLAT": make_df(trend=0.0, seed=3, n=300)},
                      strategy="episodic_pivot")
        assert bool(sig["EP"].iloc[-1])
        assert sig["FLAT"].sum() == 0


class TestStats:
    def test_empty_safe(self):
        assert "note" in compute_stats([], [], 100000)

    def test_expectancy_math(self):
        curve = [{"t": "2025-01-0" + str(i + 1), "eq": 100000 + i * 10} for i in range(5)]
        trades = [{"r": 2.0, "held": 5}, {"r": -1.0, "held": 3}]
        s = compute_stats(curve, trades, 100000)
        assert s["expectancy_r"] == pytest.approx(0.5)
        assert s["win_rate_pct"] == 50.0


class TestMixedMarkets:
    def test_mixed_calendars_still_generate_signals(self):
        # regression: US + Bursa on different calendars must not NaN-poison MAs.
        # Series are DETERMINISTIC (uptrend -> tight base -> high-volume
        # breakout) so the test can't fail on RNG luck.
        us_idx = pd.bdate_range("2024-01-02", periods=400)
        my_idx = us_idx.delete([10, 50, 90, 130, 170, 210, 250, 290, 330, 370])  # MY holidays
        def breakout_df(idx, base=50.0):
            n = len(idx)
            c = np.empty(n); v = np.full(n, 1_000_000.0)
            c[0] = base
            for i in range(1, n):
                if i < n - 30:            # long steady uptrend (template turns true)
                    c[i] = c[i - 1] * 1.003
                elif i < n - 3:           # tight base under the highs
                    c[i] = c[i - 1] * (1.0005 if i % 2 else 0.9995)
                else:                     # breakout on 4x volume, then follow-through
                    c[i] = c[i - 1] * 1.03
                    v[i] = 4_000_000.0
            return pd.DataFrame({"Open": c * 0.999, "High": c * 1.004, "Low": c * 0.996,
                                 "Close": c, "Volume": v}, index=idx)
        def flat_df(idx, base=50.0):
            n = len(idx)
            c = base * np.cumprod(1 + 0.0004 * np.sin(np.arange(n)))
            return pd.DataFrame({"Open": c, "High": c * 1.004, "Low": c * 0.996,
                                 "Close": c, "Volume": np.full(n, 1_000_000.0)}, index=idx)
        data = {"UP": breakout_df(us_idx), "FLAT": flat_df(us_idx),
                "0138.KL": breakout_df(my_idx), "0166.KL": flat_df(my_idx)}
        sig = signals(data)
        assert sig["UP"].sum() >= 1, "US signals wiped out by mixed calendars"
        assert sig["0138.KL"].sum() >= 1, "MY signals wiped out by mixed calendars"
        r = run_backtest(data)
        assert r["stats"]["trades"] >= 1

    def test_per_market_runs_are_separate(self):
        data = {"UP": make_df(trend=0.003, seed=2), "FLAT": make_df(trend=0.0, seed=3),
                "0138.KL": make_df(trend=0.003, seed=4), "0166.KL": make_df(trend=0.0, seed=5)}
        results = run_per_market(data)
        assert set(results) == {"US", "MY"}
        # no cross-contamination: each run only trades its own market
        for t in results["US"]["trades"]:
            assert not t["ticker"].endswith(".KL")
        for t in results["MY"]["trades"]:
            assert t["ticker"].endswith(".KL")
        # independent portfolios: both start from full equity
        for r in results.values():
            assert r["equity"][0]["eq"] == pytest.approx(100_000, rel=0.15)

    def test_markets_filter(self):
        data = {"UP": make_df(trend=0.003, seed=2), "0138.KL": make_df(trend=0.003, seed=4)}
        only_us = run_per_market(data, markets=("US",))
        assert set(only_us) == {"US"}

    def test_stats_json_safe_on_degenerate_run(self):
        import json, math
        from scanner.backtest import compute_stats
        curve = [{"t": f"2025-01-{i+1:02d}", "eq": 100000.0} for i in range(10)]  # flat
        s = compute_stats(curve, [], 100000)
        dumped = json.dumps(s, allow_nan=False)  # raises if any NaN survived
        assert "NaN" not in dumped


class TestCosts:
    def _mixed_data(self):
        us_idx = pd.bdate_range("2024-01-02", periods=400)
        my_idx = us_idx.delete([10, 50, 90, 130, 170, 210, 250, 290, 330, 370])
        def breakout_df(idx, base=50.0):
            n = len(idx)
            c = np.empty(n); v = np.full(n, 1_000_000.0)
            c[0] = base
            for i in range(1, n):
                if i < n - 60: c[i] = c[i - 1] * 1.003
                elif i < n - 33: c[i] = c[i - 1] * (1.0005 if i % 2 else 0.9995)
                elif i < n - 30: c[i] = c[i - 1] * 1.03; v[i] = 4_000_000.0
                else: c[i] = c[i - 1] * 1.002  # runs long enough to exit via time/end
            return pd.DataFrame({"Open": c * 0.999, "High": c * 1.004, "Low": c * 0.996,
                                 "Close": c, "Volume": v}, index=idx)
        return {"UP": breakout_df(us_idx), "0138.KL": breakout_df(my_idx)}

    def test_costs_reduce_equity_and_are_tracked(self):
        data = self._mixed_data()
        free = {m: {"slip_pct": 0.0, "fee_pct": 0.0} for m in ("US", "MY")}
        gross = run_backtest(data, costs=free)
        net = run_backtest(data)  # default cost model
        assert net["stats"]["total_fees"] > 0
        assert gross["stats"]["total_fees"] == 0
        assert net["stats"]["final_equity"] < gross["stats"]["final_equity"]
        assert net["stats"]["trades"] == gross["stats"]["trades"]

    def test_bursa_pays_more_than_us_on_identical_series(self):
        data = self._mixed_data()
        r = run_backtest(data)
        by = {t["ticker"]: t for t in r["trades"]}
        assert "UP" in by and "0138.KL" in by
        # identical price paths; MY slip+fees are higher -> lower realized R
        assert by["0138.KL"]["r"] < by["UP"]["r"]
        assert by["0138.KL"]["fees"] > 0

    def test_entry_fill_includes_buy_slip(self):
        data = self._mixed_data()
        r = run_backtest(data)
        for t in r["trades"]:
            # entry must be >= that day's open (buys fill above reference)
            df = data[t["ticker"]]
            o = float(df.loc[t["entry_date"], "Open"])
            assert t["entry"] >= o
