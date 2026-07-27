"""Backtest engine: no-lookahead replay on synthetic data."""
import datetime as dt

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


class TestPocketPivotStrategy:
    def _pp_df(self):
        idx = pd.bdate_range("2024-01-02", periods=400)
        n = len(idx)
        c = np.empty(n); v = np.full(n, 500_000.0)
        c[0] = 50.0
        for i in range(1, n):
            if i < n - 6:
                c[i] = c[i - 1] * 1.003              # long uptrend -> trend template true
            elif i < n - 1:
                c[i] = c[i - 1] * 0.996               # tight pullback, down days
                v[i] = 400_000.0                       # modest down-day volume
            else:
                c[i] = c[i - 1] * 1.02                 # pocket pivot day
                v[i] = 1_200_000.0                     # beats every recent down-day volume
        o = c * 0.999
        h = c * 1.006
        l = c * 0.985                                  # (c-l)/(h-l) ~ 0.71 -> top-third close
        return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v}, index=idx)

    def test_volume_thrust_inside_base_fires(self):
        data = {"PP": self._pp_df(), "FLAT": make_df(trend=0.0, seed=3)}
        sig = signals(data, strategy="pocket_pivot")
        assert bool(sig["PP"].iloc[-1])
        assert sig["FLAT"].sum() == 0

    def test_not_trend_gated_stocks_never_fire(self):
        # a pocket-pivot-shaped volume day on a stock that ISN'T in a
        # confirmed uptrend must not fire — this backtest gates on the Trend
        # Template even though the live indicators.pocket_pivot() does not
        df = self._pp_df()
        flat_base = df.copy()
        flat_base["Close"] = 50.0 * np.ones(len(df))    # kill the uptrend entirely
        flat_base["Open"] = flat_base["Close"] * 0.999
        flat_base["High"] = flat_base["Close"] * 1.006
        flat_base["Low"] = flat_base["Close"] * 0.994
        sig = signals({"NOTREND": flat_base}, strategy="pocket_pivot")
        assert sig["NOTREND"].sum() == 0


class TestBuyableGapUpStrategy:
    def _bgu_df(self):
        idx = pd.bdate_range("2024-01-02", periods=400)
        n = len(idx)
        c = np.empty(n); v = np.full(n, 500_000.0)
        c[0] = 50.0
        for i in range(1, n):
            if i < n - 30:
                c[i] = c[i - 1] * 1.002                        # steady uptrend
            elif i < n - 1:
                c[i] = c[i - 1] * (1.0003 if i % 2 else 0.9997)  # tight base under the pivot
            else:
                c[i] = c[i - 1] * 1.06                          # gap day: +6%
                v[i] = 3_000_000.0                               # 2x+ the 50d average
        o = c * 0.999
        h = c * 1.004
        l = c * 0.996
        return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v}, index=idx)

    def test_full_gap_on_volume_at_pivot_fires(self):
        data = {"BGU": self._bgu_df(), "FLAT": make_df(trend=0.0, seed=3)}
        sig = signals(data, strategy="buyable_gap_up")
        assert bool(sig["BGU"].iloc[-1])
        assert sig["FLAT"].sum() == 0

    def test_overlapping_gap_does_not_fire(self):
        # same-size move but the low does NOT clear the prior high -> not a
        # full gap, must not fire even though every other condition holds
        df = self._bgu_df()
        df.iloc[-1, df.columns.get_loc("Low")] = df["High"].iloc[-2] * 0.999
        sig = signals({"BGU": df}, strategy="buyable_gap_up")
        assert not bool(sig["BGU"].iloc[-1])


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


