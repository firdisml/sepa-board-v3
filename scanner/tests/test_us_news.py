"""EODHD news relevance filtering. No network — the filter is the logic."""
import pytest

from scanner import us_news


def item(title, symbols, link="http://x/1", date="2026-07-25T10:00:00+00:00"):
    return {"title": title, "symbols": symbols, "link": link, "date": date,
            "sentiment": {"polarity": 0.1}}


class TestRelevance:
    def test_headline_naming_the_ticker_is_kept(self):
        assert us_news.is_relevant(
            item("ERAS Investor Alert: claims investigated", ["ERAS.US"]), "ERAS")

    def test_headline_naming_the_company_is_kept(self):
        assert us_news.is_relevant(
            item("Erasca shareholders who lost money", ["ERAS.US"]), "ERAS", "Erasca, Inc.")

    def test_listicle_mentioning_but_not_naming_is_dropped(self):
        # the measured failure: an AAPL query returning an Alphabet article
        assert not us_news.is_relevant(
            item("What's Going on With Alphabet Stock?",
                 ["AAPL.US", "AMZN.US", "GOOG.US", "GOOGL.US",
                  "META.US", "MSFT.US", "NVDA.US", "TSLA.US"]),
            "AAPL", "Apple Inc.")

    def test_alphabetical_position_is_not_treated_as_relevance(self):
        # `symbols` is sorted ALPHABETICALLY — AAPL sits first in every
        # mega-cap listicle. Position must carry no weight.
        listicle = item("Ranking the Best Magnificent Seven Stocks",
                        ["AAPL.US", "AMZN.US", "GOOG.US", "META.US",
                         "MSFT.US", "NVDA.US", "TSLA.US"])
        assert not us_news.is_relevant(listicle, "AAPL", "Apple Inc.")

    def test_title_match_beats_tag_count(self):
        # regression: rejecting on tag-count first threw away real coverage
        # ("Anterix Surges 163%") for the sin of also mentioning peers
        assert us_news.is_relevant(
            item("Anterix Surges 163% in the Past Year: Should You Bet?",
                 [f"P{i}.US" for i in range(9)] + ["ATEX.US"]),
            "ATEX", "Anterix Inc.")

    def test_exclusive_tag_kept_even_without_title_match(self):
        # tagged with almost nothing else -> it is about this counter
        assert us_news.is_relevant(
            item("Q4 revenue beats estimates", ["ATEX.US"]), "ATEX", "Anterix Inc.")

    def test_untagged_theme_piece_dropped(self):
        assert not us_news.is_relevant(
            item("AI Memory Stocks Lead Chip Selloff",
                 ["MU.US", "NVDA.US", "SNDK.US"]), "SNDK", "Sandisk Corp")

    def test_corporate_suffixes_do_not_match(self):
        # every US company is an "Inc" — matching on it defeats the filter
        assert not us_news.is_relevant(
            item("Some Other Inc Corp Company news", ["ZZZZ.US", "YYYY.US",
                 "XXXX.US"]), "ATEX", "Anterix Inc.")


class TestHeadlines:
    def _stub(self, monkeypatch, items):
        monkeypatch.setattr(us_news, "_get", lambda **kw: items)

    def test_syndicated_duplicate_titles_collapse(self, monkeypatch):
        # the same story arrived from finance.yahoo.com AND nasdaq.com under
        # different URLs; three copies of one headline reads as three events
        self._stub(monkeypatch, [
            item("Ford Just Put Apple Maps in a $30,000 EV", ["AAPL.US"],
                 link="http://yahoo/a"),
            item("Ford just put Apple Maps in a $30,000 EV!", ["AAPL.US"],
                 link="http://nasdaq/b"),
        ])
        out = us_news.headlines("AAPL", company="Apple Inc.")
        assert len(out) == 1

    def test_shape_matches_counter_news_writer(self, monkeypatch):
        self._stub(monkeypatch, [item("ATEX beats estimates", ["ATEX.US"])])
        h = us_news.headlines("ATEX", company="Anterix Inc.")[0]
        assert set(h) >= {"item_id", "title", "url", "source", "category", "date"}
        assert h["category"] is None          # news is uncategorised; filings carry categories
        assert h["source"] == "x"             # domain, www- stripped

    def test_limit_respected(self, monkeypatch):
        self._stub(monkeypatch, [item(f"ATEX news {i}", ["ATEX.US"],
                                      link=f"http://x/{i}") for i in range(30)])
        assert len(us_news.headlines("ATEX", limit=5)) == 5

    def test_filter_can_be_disabled_for_diagnostics(self, monkeypatch):
        self._stub(monkeypatch, [item("Unrelated theme piece",
                                      [f"P{i}.US" for i in range(12)])])
        assert us_news.headlines("ATEX") == []
        assert len(us_news.headlines("ATEX", filter_relevance=False)) == 1


class TestVendorMapping:
    def test_matches_eodhd_client_convention(self):
        from scanner import eodhd_client as eod
        for t in ("AAPL", "1155.KL", "BRK-B"):
            assert us_news.to_vendor(t) == eod.to_vendor(t)
