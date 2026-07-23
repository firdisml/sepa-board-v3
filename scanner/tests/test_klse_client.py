"""Parser tests against a REAL captured page (fixture: MAYBANK 1155).

These are the tests that would have caught the i3investor mistake: they assert
ROWS, not that a page loaded. A source that returns headers with an empty
tbody fails every one of them.
"""
import gzip
import pathlib
from io import StringIO

import pandas as pd
import pytest
from bs4 import BeautifulSoup

from scanner import fundamentals, klse_client as k

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "klsescreener_1155.html.gz"


@pytest.fixture(scope="module")
def html():
    with gzip.open(FIXTURE, "rt", encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def tables(html):
    return pd.read_html(StringIO(html))


@pytest.fixture(scope="module")
def soup(html):
    return BeautifulSoup(html, "html.parser")


class TestTickerMapping:
    def test_code_of(self):
        assert k.code_of("1155.KL") == "1155"
        assert k.code_of("0250.KL") == "0250"

    def test_url(self):
        assert k.stock_url("1155").endswith("/v2/stocks/view/1155")


class TestQuarters:
    def test_parses_rows(self, tables):
        qs = k.parse_quarters(tables)
        assert len(qs) >= 8, "the fundamentals source must yield real quarters"
        q = qs[0]
        assert q["quarter_end"] and q["revenue"] and q["eps"] is not None

    def test_newest_first(self, tables):
        qs = k.parse_quarters(tables)
        ends = [q["quarter_end"] for q in qs if q["quarter_end"]]
        assert ends == sorted(ends, reverse=True)

    def test_no_fabricated_zeros(self, tables):
        # a missing figure must be None, never 0 — 0 would feed the grade math
        for q in k.parse_quarters(tables):
            for key in ("revenue", "net_profit", "eps"):
                assert q[key] is None or isinstance(q[key], float)


class TestAnnualAndDividends:
    def test_annual(self, tables):
        rows = k.parse_annual(tables)
        assert len(rows) >= 5, "5y trend is what separates an inflection from a blip"
        assert any(r["revenue"] for r in rows)

    def test_dividends_have_ex_dates(self, tables):
        divs = k.parse_dividends(tables)
        assert divs, "ex-date near a breakout is a gap hazard — we need these"
        assert any(d["ex_date"] for d in divs)


class TestShareholding:
    def test_summarises_window(self, tables):
        s = k.parse_shareholding(tables, days=3650)
        assert s["acquired"] or s["disposed"]
        assert s["net_shares"] == s["acquired"] - s["disposed"]
        assert s["holders"], "top movers identify the institutional sponsor"


class TestAnnouncementsAndNews:
    def test_announcements_are_linked(self, soup):
        items = k.parse_announcements(soup)
        assert items, "announcements are the highest-value Bursa read"
        for it in items:
            assert it["url"].startswith("https://"), "every item must be hyperlinked"
            assert it["title"]
            assert "category" in it

    def test_news_are_linked_with_source(self, soup):
        items = k.parse_news(soup)
        assert items
        assert all(i["url"].startswith("https://") for i in items)

    def test_classification(self):
        assert k.classify("Changes in Sub. S-hldr's Int (Section 138 of CA 2016) "
                          "- EMPLOYEES PROVIDENT FUND BOARD") == "insider_dealing"
        assert k.classify("Private Placement of up to 10% of issued shares") == "dilution"
        assert k.classify("Unusual Market Activity") == "uma"
        assert k.classify("Quarterly rpt on consolidated results") == "results"
        assert k.classify("Letter of Award from PETRONAS") == "contract"
        assert k.classify("Change of Registered Address") == "other"


class TestFetchGuards:
    """Regression tests for two live-only bugs the fixture tests cannot see."""

    def test_browser_headers_are_sent(self):
        # `requests.Session` is born with User-Agent: python-requests/x.y, which
        # this source answers with 403. The original code used headers.setdefault,
        # a silent no-op, so fetch() never once worked live.
        assert "Mozilla" in k.BROWSER_HEADERS["User-Agent"]
        assert "python-requests" not in k.BROWSER_HEADERS["User-Agent"]

    def test_session_headers_are_overridden_not_defaulted(self):
        import requests
        s = requests.Session()
        assert "python-requests" in s.headers["User-Agent"]  # the trap
        s.headers.update(k.BROWSER_HEADERS)
        assert "Mozilla" in s.headers["User-Agent"]

    def test_throttled_page_detected(self):
        # A burst limit answers 200 with the rows stripped. That must NOT be
        # reported as a layout change, or it sends someone chasing a phantom bug.
        assert k._throttled("<html><body>nothing here</body></html>")
        assert not k._throttled("<tr>" * 50)

    def test_throttle_is_not_aggressive(self):
        # measured: ~5 requests at 1.5s spacing trips the limit; 10s ran clean
        assert k.THROTTLE >= 5


class TestNumberParsing:
    def test_handles_bursa_formats(self):
        assert k._num("14,915,455") == 14915455.0
        assert k._num("20.53") == 20.53
        assert k._num("4.2%") == 4.2
        assert k._num("(1,234)") == -1234.0
        for empty in ("-", "", "nan", None):
            assert k._num(empty) is None

    def test_dates(self):
        assert k._date("31 Dec, 2026") == "2026-12-31"
        assert k._date("22 Jul 2026") == "2026-07-22"
        assert k._date("rubbish") is None


class TestFundamentalsIntegration:
    def test_grade_from_real_page(self, tables):
        d = {"code": "1155", "url": k.stock_url("1155"),
             "quarters": k.parse_quarters(tables)}
        f = fundamentals.from_dossier(d)
        assert f is not None, "a blue chip with 120 quarters must produce metrics"
        assert f["source"] == "klsescreener"
        assert f["source_url"].endswith("/1155")
        assert f["grade"] in {None, "A", "B", "C", "D", "E"}

    def test_too_few_quarters_returns_none(self):
        # 4 quarters cannot support a YoY comparison — must degrade, not guess
        few = [{"quarter_end": f"2026-0{i}-01", "revenue": 100.0,
                "net_profit": 10.0, "eps": 1.0} for i in range(1, 5)]
        assert fundamentals.frame_from_quarters(few) is None
        assert fundamentals.from_dossier({"quarters": few}) is None

    def test_negative_base_yields_none_not_fake_growth(self):
        qs = [{"quarter_end": "2026-06-30", "revenue": 100.0, "net_profit": 50.0, "eps": 1.0}]
        qs += [{"quarter_end": f"202{5 - i // 4}-0{(i % 4) + 1}-30", "revenue": 100.0,
                "net_profit": -10.0, "eps": -0.5} for i in range(1, 6)]
        m = fundamentals.growth_metrics(fundamentals.frame_from_quarters(qs))
        assert m is None or m["ni_yoy_pct"] is None


class TestFeedParsing:
    """PLAN §7.2 feeds. Synthetic markup mirroring the stock page's embedded
    list style until the backfill workflow captures a real feed fixture —
    parse_feed keys on the /view/{id} links, not on container classes, so it
    must survive markup it has never seen."""

    PAGE = """
    <html><body><div class="container">
      <ul class="list-group">
        <li class="list-group-item">
          <a href="/v2/news/view/1759463/99-speed-mart-growth-intact">
            <img src="/thumb.jpg"></a>
          <h6><a href="/v2/news/view/1759463/99-speed-mart-growth-intact">
            99 Speed Mart growth intact</a></h6>
          <span>TheStar</span>
          <time datetime="2026-07-22 00:00:00">22 Jul, 2026</time>
        </li>
        <li class="list-group-item">
          <h6><a href="/v2/news/view/1759175/x">Chinese headline</a></h6>
          <span>Chinapress</span>
          <time datetime="2026-07-21 09:30:00">21 Jul, 2026</time>
        </li>
      </ul>
      <a href="/v2/stocks/view/5326">99SMART</a>
    </div></body></html>
    """

    def test_items_extracted_with_ids_dates_sources(self):
        items = k.parse_feed(self.PAGE)
        assert [i["item_id"] for i in items] == ["1759463", "1759175"]
        first = items[0]
        assert first["title"] == "99 Speed Mart growth intact"
        assert first["url"].startswith("https://www.klsescreener.com/v2/news/view/")
        assert first["source"] == "TheStar"
        assert first["date"] == "2026-07-22 00:00:00"

    def test_thumbnail_anchor_not_duplicated_and_neighbours_not_bled(self):
        items = k.parse_feed(self.PAGE)
        assert len(items) == 2                    # img anchor didn't double-count
        assert items[1]["date"] == "2026-07-21 09:30:00"  # own time, not sibling's

    def test_non_view_links_ignored_and_empty_page_is_empty(self):
        assert k.parse_feed("<html><body><a href='/v2/stocks/view/5326'>x</a>"
                            "</body></html>") == []
        assert k.parse_feed("") == []


class TestFeedWalk:
    def _pages(self, monkeypatch, pages):
        calls = []
        def fake_get(url, session=None):
            calls.append(url)
            return pages[min(len(calls), len(pages)) - 1]
        monkeypatch.setattr(k, "_get", fake_get)
        return calls

    ITEM = ('<li><h6><a href="/v2/news/view/{i}/t">title {i}</a></h6>'
            '<time datetime="2026-07-01 00:00:00">x</time></li>')

    def test_stops_on_empty_page(self, monkeypatch):
        calls = self._pages(monkeypatch, [self.ITEM.format(i=1), "<html></html>"])
        items = k.news_feed("5326", max_pages=10)
        assert [i["item_id"] for i in items] == ["1"]
        assert len(calls) == 2                    # page 2 empty -> no page 3

    def test_stops_when_nothing_new(self, monkeypatch):
        calls = self._pages(monkeypatch, [self.ITEM.format(i=7)])
        items = k.news_feed("5326", max_pages=10, known_ids={"7"})
        assert items == []
        assert len(calls) == 1                    # caught up on page 1

    def test_announcements_gain_category(self, monkeypatch):
        page = ('<li><h6><a href="/v2/announcements/view/11634777">'
                "Changes in Sub. S-hldr's Int (Section 138)</a></h6></li>")
        self._pages(monkeypatch, [page, ""])
        items = k.announcements_feed("5326", max_pages=2)
        assert items[0]["category"] == "insider_dealing"

    def test_max_pages_caps_the_walk(self, monkeypatch):
        calls = self._pages(monkeypatch, [self.ITEM.format(i=1),
                                          self.ITEM.format(i=2),
                                          self.ITEM.format(i=3)])
        k.news_feed("5326", max_pages=2)
        assert len(calls) == 2
