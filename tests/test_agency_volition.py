# tests/test_agency_volition.py
"""F3 engine integration: probe() lazy profile, run(budget) L3 heartbeat."""
import json

from conscio import ConsciousnessEngine
from conscio.engine import _RAG_DISABLED
from conscio.agency.adapter import MockAdapter
from conscio.agency.loop import ActBudget
from conscio.context_manager import ConsciousnessState

ALL_PASS_PROBES = [
    '{"status": "ok", "count": 3}',
    '{"plan": {"tool": "x", "steps": ["a"]}}',
    '{"color": "red"}',
    '{"name": "probe"}',
    "TOOL: fs_read\nWHY: probe",
]

CHECKLIST_PASS = "A1: NO\nA2: NO\nA3: YES"
OPEN_PASS = '{"verdict": "PASS", "reasons": [], "risk_flags": []}'


def _proposal(tool="memory_note", args=None):
    return json.dumps({"tool": tool, "args": args or {"text": "n"},
                       "rationale": "r", "expected_outcome": "e"})


class TestProbe:
    def test_probe_without_adapter_returns_none(self, tmp_path):
        with ConsciousnessEngine("glm-5.1", storage_path=tmp_path) as eng:
            assert eng.probe() is None

    def test_probe_applies_profile_to_pipeline(self, tmp_path):
        with ConsciousnessEngine("glm-5.1", storage_path=tmp_path) as eng:
            pipe = eng.attach_adapter(MockAdapter(script=ALL_PASS_PROBES),
                                      sandbox_root=tmp_path / "sb")
            profile = eng.probe()
            assert profile.valid and profile.json_fidelity == 1.0
            assert pipe.gateway.tier == "T2"      # json_mode, no gbnf
            assert pipe.skeptic.mode == "open"    # auto from profile
            assert pipe.max_visible_tools is None

    def test_probe_caches_in_memory(self, tmp_path):
        with ConsciousnessEngine("glm-5.1", storage_path=tmp_path) as eng:
            eng.attach_adapter(MockAdapter(script=ALL_PASS_PROBES),
                               sandbox_root=tmp_path / "sb")
            first = eng.probe()
            assert eng.probe() is first           # no re-probe

    def test_explicit_skeptic_mode_survives_probe(self, tmp_path):
        with ConsciousnessEngine("glm-5.1", storage_path=tmp_path) as eng:
            pipe = eng.attach_adapter(MockAdapter(script=ALL_PASS_PROBES),
                                      sandbox_root=tmp_path / "sb",
                                      skeptic_mode="checklist")
            eng.probe()
            assert pipe.skeptic.mode == "checklist"

    def test_invalid_probe_keeps_defaults(self, tmp_path):
        with ConsciousnessEngine("glm-5.1", storage_path=tmp_path) as eng:
            pipe = eng.attach_adapter(MockAdapter(script=[]),
                                      sandbox_root=tmp_path / "sb")
            profile = eng.probe()
            assert profile.valid is False
            assert pipe.gateway.tier is None      # caps auto preserved

    def test_act_alone_never_probes(self, tmp_path):
        """A6 guard: F1/F2-style attach+act consumes zero probe calls."""
        adapter = MockAdapter(script=[_proposal(), CHECKLIST_PASS])
        with ConsciousnessEngine("glm-5.1", storage_path=tmp_path) as eng:
            eng.content_layer._session_rag = _RAG_DISABLED
            eng.attach_adapter(adapter, sandbox_root=tmp_path / "sb")
            report = eng.act(ConsciousnessState(active_goals=["note it"]))
            assert report.status.value in ("proposed", "executed")
            assert len(adapter.calls) == 2        # actor + skeptic only


class TestRun:
    # v1.5 R9: run() is the autonomous heartbeat and requires Awake Mode; these
    # tests wake() first because they exercise autonomous operation. (The asleep
    # = reflect-only contract is proven in tests/test_awake.py.)
    def test_run_without_adapter_fails_cleanly(self, tmp_path):
        with ConsciousnessEngine("glm-5.1", storage_path=tmp_path) as eng:
            eng.wake()
            report = eng.run()
            assert report.stopped == "no adapter attached"
            assert report.cycles == 0

    def test_run_probes_then_cycles(self, tmp_path):
        cycles = 2
        script = list(ALL_PASS_PROBES)
        for _ in range(cycles):
            script += [_proposal(), OPEN_PASS]    # probe sets mode=open
        adapter = MockAdapter(script=script)
        with ConsciousnessEngine("glm-5.1", storage_path=tmp_path) as eng:
            eng.content_layer._session_rag = _RAG_DISABLED
            eng.attach_adapter(adapter, sandbox_root=tmp_path / "sb")
            eng.wake()
            eng.goals.add_user_goal("write a memory note")
            report = eng.run(ActBudget(max_cycles=cycles, max_wall_s=120.0))
            assert report.cycles == cycles
            assert report.stopped == "max_cycles"
            # loop budget counts only its own consumption (actor+skeptic);
            # the one-time probe cost is visible on the engine meter
            assert report.llm_calls == cycles * 2
            assert eng._act_meter.calls == 5 + cycles * 2
            assert eng._model_profile is not None

    def test_run_ledger_records_real_tier_and_adapter(self, tmp_path):
        script = list(ALL_PASS_PROBES) + [_proposal(), OPEN_PASS]
        with ConsciousnessEngine("glm-5.1", storage_path=tmp_path) as eng:
            eng.content_layer._session_rag = _RAG_DISABLED
            pipe = eng.attach_adapter(MockAdapter(script=script),
                                      sandbox_root=tmp_path / "sb")
            eng.wake()
            eng.goals.add_user_goal("write a memory note")
            eng.run(ActBudget(max_cycles=1, max_wall_s=120.0))
            rows = pipe.ledger.latest(1)
            assert rows and rows[0]["tier"] == "T2"
            assert rows[0]["adapter"] == "MockAdapter"   # unwrapped name
