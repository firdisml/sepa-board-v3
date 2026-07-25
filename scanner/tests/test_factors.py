"""Factor validation: forward returns and bucket logic on synthetic frames."""
import datetime as dt

import pandas as pd
import pytest

from scanner.factors import FWD_SESSIONS, compute_factors, forward_returns


def _closes(ticker, start, closes):
    d0 = dt.date.fromisoformat(start)
    return pd.DataFrame({
        "ticker": ticker,
        "d": [d0 + dt.timedelta(days=i) for i in range(len(closes))],
        "close": closes,
    })


class TestForwardReturns:
    def test_computes_20_session_return(self):
        closes = _closes("1111.KL", "2026-01-01", [10.0] * 1 + [10.0 + 0.1 * i for i in range(1, 40)])
        cand = pd.DataFrame([{"run_date": dt.date(2026, 1, 1), "ticker": "1111.KL",
                              "quality": 50, "rs_rank": 80, "grade": "A", "anticipation": 3}])
        out = forward_returns(cand, closes)
        assert len(out) == 1
        expected = (closes["close"].iloc[FWD_SESSIONS] / 10.0 - 1) * 100
        assert out["fwd20"].iloc[0] == pytest.approx(expected, abs=0.01)

    def test_too_recent_rows_are_skipped_not_graded_early(self):
        closes = _closes("1111.KL", "2026-01-01", [10.0] * 10)   # only 10 bars ahead
        cand = pd.DataFrame([{"run_date": dt.date(2026, 1, 1), "ticker": "1111.KL",
                              "quality": 50, "rs_rank": 80, "grade": "A", "anticipation": 3}])
        assert len(forward_returns(cand, closes)) == 0

    def test_missing_ticker_skipped(self):
        closes = _closes("2222.KL", "2026-01-01", [10.0] * 40)
        cand = pd.DataFrame([{"run_date": dt.date(2026, 1, 1), "ticker": "1111.KL",
                              "quality": 50, "rs_rank": 80, "grade": "A", "anticipation": 3}])
        assert len(forward_returns(cand, closes)) == 0


class TestComputeFactors:
    def _conn_stub(self, cand_rows, candle_rows):
        class Cur:
            def __init__(self):
                self._rows = None
            def execute(self, sql, params=None):
                self._rows = candle_rows if "candles" in sql else cand_rows
            def fetchall(self):
                return self._rows
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        class Conn:
            def cursor(self):
                return Cur()
        return Conn()

    def test_predictive_factor_is_monotone_and_junk_is_not(self):
        # 60 candidates: quality 1..60. Forward return rises WITH quality
        # (predictive); anticipation is anti-correlated noise (junk).
        cand_rows, candle_rows = [], []
        d0 = dt.date(2026, 1, 1)
        for i in range(60):
            t = f"{1000+i}.KL"
            q = i + 1
            cand_rows.append((d0, t, q, 50, "B", 60 - i))
            # price path: flat then drifts by q basis points a session
            closes = [10.0 * (1 + q / 10000) ** k for k in range(FWD_SESSIONS + 5)]
            for k, c in enumerate(closes):
                candle_rows.append((t, d0 + dt.timedelta(days=k), c))

        conn = self._conn_stub(cand_rows, candle_rows)
        rows = compute_factors(conn, "MY")
        by_factor = {}
        for r in rows:
            by_factor.setdefault(r["factor"], []).append(r)

        assert all(r["monotone"] for r in by_factor["quality"])
        assert not any(r["monotone"] for r in by_factor["anticipation"])
        # every quality bucket landed with rows and a mean
        assert sum(r["n"] for r in by_factor["quality"]) == 60

    def test_small_sample_factors_skipped(self):
        d0 = dt.date(2026, 1, 1)
        cand_rows = [(d0, "1000.KL", 50, 80, "A", 3)] * 5   # 5 rows < MIN_ROWS
        candle_rows = [("1000.KL", d0 + dt.timedelta(days=k), 10.0)
                       for k in range(FWD_SESSIONS + 5)]
        conn = self._conn_stub(cand_rows, candle_rows)
        assert compute_factors(conn, "MY") == []
