"""EP is a measured NEGATIVE edge on Bursa — pin it as a hazard.

Full-universe backtest 2026-07-24 (PLAN §12.1): 292 trades, 17.1% win rate,
-0.38R expectancy, -75% max drawdown. These tests exist so a later refactor
cannot quietly promote it back to a buyable setup.
"""
import numpy as np
import pandas as pd

from scanner import indicators


def _ep_frame():
    """A textbook EP: quiet for months, then a violent gap on huge volume."""
    n = 80
    close = np.full(n, 1.00)
    vol = np.full(n, 100_000.0)
    df = pd.DataFrame({
        "Open": close, "High": close * 1.005, "Low": close * 0.995,
        "Close": close, "Volume": vol,
    }, index=pd.bdate_range(end="2026-07-24", periods=n))
    # gap day: opens above prior high, closes +8%, 5x volume, tight range
    df.iloc[-1, df.columns.get_loc("Open")] = 1.05
    df.iloc[-1, df.columns.get_loc("Low")] = 1.05
    df.iloc[-1, df.columns.get_loc("Close")] = 1.08
    df.iloc[-1, df.columns.get_loc("High")] = 1.09
    df.iloc[-1, df.columns.get_loc("Volume")] = 500_000.0
    return df


class TestEpisodicPivotHazard:
    def test_detector_still_fires(self):
        """The pattern must still be DETECTED — the gap is real information,
        and hiding it would remove the chance to check the catalyst."""
        ep = indicators.episodic_pivot(_ep_frame())
        assert ep is not None
        assert ep["trigger"] and ep["stop"]

    def test_carries_measured_negative_edge(self):
        ep = indicators.episodic_pivot(_ep_frame())
        assert ep["measured_edge_r"] < 0, "EP backtested -0.38R on Bursa"
        assert ep["requires_catalyst"] is True

    def test_note_warns_rather_than_invites(self):
        note = indicators.episodic_pivot(_ep_frame())["note"].lower()
        assert "negative" in note or "-0.38r" in note
        assert "avoid" in note
        # must not read like an invitation to buy
        assert "no catalyst" in note or "verified catalyst" in note

    def test_analyst_prompt_treats_ep_as_hazard(self):
        """The AI must never write a buy plan around an EP alone."""
        from scanner import analyst
        c = {"ticker": "0001.KL", "market": "MY", "bucket": "forming",
             "price": 1.08, "setup": {"episodic_pivot": {"trigger": 1.09, "stop": 1.05}}}
        task = analyst._note_payload(c, [], regime_light="green")["task"].lower()
        assert "backtests negative" in task
        assert "hazard" in task
        assert "never write a buy plan around an ep" in task