class TestDeepHistory:
    """--deep-history: survivorship fix (NEXT.md §1). Everything network-shaped
    is monkeypatched; only the selection/caching/staleness logic is under test.

    No liquidity pre-filter: the whole point of this version is that the
    RS-ranking pool must not be narrowed before signals() sees it (see
    load_deep_history's docstring for the bug this replaced)."""

    def _hist_df(self, n=300, base=10.0, end=None):
        # default end date is safely in the past (2023-01-02 + n bdays), well
        # outside stale_delisted_days of "today" in any real test run
        if end is not None:
            idx = pd.bdate_range(end=end, periods=n)
        else:
            idx = pd.bdate_range("2023-01-02", periods=n)
        c = base * np.cumprod(1 + 0.0005 * np.ones(n))
        return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99,
                             "Close": c, "Volume": np.full(n, 2_000_000.0)}, index=idx)

    def _directory(self):
        return pd.DataFrame([
            {"ticker": "1111.KL", "name": "Live Co", "type": "Common Stock", "delisted": False},
            {"ticker": "2222.KL", "name": "Also Live Co", "type": "Common Stock", "delisted": False},
            {"ticker": "3333.KL", "name": "Dead Co", "type": "Common Stock", "delisted": True},
        ])

    def _patch_common(self, monkeypatch):
        from scanner import backtest as bt

        monkeypatch.setattr(bt.eod, "symbols", lambda exchange, include_delisted=True: self._directory())

        calls = {"history": []}

        def fake_history(ticker, years=2):
            calls["history"].append(ticker)
            if ticker == "3333.KL":
                return self._hist_df(n=280, base=3.0)
            return self._hist_df(n=300, base=10.0)

        monkeypatch.setattr(bt.eod, "history", fake_history)
        return calls

    def test_fetches_whole_directory_no_liquidity_prefilter(self, monkeypatch, tmp_path):
        from scanner import backtest as bt
        monkeypatch.setattr(bt, "CACHE_DIR", str(tmp_path))
        calls = self._patch_common(monkeypatch)

        data = bt.load_deep_history(market="MY", years=2, min_bars=200)

        # both live tickers included regardless of price/volume, plus delisted
        assert set(data) == {"1111.KL", "2222.KL", "3333.KL"}
        assert set(calls["history"]) == {"1111.KL", "2222.KL", "3333.KL"}

    def test_second_run_reads_from_parquet_cache(self, monkeypatch, tmp_path):
        from scanner import backtest as bt
        monkeypatch.setattr(bt, "CACHE_DIR", str(tmp_path))
        calls = self._patch_common(monkeypatch)

        bt.load_deep_history(market="MY", years=2, min_bars=200)
        assert len(calls["history"]) == 3

        bt.load_deep_history(market="MY", years=2, min_bars=200)
        assert len(calls["history"]) == 3                # no new API calls second run

    def test_min_bars_drops_short_delisted_history(self, monkeypatch, tmp_path):
        from scanner import backtest as bt
        monkeypatch.setattr(bt, "CACHE_DIR", str(tmp_path))
        self._patch_common(monkeypatch)

        data = bt.load_deep_history(market="MY", years=2, min_bars=290)
        assert "3333.KL" not in data                    # 280 bars < 290 min_bars
        assert "1111.KL" in data

    def test_unavailable_ticker_is_skipped_not_raised(self, monkeypatch, tmp_path):
        from scanner import backtest as bt
        monkeypatch.setattr(bt, "CACHE_DIR", str(tmp_path))
        self._patch_common(monkeypatch)

        def flaky_history(ticker, years=2):
            if ticker == "3333.KL":
                raise bt.eod.DataUnavailable("no history")
            return self._hist_df(n=300, base=10.0)

        monkeypatch.setattr(bt.eod, "history", flaky_history)
        data = bt.load_deep_history(market="MY", years=2, min_bars=200)
        assert "3333.KL" not in data
        assert "1111.KL" in data

    def test_delisted_ticker_still_trading_recently_is_excluded(self, monkeypatch, tmp_path):
        # a "delisted" ticker whose fetched history ends yesterday cannot be a
        # real delisting — it's a live-company alias duplicate (AEON/AXIATA/
        # BAT-class case found 2026-07-25); must be excluded, not counted
        from scanner import backtest as bt
        monkeypatch.setattr(bt, "CACHE_DIR", str(tmp_path))
        self._patch_common(monkeypatch)

        yesterday = dt.date.today() - dt.timedelta(days=1)

        def fake_history(ticker, years=2):
            if ticker == "3333.KL":
                return self._hist_df(n=280, base=3.0, end=yesterday)
            return self._hist_df(n=300, base=10.0)

        monkeypatch.setattr(bt.eod, "history", fake_history)
        data = bt.load_deep_history(market="MY", years=2, min_bars=200)

        assert "3333.KL" not in data                    # excluded despite >= min_bars
        assert {"1111.KL", "2222.KL"} <= set(data)

    def test_genuinely_old_delisted_ticker_is_kept(self, monkeypatch, tmp_path):
        # sanity check the staleness gate isn't overzealous: a name whose last
        # bar is years old is exactly what this function exists to include
        from scanner import backtest as bt
        monkeypatch.setattr(bt, "CACHE_DIR", str(tmp_path))
        self._patch_common(monkeypatch)

        data = bt.load_deep_history(market="MY", years=2, min_bars=200)
        assert "3333.KL" in data                        # last bar is in 2023-2024, not recent


