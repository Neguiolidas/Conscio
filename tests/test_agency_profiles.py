# tests/test_agency_profiles.py
"""ProbeSuite/ModelProfile — empirical capability measurement (5.10)."""
from conscio.agency.adapter import AdapterCaps, MockAdapter
from conscio.agency.profiles import (VISIBLE_TOOLS_SMALL, ModelProfile,
                                     ProbeSuite, choose_tier,
                                     max_visible_tools, skeptic_mode)

ALL_PASS = [
    '{"status": "ok", "count": 3}',                       # P1 flat echo
    '{"plan": {"tool": "x", "steps": ["a", "b"]}}',       # P2 nested
    '{"color": "red"}',                                   # P3 enum
    '{"name": "probe"}',                                  # P4 negative instr
    "TOOL: fs_read\nWHY: probe",                          # P5 KV-line
]


def _suite(tmp_path, script, caps=None):
    adapter = MockAdapter(script=list(script), caps=caps)
    return ProbeSuite(adapter, tmp_path / "conscio.db"), adapter


class TestProbeRun:
    def test_all_pass_yields_full_profile(self, tmp_path):
        suite, _ = _suite(tmp_path, ALL_PASS)
        p = suite.run()
        assert p.json_fidelity == 1.0
        assert p.schema_depth == 2
        assert p.instruction_depth == 2
        assert p.kv_ok is True
        assert p.valid is True
        assert p.model_name == "mock"
        suite.close()

    def test_partial_failures_scored(self, tmp_path):
        script = ALL_PASS.copy()
        script[1] = "not json"                  # P2 fails
        script[4] = "garbage"                   # P5 fails
        suite, _ = _suite(tmp_path, script)
        p = suite.run()
        assert p.json_fidelity == 0.75
        assert p.schema_depth == 1              # P1 ok, P2 not
        assert p.kv_ok is False
        suite.close()

    def test_all_errors_marks_invalid(self, tmp_path):
        suite, _ = _suite(tmp_path, [])         # script exhausted -> errors
        p = suite.run()
        assert p.valid is False
        assert p.json_fidelity == 0.0
        suite.close()

    def test_probe_uses_low_temperature_and_small_budget(self, tmp_path):
        suite, adapter = _suite(tmp_path, ALL_PASS)
        suite.run()
        assert all(c["temperature"] == 0.0 for c in adapter.calls)
        assert all(c["max_tokens"] <= 200 for c in adapter.calls)
        suite.close()


class TestCache:
    def test_get_caches_by_model_name(self, tmp_path):
        suite, adapter = _suite(tmp_path, ALL_PASS)
        first = suite.get()
        assert len(adapter.calls) == 5
        again = suite.get()                     # cache hit: no new calls
        assert len(adapter.calls) == 5
        assert again.json_fidelity == first.json_fidelity
        suite.close()

    def test_cache_survives_new_suite_same_db(self, tmp_path):
        suite, _ = _suite(tmp_path, ALL_PASS)
        suite.get()
        suite.close()
        suite2, adapter2 = _suite(tmp_path, [])  # would error if probed
        p = suite2.get()
        assert p.valid is True and len(adapter2.calls) == 0
        suite2.close()

    def test_force_reprobes(self, tmp_path):
        suite, adapter = _suite(tmp_path, ALL_PASS + ALL_PASS)
        suite.get()
        suite.get(force=True)
        assert len(adapter.calls) == 10
        suite.close()

    def test_invalid_profile_never_cached(self, tmp_path):
        suite, _ = _suite(tmp_path, [])
        assert suite.get().valid is False
        suite.close()
        suite2, adapter2 = _suite(tmp_path, ALL_PASS)
        assert suite2.get().valid is True       # re-probed, not cached miss
        assert len(adapter2.calls) == 5
        suite2.close()


class TestDerivations:
    def test_choose_tier_t1_when_gbnf(self):
        p = ModelProfile("m", supports_gbnf=True, valid=True)
        assert choose_tier(p) == "T1"

    def test_choose_tier_t2_needs_fidelity(self):
        good = ModelProfile("m", has_json_mode=True, json_fidelity=0.8,
                            valid=True)
        weak = ModelProfile("m", has_json_mode=True, json_fidelity=0.5,
                            valid=True)
        assert choose_tier(good) == "T2"
        assert choose_tier(weak) == "T3"

    def test_choose_tier_invalid_profile_is_none(self):
        assert choose_tier(ModelProfile("m", valid=False)) is None

    def test_caps_flow_into_profile(self, tmp_path):
        caps = AdapterCaps(model_name="llamacpp", json_mode=False,
                           grammar=True)
        suite, _ = _suite(tmp_path, ALL_PASS, caps=caps)
        p = suite.run()
        assert p.supports_gbnf is True and p.has_json_mode is False
        assert p.model_name == "llamacpp"
        suite.close()

    def test_skeptic_mode_open_only_for_capable(self):
        big = ModelProfile("m", json_fidelity=1.0, schema_depth=2, valid=True)
        small = ModelProfile("m", json_fidelity=0.5, schema_depth=1,
                             valid=True)
        assert skeptic_mode(big) == "open"
        assert skeptic_mode(small) == "checklist"

    def test_max_visible_tools_limits_small_models(self):
        big = ModelProfile("m", json_fidelity=1.0, schema_depth=2,
                           instruction_depth=2, valid=True)
        small = ModelProfile("m", schema_depth=1, instruction_depth=1,
                             valid=True)
        assert max_visible_tools(big) is None
        assert max_visible_tools(small) == VISIBLE_TOOLS_SMALL
