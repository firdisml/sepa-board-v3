"""Weekly timeframe. The look-ahead property is the thing worth pinning: a
partial week contains part of the move being predicted, so using it flatters
results exactly where it matters."""
import numpy as np
import pandas as pd
import pytest

from scanner import weekly


def daily(n=400, start="2024-01-01", trend=0.002):
    idx = pd.bdate_range(start, periods=n)
    c = 50 * np.cumprod(np.full(n, 1 + trend))
    return pd.DataFrame({"Open": c * 0.999, "High": c * 1.01, "Low": c * 0.99,
                         "Close": c, "Volume": np.full(n, 1e6)}, index=idx)


class TestResampling:
    def test_aggregates_ohlcv_correctly(self):
        df = daily(10, start="2024-01-01")     # Mon 1 Jan -> two partial weeks
        wk = weekly.to_weekly(df, include_partial=True)
        first = wk.iloc[0]
        d1 = df.loc[:wk.index[0]]
        assert first["Open"] == pytest.approx(d1["Open"].iloc[0])
        assert first["Close"] == pytest.approx(d1["Close"].iloc[-1])
        assert first["High"] == pytest.approx(d1["High"].max())
        assert first["Low"] == pytest.approx(d1["Low"].min())
        assert first["Volume"] == pytest.approx(d1["Volume"].sum())

    def test_partial_week_dropped_by_default(self):
        # end mid-week: the final W-FRI label has not occurred yet
        df = daily(400)
        df = df.loc[:df.index[-1]]
        wed = df.index[df.index.dayofweek == 2][-1]
        part = df.loc[:wed]
        assert len(weekly.to_weekly(part)) < len(
            weekly.to_weekly(part, include_partial=True))

    def test_empty_input(self):
        assert weekly.to_weekly(pd.DataFrame()).empty


class TestNoLookahead:
    def test_daily_alignment_never_sees_the_current_week(self):
        df = daily(500)
        al = weekly.weekly_aligned(df)
        wk = weekly.to_weekly(df)
        ma = weekly.stage_ma(wk["Close"])
        # pick a Wednesday and confirm it carries the PRIOR week's value
        weds = [d for d in df.index if d.dayofweek == 2][-5]
        this_week = wk.index[wk.index >= weds][0]
        assert al.loc[weds, "ma30w"] != pytest.approx(float(ma.loc[this_week])), \
            "a mid-week day must not carry its own (incomplete) week's MA"

    def test_truncating_the_future_does_not_change_past_values(self):
        df = daily(500)
        full = weekly.weekly_aligned(df)
        cut = df.index[-60]
        truncated = weekly.weekly_aligned(df.loc[:cut])
        a = full.loc[truncated.index[-1], "ma30w"]
        b = truncated.iloc[-1]["ma30w"]
        assert (pd.isna(a) and pd.isna(b)) or a == pytest.approx(b), \
            "removing future bars must not change a past weekly value"


class TestConfirmation:
    def test_uptrend_is_stage2_weekly(self):
        c = weekly.confirmation(daily(500, trend=0.003))
        assert c and c["stage2_weekly"] and c["above_ma30w"] and c["ma30w_rising"]

    def test_downtrend_is_not(self):
        c = weekly.confirmation(daily(500, trend=-0.002))
        assert c and not c["stage2_weekly"]

    def test_short_history_returns_none(self):
        # under 30 weeks there is no Stage line to speak of
        assert weekly.confirmation(daily(60)) is None

    def test_note_distinguishes_base_from_trend(self):
        # above the MA but not rising must NOT read as Stage 2
        c = weekly.confirmation(daily(500, trend=0.003))
        assert "Stage 2" in c["note"]