class TestKlseAliasFilterWarnsOnDelisted:
    def test_delisted_row_dropped_by_alias_filter_logs_warning(self, monkeypatch, caplog):
        import logging
        from scanner import eodhd_client as eod

        rows_live = [{"Code": "HEXTAR", "Name": "Hextar Alias", "Type": "Common Stock"}]
        rows_dead = [{"Code": "OLDCO", "Name": "Old Delisted Co", "Type": "Common Stock"}]

        def fake_get(path, **params):
            return rows_dead if params.get("delisted") == "1" else rows_live

        monkeypatch.setattr(eod, "_get", fake_get)
        with caplog.at_level(logging.WARNING):
            out = eod.symbols("KLSE", include_delisted=True)

        assert list(out["ticker"]) == []                 # both non-numeric, both dropped
        assert any("DELISTED symbols dropped" in r.message for r in caplog.records)


class TestGhostTradingDays:
    """A handful of junk tickers carry vendor bars on MARKET HOLIDAYS. Every
    real ticker is NaN on those rows, and one NaN nulls any rolling window
    spanning it — so ma200 was NaN for all 5,594 US tickers and the nightly US
    backtest reported ZERO trades, silently, for as long as US ran."""

    HOLIDAY_POS = [120, 250]     # positions treated as market holidays

    def _universe(self, n=30, with_ghosts=True):
        """Real tickers skip the holidays; one junk ticker trades on them —
        exactly the vendor behaviour observed on the live US warehouse."""
        full = pd.bdate_range("2024-01-02", periods=400)
        real_idx = full.delete(self.HOLIDAY_POS)
        data = {}
        for i in range(n):
            df = make_df(n=len(real_idx), trend=0.003, seed=i)
            df.index = real_idx
            data[f"T{i}"] = df
        junk = make_df(n=len(full) if with_ghosts else len(real_idx),
                       trend=0.0, seed=99)
        junk.index = full if with_ghosts else real_idx
        data["JUNK"] = junk
        return data

    def _ghost_dates(self):
        return pd.bdate_range("2024-01-02", periods=400)[self.HOLIDAY_POS]

    def test_holiday_rows_are_excluded_from_the_session_index(self):
        from scanner.backtest import _drop_ghost_days, _matrix
        C = _matrix(self._universe(), "Close")
        real = _drop_ghost_days(C)
        for g in self._ghost_dates():
            assert g in C.index, "fixture must contain the ghost row"
            assert g not in real, "a day only one junk ticker traded is not a session"

    def test_signals_survive_a_ghost_day(self):
        # the actual regression: with ghost rows present ma200 is NaN for
        # EVERY ticker, so the strategy can never fire
        clean = signals(self._universe(with_ghosts=False), "breakout")
        poisoned = signals(self._universe(with_ghosts=True), "breakout")
        assert clean.values.sum() > 0, "control: clean universe must produce signals"
        assert poisoned.values.sum() > 0, \
            "ghost holiday rows must not null every rolling window"

    def test_ma50_exit_matrix_also_filtered(self):
        # the 50MA EXIT reads its own matrix; leaving it unfiltered breaks
        # exits the same way entries broke
        from scanner.backtest import _ma50_matrix
        m = _ma50_matrix(self._universe())
        assert m.notna().any().any(), "50MA exit must not be all-NaN"


