"""AI analyst: pure-helper tests (no API calls, no DB)."""
from scanner.analyst import (_brief_payload, _clean_assessment, _extract_json,
                             _note_payload, _probe)


def _err(code=None):
    e = RuntimeError("boom")
    if code is not None:
        e.code = code
    return e


class TestProbe:
    def test_healthy_model_ok_first_try(self):
        calls = []
        assert _probe(["a"], calls.append, pause=lambda s: None) == {"a": "ok"}
        assert calls == ["a"]

    def test_404_is_dead_with_no_retry(self):
        calls = []
        def ping(m):
            calls.append(m)
            raise _err(404)
        assert _probe(["a"], ping, pause=lambda s: None) == {"a": "dead"}
        assert calls == ["a"]  # wrong id — a second try can't fix it

    def test_busy_twice_is_busy(self):
        calls = []
        def ping(m):
            calls.append(m)
            raise _err(503)
        assert _probe(["a"], ping, pause=lambda s: None) == {"a": "busy"}
        assert calls == ["a", "a"]  # one retry, then condemned for the run

    def test_single_blip_recovers_to_ok(self):
        seen = []
        def ping(m):
            seen.append(m)
            if len(seen) == 1:
                raise _err(503)
        assert _probe(["a"], ping, pause=lambda s: None) == {"a": "ok"}

    def test_network_error_without_code_is_busy_not_dead(self):
        def ping(m):
            raise _err()  # no .code attribute at all
        assert _probe(["a"], ping, pause=lambda s: None) == {"a": "busy"}

    def test_duplicate_models_probed_once(self):
        calls = []
        assert _probe(["a", "b", "a"], calls.append, pause=lambda s: None) \
            == {"a": "ok", "b": "ok"}
        assert calls == ["a", "b"]


class TestExtractJson:
    def test_plain_object(self):
        assert _extract_json('{"risk": "low", "note": "ok"}') == {"risk": "low", "note": "ok"}

    def test_fenced_or_wrapped(self):
        # models occasionally wrap JSON despite instructions — parser must cope
        txt = 'Here you go:\n```json\n{"tone": "neutral", "bullets": ["a"]}\n```'
        assert _extract_json(txt) == {"tone": "neutral", "bullets": ["a"]}

    def test_garbage_returns_none(self):
        assert _extract_json("no json here") is None
        assert _extract_json("") is None
        assert _extract_json(None) is None

    def test_non_object_returns_none(self):
        assert _extract_json('["a", "b"]') is None


class TestNotePayload:
    def _cand(self):
        return {
            "ticker": "1234.KL", "name": "Test Bhd", "market": "MY", "bucket": "swing",
            "price": 1.50, "pivot": 1.60, "stop": 1.44, "target_2r": 1.92, "target_3r": 2.08,
            "rs_rank": 92, "group_rs": 80,
            "quality": 77, "adr_pct": 3.1, "extended": False, "earnings": None,
            "sector": "Technology", "industry": None,
            "vcp": {"vcp": True, "contractions_pct": [12.0, 6.1, 3.2]},
            "setup": {"ipo": False, "pocket_pivot": True, "tightening": False,
                      "early_entry": {"trigger": 1.55, "stop": 1.47},
                      "warnings": [{"code": "climax_run"}]},
        }

    def test_headlines_capped_at_twelve(self):
        # counter_news history (PLAN §7.2) widened the window from 5 to 12
        heads = [{"title": f"h{i}", "publisher": "X", "date": "2026-07-01"} for i in range(20)]
        p = _note_payload(self._cand(), heads)
        assert len(p["data"]["headlines"]) == 12

    def test_announcements_ride_along_with_task_guidance(self):
        anns = [{"title": "Private placement of new shares", "category": "dilution",
                 "date": "2026-07-20"}] * 12
        p = _note_payload(self._cand(), [], announcements=anns)
        assert len(p["data"]["recent_announcements"]) == 8
        assert p["data"]["recent_announcements"][0]["category"] == "dilution"
        assert "CODE-assigned" in p["task"]      # model told not to re-classify
        assert "dilution" in p["task"]
        # without announcements: no dangling instructions, field stays null
        p0 = _note_payload(self._cand(), [])
        assert p0["data"]["recent_announcements"] is None
        assert "CODE-assigned" not in p0["task"]

    def test_computed_values_passed_through_not_recomputed(self):
        p = _note_payload(self._cand(), [], regime_light="yellow")
        d = p["data"]
        assert d["rs_rank"] == 92 and d["vcp_valid"] is True
        assert d["target_2r"] == 1.92 and d["target_3r"] == 2.08
        assert d["early_entry"] == {"trigger": 1.55, "stop": 1.47}
        assert d["market_regime_light"] == "yellow"
        assert d["setup_flags"]["warnings"] == ["climax_run"]
        assert d["headlines"] == []

    def test_task_demands_concrete_plan(self):
        p = _note_payload(self._cand(), [])
        assert "trade plan" in p["task"].lower()
        assert "invalidation" in p["task"]
        # the task must force 'unknown risk' wording when there is no news
        assert "no recent news found" in p["task"]
        assert set(p["output_schema"]["plan"]) == {"entry", "stop", "targets", "invalidation"}


class TestCleanAssessment:
    def test_sanitizes_tones_and_caps(self):
        raw = [
            {"title": "Trend & momentum", "tone": "info", "lines": ["RS 92, above all MAs"]},
            {"title": "Warnings", "tone": "warn", "lines": ["earnings in 4 days"]},
            {"title": "Bad tone", "tone": "explode", "lines": ["x"]},          # tone coerced to info
            {"title": "Empty", "tone": "info", "lines": []},                    # dropped
            {"no_title": True, "lines": ["y"]},                                 # dropped
        ] + [{"title": f"S{i}", "tone": "info", "lines": ["z"]} for i in range(10)]
        out = _clean_assessment(raw)
        assert len(out) == 6                                # capped
        assert out[0]["lines"] == ["RS 92, above all MAs"]
        assert out[1]["tone"] == "warn"
        assert out[2]["tone"] == "info"                     # coerced
        assert all(o["title"] and o["lines"] for o in out)

    def test_handles_garbage(self):
        assert _clean_assessment(None) == []
        assert _clean_assessment("not a list") == []
        assert _clean_assessment([{"title": "T", "lines": ["a" * 999]}])[0]["lines"][0] == "a" * 300


class TestBriefPayload:
    def test_board_counters_capped_and_passed_through(self):
        counters = [{"ticker": f"T{i}", "sector": "Tech", "bucket": "swing", "rs_rank": 90}
                    for i in range(60)]
        p = _brief_payload("US", {"light": "green"}, {"swing": 3}, ["NEW1"], ["OLD1"],
                           [], [], [], None, counters)
        assert len(p["data"]["board_counters"]) == 40
        # the model must be constrained to board tickers only
        assert "board_counters" in p["task"]
        assert "counters" in p["output_schema"]
