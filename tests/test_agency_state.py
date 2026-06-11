# tests/test_agency_state.py
"""Lockdown persistence on ConsciousnessState + ModelInfo agentic flags
(blueprint sections 5 and 6 — literal requirements)."""
from conscio.context_manager import ConsciousnessState, ContextManager
from conscio.models import ContextMode, ModelInfo


class TestLockdownField:
    def test_defaults_to_false(self):
        assert ConsciousnessState().action_lockdown is False

    def test_roundtrip_through_save_and_load(self, tmp_path):
        manager = ContextManager("glm-5.1", storage_path=tmp_path)
        state = ConsciousnessState(state_summary="s", action_lockdown=True)
        manager.save_state(state)
        loaded = manager.load_state()
        assert loaded.action_lockdown is True

    def test_legacy_payload_without_key_loads_false(self, tmp_path):
        manager = ContextManager("glm-5.1", storage_path=tmp_path)
        manager.save_state(ConsciousnessState(state_summary="old"))
        # simulate a pre-v1.0 file: strip the key from the saved JSON
        import json
        path = tmp_path / "state_summary.json"
        data = json.loads(path.read_text())
        data.pop("action_lockdown", None)
        path.write_text(json.dumps(data))
        assert manager.load_state().action_lockdown is False

    def test_latch_survives_build_state_cycle(self, tmp_path):
        """reflect() rebuilds state via build_state + save_state; the
        latch must survive that cycle (A4 — persistent lockdown)."""
        manager = ContextManager("glm-5.1", storage_path=tmp_path)
        manager.save_state(ConsciousnessState(action_lockdown=True))
        rebuilt = manager.build_state(state_summary="fresh reflect output")
        assert rebuilt.action_lockdown is True
        manager.save_state(rebuilt)
        assert manager.load_state().action_lockdown is True

    def test_latch_clearable_explicitly(self, tmp_path):
        manager = ContextManager("glm-5.1", storage_path=tmp_path)
        manager.save_state(ConsciousnessState(action_lockdown=True))
        cleared = manager.build_state(action_lockdown=False)
        assert cleared.action_lockdown is False

    def test_latch_ignores_non_dict_state_file(self, tmp_path):
        manager = ContextManager("glm-5.1", storage_path=tmp_path)
        (tmp_path / "state_summary.json").write_text("[1, 2]")
        assert manager.build_state().action_lockdown is False


class TestModelInfoFlags:
    def test_flags_default_false(self):
        info = ModelInfo(name="m", context_window=128000,
                         mode=ContextMode.COMPACT)
        assert info.has_json_mode is False
        assert info.supports_gbnf is False

    def test_flags_settable(self):
        info = ModelInfo(name="m", context_window=128000,
                         mode=ContextMode.COMPACT, has_json_mode=True,
                         supports_gbnf=True)
        assert info.has_json_mode and info.supports_gbnf