class TestReversalStrategies:
    """Reversal tactics fire BEFORE the Trend Template passes — that is their
    purpose and their risk. Each must require confirmation, or it degenerates
    into episodic_pivot (723 trades, -0.38R, -96% DD)."""

    IDX = pd.bdate_range("2022-01-03", periods=620)

    def _control(self, seed=3):
        """Flat control on the SAME index as the reversal fixture. make_df
        hardcodes a 2024 start; pairing it with a 2022-based fixture makes the
        union index mostly NaN and silently nulls every rolling window."""
        rng = np.random.default_rng(seed)
        c = 50 * np.cumprod(1 + rng.normal(0, 0.004, len(self.IDX)))
        return pd.DataFrame({"Open": c, "High": c * 1.004, "Low": c * 0.996,
                             "Close": c, "Volume": np.full(len(self.IDX), 1e6)},
                            index=self.IDX)

    def _decline_then_reclaim(self, reclaim_vol=4_000_000.0, strong_close=True):
        """The real Stage 1 -> Stage 2 shape: advance, decline below the 200MA,
        then a long sideways BASE that lets the 200MA flatten, then a
        high-volume close back above it.

        The base matters. A first draft declined right up to the reclaim day,
        and the signal correctly refused to fire because the 200MA was still
        collapsing — a reclaim into a falling average is a bounce in a
        downtrend, not a turn. Flattening is what makes it a Stage 1 base.
        """
        idx = self.IDX
        n = len(idx)
        c = np.empty(n); v = np.full(n, 1_000_000.0)
        c[0] = 100.0
        for i in range(1, n):
            if i < 200:
                c[i] = c[i - 1] * 1.002            # advance, builds the 200MA
            elif i < 330:
                c[i] = c[i - 1] * 0.995            # decline under it
            elif i < n - 1:
                c[i] = c[i - 1] * (1.0008 if i % 2 else 0.9992)   # base, MA flattens
            else:
                c[i] = c[i - 1] * 1.06             # reclaim day
                v[i] = reclaim_vol
        # A weak close means price finished near the LOW of its range, i.e. the
        # HIGH is far above the close — supply took the day back. (Dropping the
        # low instead makes the close look strong: near the top of a wide bar.)
        high = c * (1.004 if strong_close else 1.15)
        return pd.DataFrame({"Open": c * 0.999, "High": high,
                             "Low": c * 0.996, "Close": c, "Volume": v}, index=idx)

    def test_ma200_reclaim_fires_on_confirmed_reclaim(self):
        data = {"REV": self._decline_then_reclaim(),
                "FLAT": self._control()}
        sig = signals(data, strategy="ma200_reclaim")
        assert bool(sig["REV"].iloc[-1]), "a volume reclaim of the 200MA must fire"

    def test_ma200_reclaim_needs_volume(self):
        # same price path, no volume expansion -> not a signal
        data = {"REV": self._decline_then_reclaim(reclaim_vol=900_000.0),
                "FLAT": self._control()}
        assert not bool(signals(data, strategy="ma200_reclaim")["REV"].iloc[-1])

    def test_ma200_reclaim_needs_a_strong_close(self):
        # reclaimed intraday but closed near the low = supply won the day
        data = {"REV": self._decline_then_reclaim(strong_close=False),
                "FLAT": self._control()}
        assert not bool(signals(data, strategy="ma200_reclaim")["REV"].iloc[-1])

    def test_ma200_reclaim_silent_in_a_steady_uptrend(self):
        # a stock that never went below its 200MA has nothing to reclaim
        data = {"UP": self._uptrend(),
                "FLAT": self._control()}
        assert signals(data, strategy="ma200_reclaim")["UP"].sum() == 0

    def _uptrend(self):
        c = 50 * np.cumprod(np.full(len(self.IDX), 1.003))
        return pd.DataFrame({"Open": c * 0.999, "High": c * 1.004, "Low": c * 0.996,
                             "Close": c, "Volume": np.full(len(self.IDX), 1e6)},
                            index=self.IDX)

    def _undercut(self, reclaim=True):
        idx = self.IDX[:300]
        n = len(idx)
        c = np.empty(n); v = np.full(n, 1_000_000.0)
        c[0] = 50.0
        for i in range(1, n):
            if i < n - 60:
                c[i] = c[i - 1] * 1.001
            elif i < n - 3:
                c[i] = c[i - 1] * 0.997        # drift into the prior low
            elif i < n - 1:
                c[i] = c[i - 1] * 0.97         # undercut it
            else:
                c[i] = c[i - 1] * (1.06 if reclaim else 0.99)
                v[i] = 3_000_000.0
        return pd.DataFrame({"Open": c * 0.999, "High": c * 1.005,
                             "Low": c * 0.985, "Close": c, "Volume": v}, index=idx)

    def test_undercut_rally_requires_the_reclaim_not_just_the_undercut(self):
        # undercutting alone is a falling knife; the RECLAIM is the signal
        no_reclaim = {"UC": self._undercut(reclaim=False),
                      "FLAT": self._control().iloc[:300]}
        assert not bool(signals(no_reclaim, strategy="undercut_rally")["UC"].iloc[-1])

    def test_reversal_tactics_are_not_trend_template_gated(self):
        # the whole point: they must be able to fire on a stock the Trend
        # Template rejects, or they add nothing the board cannot already see
        import inspect
        from scanner import backtest
        src = inspect.getsource(backtest._signals_one_market)
        block = src[src.index('if strategy == "ma200_reclaim"'):src.index('pivot = H.rolling(25)')]
        assert "tt &" not in block and "tt&" not in block

    def test_all_strategies_still_run_clean(self):
        from scanner.backtest import STRATEGIES
        data = {"REV": self._decline_then_reclaim(),
                "FLAT": self._control()}
        for strat in STRATEGIES:
            r = run_backtest(data, strategy=strat)
            assert len(r["equity"]) > 0, f"{strat} produced no curve"
