"""US fundamentals from XBRL: duration filtering, restatements, quarter grid."""
import pandas as pd
import pytest

from scanner import us_fundamentals as uf


def fact(end, val, start=None, frame=None, filed="2026-01-01"):
    f = {"end": end, "val": val, "filed": filed}
    if start:
        f["start"] = start
    if frame:
        f["frame"] = frame
    return f


def gaap(**tags):
    return {t: {"units": {"USD": facts}} for t, facts in tags.items()}


class TestDurationFiltering:
    def test_quarterly_frame_label_accepted(self):
        assert uf._is_quarter_duration(fact("2026-03-31", 1, frame="CY2026Q1"))

    def test_instant_frame_rejected(self):
        # a trailing I is a balance-sheet point, never a flow
        assert not uf._is_quarter_duration(fact("2026-03-31", 1, frame="CY2026Q1I"))

    def test_annual_frame_rejected(self):
        assert not uf._is_quarter_duration(fact("2026-03-31", 1, frame="CY2025"))

    def test_three_month_duration_accepted_without_frame(self):
        assert uf._is_quarter_duration(fact("2026-03-31", 1, start="2026-01-01"))

    def test_year_to_date_cumulative_rejected(self):
        # the trap: a 10-Q reports BOTH the quarter and the 6/9-month running
        # total. Taking the cumulative makes growth fiction.
        assert not uf._is_quarter_duration(fact("2026-09-30", 1, start="2026-01-01"))

    def test_missing_dates_rejected(self):
        assert not uf._is_quarter_duration({"val": 1})


class TestRestatements:
    def test_newest_filing_wins(self):
        g = gaap(Revenues=[
            fact("2026-03-31", 100, frame="CY2026Q1", filed="2026-04-30"),
            fact("2026-03-31", 111, frame="CY2026Q1", filed="2026-08-30"),  # restated
        ] + [fact(f"202{i}-06-30", 50, frame=f"CY202{i}Q2") for i in range(1, 6)])
        series = uf._flow_series(g, ("Revenues",))
        assert series["2026-03-31"] == 111


class TestQuarterGrid:
    def test_consecutive_quarters_map_one_to_one(self):
        ends = ["2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31"]
        grid = uf._quarter_grid(ends, span=5)
        assert [k for _, k in grid] == ends

    def test_missing_quarter_becomes_a_labelled_gap(self):
        # Apple's case: the September quarter exists only as a 12-month figure
        # in the 10-K, so it is absent here and MUST leave a hole — otherwise
        # position i+4 stops meaning "one year ago"
        ends = ["2026-03-31", "2025-12-31", "2025-06-30", "2025-03-31", "2024-12-31"]
        grid = uf._quarter_grid(ends, span=5)
        keys = [k for _, k in grid]
        assert keys[2] is None, "the gap must be preserved, not closed up"
        assert keys[0] == "2026-03-31" and keys[3] == "2025-06-30"

    def test_gap_label_is_never_nat(self):
        # growth_metrics re-sorts on q.columns; NaT poisons that sort and every
        # metric silently came back None
        ends = ["2026-03-31", "2025-12-31", "2025-06-30", "2025-03-31", "2024-12-31"]
        for label, _ in uf._quarter_grid(ends, span=6):
            assert not pd.isna(label)

    def test_empty_input(self):
        assert uf._quarter_grid([]) == []


class TestFrameBuilding:
    def _five_quarters(self, rev_tag="Revenues"):
        ends = ["2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31"]
        rev = [fact(e, 100 + i * 10, frame=f"CY-{i}") for i, e in enumerate(ends)]
        # frame strings above are non-matching on purpose; use durations instead
        rev = [fact(e, 200 - i * 10, start=str(pd.Timestamp(e) - pd.Timedelta(days=89))[:10])
               for i, e in enumerate(ends)]
        ni = [fact(e, 20 - i, start=str(pd.Timestamp(e) - pd.Timedelta(days=89))[:10])
              for i, e in enumerate(ends)]
        return {rev_tag: {"units": {"USD": rev}}, "NetIncomeLoss": {"units": {"USD": ni}}}

    def test_builds_expected_line_items(self):
        fr = uf.frame_from_xbrl(self._five_quarters())
        assert list(fr.index) == ["Total Revenue", "Net Income"]
        assert fr.shape[1] >= 5

    def test_revenue_tag_fallback_chain(self):
        # Sandisk uses the post-2018 tag, Anterix plain Revenues — both must work
        for tag in ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"):
            assert uf.frame_from_xbrl(self._five_quarters(tag)) is not None

    def test_too_few_quarters_returns_none(self):
        g = self._five_quarters()
        g["Revenues"]["units"]["USD"] = g["Revenues"]["units"]["USD"][:3]
        assert uf.frame_from_xbrl(g) is None

    def test_no_usable_tags_returns_none(self):
        assert uf.frame_from_xbrl({"SomethingElse": {"units": {"USD": []}}}) is None


class TestGradeContract:
    def test_grade_comes_from_shared_scorecard(self, monkeypatch):
        # the point of this module: an A on a US counter must mean what an A
        # means on a Bursa counter, so grading is DELEGATED, never reimplemented
        from scanner import fundamentals
        calls = []
        monkeypatch.setattr(fundamentals, "grade",
                            lambda m: calls.append(m) or "B")
        monkeypatch.setattr(uf.edgar, "_cik_cache", {"ACME": 1})
        monkeypatch.setattr(uf.edgar, "_get", lambda url: {"facts": {"us-gaap":
            TestFrameBuilding()._five_quarters()}})
        out = uf.for_ticker("ACME")
        assert out["grade"] == "B" and len(calls) == 1
        assert out["source"] == "sec-xbrl"

    def test_bursa_ticker_returns_none(self):
        assert uf.for_ticker("1155.KL") is None
