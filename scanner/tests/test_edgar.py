"""EDGAR client: category/title mapping and filing shape. No network."""
import pytest

from scanner import edgar


@pytest.fixture(autouse=True)
def _stub_cik(monkeypatch):
    monkeypatch.setattr(edgar, "_cik_cache", {"ACME": 111, "BRK-B": 222})


class TestCikLookup:
    def test_bursa_ticker_has_no_cik(self):
        # .KL names are not SEC registrants — None, never an exception
        assert edgar.cik_of("1155.KL") is None

    def test_class_share_dot_is_normalised(self):
        assert edgar.cik_of("BRK.B") == 222
        assert edgar.cik_of("BRK-B") == 222

    def test_unknown_ticker_is_none(self):
        assert edgar.cik_of("NOPE") is None


class TestCategoryMapping:
    def test_8k_item_code_beats_form_type(self):
        # 1.01 is the contract-win signal §7.1 asks about
        assert edgar._category_for("8-K", "1.01,9.01") == "contract"
        assert edgar._category_for("8-K", "2.02") == "results"

    def test_us_specific_hazards_have_labels(self):
        assert edgar._category_for("8-K", "3.01") == "delisting_risk"
        assert edgar._category_for("8-K", "4.02") == "restatement"
        assert edgar._category_for("8-K", "1.03") == "bankruptcy"

    def test_form_type_used_when_no_item_codes(self):
        assert edgar._category_for("10-Q", "") == "results"
        assert edgar._category_for("SC 13D", "") == "insider_dealing"
        assert edgar._category_for("424B5", "") == "dilution"

    def test_unknown_form_is_other_never_guessed(self):
        assert edgar._category_for("WEIRD-1", "") == "other"


class TestTitles:
    def test_8k_title_names_the_event_not_just_the_form(self):
        # "8-K" alone tells a reader nothing; the item code IS the meaning
        t = edgar._title_for("8-K", "1.01,9.01")
        assert "material definitive agreement" in t.lower()
        assert t.startswith("8-K")

    def test_exhibit_only_item_is_dropped(self):
        # 9.01 rides along with real items and means nothing alone
        assert edgar._title_for("8-K", "9.01") == "8-K"

    def test_multiple_items_all_listed(self):
        t = edgar._title_for("8-K", "2.02,8.01")
        assert "Results of operations" in t and "Other event" in t


class TestFilings:
    SUBMISSION = {
        "name": "Acme Corp",
        "sicDescription": "Pharmaceutical Preparations",
        "exchanges": ["Nasdaq"],
        "filings": {"recent": {
            "accessionNumber": ["0001-26-000001", "0001-26-000002", "0001-26-000003"],
            "form": ["8-K", "4", "10-Q"],
            "items": ["1.01,9.01", "", ""],
            "filingDate": ["2026-07-20", "2026-07-19", "2026-07-18"],
            "primaryDocument": ["a.htm", "b.htm", "c.htm"],
        }},
    }

    @pytest.fixture(autouse=True)
    def _stub_get(self, monkeypatch):
        monkeypatch.setattr(edgar, "_get", lambda url: self.SUBMISSION)

    def test_shape_matches_counter_news_writer(self):
        f = edgar.filings("ACME")[0]
        # db.save_counter_news reads exactly these keys
        assert set(f) >= {"item_id", "title", "url", "source", "category", "date"}
        assert f["item_id"] == "0001-26-000001"      # accession = SEC's own dedupe key
        assert f["category"] == "contract"
        assert f["source"] == ""                      # filings have no publisher
        assert "000126000001" in f["url"]             # dashes stripped in archive path

    def test_material_filings_drops_insider_form_noise(self):
        forms = {f["form"] for f in edgar.material_filings("ACME")}
        assert "4" not in forms          # Form 4 floods crowd out material events
        assert {"8-K", "10-Q"} <= forms

    def test_company_info_carries_industry_for_group_rs(self):
        info = edgar.company_info("ACME")
        assert info["industry"] == "Pharmaceutical Preparations"
        assert info["name"] == "Acme Corp"
        assert info["exchange"] == "Nasdaq"

    def test_non_registrant_raises_rather_than_returning_empty(self):
        # an empty list would read as "this company never files"
        with pytest.raises(edgar.EdgarUnavailable):
            edgar.filings("1155.KL")


class TestMultiMarketSaveRunScoping:
    """scan-my (12:30 UTC) and scan-us (22:00 UTC) share one run_date, so a
    single-market save must not touch the other market's rows. Pure SQL-shape
    test against a stub cursor — the real behaviour was verified live."""

    class _Cur:
        def __init__(self): self.sql = []
        def execute(self, q, p=None): self.sql.append((" ".join(q.split()), p))
        def fetchone(self): return (1,)
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _Conn:
        def __init__(self, cur): self._c = cur
        def cursor(self): return self._c
        def commit(self): pass

    def _run(self, candidates, sectors=()):
        from scanner import db
        cur = self._Cur()
        db.save_run(self._Conn(cur), "2099-01-01", {"MY": {}}, list(candidates), list(sectors))
        return cur.sql

    def _cand(self, t, m):
        return {"ticker": t, "market": m, "price": 1.0, "rs_rank": 1, "bucket": "swing",
                "checks": {}, "vcp": {}, "setup": {}, "patterns": {}, "levels": {},
                "candles": [], "targets": {}, "quality": 1}

    def test_candidate_delete_is_market_scoped(self):
        sql = self._run([self._cand("AAPL", "US")])
        dels = [(q, p) for q, p in sql if q.startswith("DELETE FROM candidates")]
        assert dels, "expected a candidates delete"
        q, p = dels[0]
        assert "market = ANY" in q, "a US scan must not delete Bursa candidates"
        assert p[1] == ["US"]

    def test_regime_is_merged_not_replaced(self):
        sql = self._run([self._cand("AAPL", "US")])
        ins = [q for q, _ in sql if "INSERT INTO scan_runs" in q][0]
        # replacing would drop the other market's regime block entirely
        assert "scan_runs.regime" in ins and "||" in ins

    def test_sector_ranks_untouched_when_no_sector_rows(self):
        # sector_ranks has NO market column, so a US run (no sector rows) must
        # not issue the unscoped delete that would wipe Bursa's rotation
        sql = self._run([self._cand("AAPL", "US")], sectors=[])
        assert not [q for q, _ in sql if q.startswith("DELETE FROM sector_ranks")]
