"""Fundamentals growth math (pure, no network)."""
import pandas as pd

from scanner.fundamentals import grade, growth_metrics


def _q(rev, ni, eps=None):
    cols = pd.to_datetime(["2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30",
                           "2025-03-31", "2024-12-31", "2024-09-30", "2024-06-30"])
    rows, idx = [rev, ni], ["Total Revenue", "Net Income"]
    if eps is not None:
        rows.append(eps)
        idx.append("Diluted EPS")
    return pd.DataFrame(rows, index=idx, columns=cols)


class TestGrowthMetrics:
    def test_yoy_and_acceleration(self):
        rev = [130, 120, 110, 105, 100, 100, 100, 100]
        ni = [30, 22, 20, 20, 20, 20, 20, 20]
        m = growth_metrics(_q(rev, ni))
        assert m["rev_yoy_pct"] == 30.0
        assert m["ni_yoy_pct"] == 50.0        # 30 vs 20 a year earlier
        assert m["ni_yoy_prev_pct"] == 10.0   # 22 vs 20
        assert m["accelerating"] is True
        assert m["quarter_end"] == "2026-03-31"

    def test_decelerating_flagged(self):
        ni = [22, 30, 20, 20, 20, 20, 20, 20]
        m = growth_metrics(_q([100] * 8, ni))
        assert m["accelerating"] is False

    def test_negative_base_quarter_returns_none_pct(self):
        # growth % off a loss-making base quarter is meaningless — must be None
        ni = [10, 5, 5, 5, -2, 5, 5, 5]
        m = growth_metrics(_q([100] * 8, ni))
        assert m["ni_yoy_pct"] is None
        assert m["accelerating"] is False

    def test_short_history_safe(self):
        cols = pd.to_datetime(["2026-03-31", "2025-12-31"])
        q = pd.DataFrame([[100, 90], [10, 9]],
                         index=["Total Revenue", "Net Income"], columns=cols)
        assert growth_metrics(q) is None  # no year-ago quarter to compare

    def test_empty_and_missing(self):
        assert growth_metrics(None) is None
        assert growth_metrics(pd.DataFrame()) is None

    def test_eps_growth_and_acceleration(self):
        rev = [130, 120, 110, 105, 100, 100, 100, 100]
        ni = [30, 22, 20, 20, 20, 20, 20, 20]
        eps = [1.5, 1.1, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
        m = growth_metrics(_q(rev, ni, eps))
        assert m["eps_yoy_pct"] == 50.0        # 1.5 vs 1.0 a year earlier
        assert m["eps_yoy_prev_pct"] == 10.0   # 1.1 vs 1.0
        assert m["eps_accelerating"] is True

    def test_margin_trend(self):
        # margin 30/130 = 23.1% now vs 20/100 = 20% a year ago -> +3.1pp
        rev = [130, 120, 110, 105, 100, 100, 100, 100]
        ni = [30, 22, 20, 20, 20, 20, 20, 20]
        m = growth_metrics(_q(rev, ni))
        assert m["margin_pct"] == 23.1
        assert m["margin_delta_pp"] == 3.1


class TestGrade:
    def test_all_boxes_ticked_is_a(self):
        m = {"eps_yoy_pct": 50.0, "eps_yoy_prev_pct": 10.0, "eps_accelerating": True,
             "rev_yoy_pct": 30.0, "margin_delta_pp": 3.1, "roe_pct": 22.0}
        assert grade(m) == "A"

    def test_all_boxes_failed_is_e(self):
        m = {"eps_yoy_pct": 2.0, "eps_yoy_prev_pct": 10.0, "eps_accelerating": False,
             "rev_yoy_pct": 1.0, "margin_delta_pp": -2.0, "roe_pct": 5.0}
        assert grade(m) == "E"

    def test_sparse_data_not_graded(self):
        # only 2 known boxes -> None, missing coverage must not read as failure
        assert grade({"rev_yoy_pct": 30.0, "roe_pct": 20.0}) is None
        assert grade({}) is None

    def test_ni_growth_backfills_missing_eps(self):
        m = {"eps_yoy_pct": None, "ni_yoy_pct": 40.0, "ni_yoy_prev_pct": 10.0,
             "accelerating": True, "rev_yoy_pct": 25.0}
        # growth 40 (>=25), rev 25 (>=20), accel True -> 3/3 known boxes
        assert grade(m) == "A"
