"""The cognitive cycle — an explicit, useful reflect loop (Lote 3).

reflect() already runs perceive/coherence/self-improve/goals internally but
returns a flat dict, so the loop is invisible and hard to use. cognitive_cycle
orchestrates the six stages the agent actually goes through and returns a
structured, JSON-serializable report of each:

    Reflect -> Synthesize -> Propose+Act -> Learn -> Self-improve

Reflect/synthesize/self-improve are offline-heuristic (no LLM). The Act stage
reuses engine.act() unchanged — fully gated (skeptic/trust/breaker/approval),
NOT awake-gated because the host drives it. With no adapter, Act is skipped.
"""
from unittest.mock import MagicMock

from conscio.engine import ConsciousnessEngine

STAGES = {"reflection", "synthesis", "action", "learning", "self_improvement"}


def test_cognitive_cycle_offline_runs_reportable_stages(tmp_path):
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    try:
        rep = eng.cognitive_cycle(world_state="all systems operational")
        assert STAGES <= set(rep)
        # offline: no adapter -> no proposal/act
        assert rep["action"] is None
        # reflection ran
        assert isinstance(rep["reflection"], dict) and rep["reflection"]
    finally:
        eng.close()


def test_synthesis_consolidates_coherence_and_focus(tmp_path):
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    try:
        rep = eng.cognitive_cycle(world_state="latency spike on db",
                                  anomalies=["db latency"])
        syn = rep["synthesis"]
        assert "insight" in syn and isinstance(syn["insight"], str) and syn["insight"]
        assert "coherence" in syn
        assert "focus_goals" in syn and isinstance(syn["focus_goals"], list)
        assert 0.0 <= syn["confidence"] <= 1.0
    finally:
        eng.close()


def test_self_improvement_is_a_list_and_survives_no_errors(tmp_path):
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    try:
        rep = eng.cognitive_cycle(world_state="ok")
        assert isinstance(rep["self_improvement"]["new_proposals"], list)
        assert isinstance(rep["self_improvement"]["pending"], int)
    finally:
        eng.close()


def test_action_stage_invokes_act_when_pipeline_present(tmp_path, monkeypatch):
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    try:
        # Simulate an attached pipeline + a proposed act, WITHOUT downgrading
        # any gate — cognitive_cycle must just call engine.act() and surface it.
        class _Rep:
            def __init__(self):
                from conscio.agency.act import ActStatus
                self.status = ActStatus.PROPOSED
                self.reason = "queued for approval"
                self.ledger_id = 7
                self.lockdown = False
        monkeypatch.setattr(eng, "_act_pipeline", MagicMock(), raising=False)
        monkeypatch.setattr(eng, "act", lambda *a, **k: _Rep())
        rep = eng.cognitive_cycle(world_state="deploy failed", act=True)
        assert rep["action"] is not None
        assert rep["action"]["status"] == "proposed"
        assert rep["action"]["ledger_id"] == 7
    finally:
        eng.close()


def test_act_false_skips_action_even_with_pipeline(tmp_path, monkeypatch):
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    try:
        monkeypatch.setattr(eng, "_act_pipeline", MagicMock(), raising=False)
        called = {"n": 0}
        monkeypatch.setattr(eng, "act", lambda *a, **k: called.__setitem__("n", 1))
        rep = eng.cognitive_cycle(world_state="ok", act=False)
        assert rep["action"] is None
        assert called["n"] == 0
    finally:
        eng.close()


def test_mcp_exposes_cognitive_cycle(tmp_path):
    from conscio.mcp.seen import SeenStore
    from conscio.mcp.server import Bindings
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path / "s")
    seen = SeenStore(tmp_path / "seen.db")
    b = Bindings(eng, seen, adapter_name=None, workspace_id="ws")
    try:
        assert "conscio.cognitive_cycle" in b._tools()
        assert "conscio.cognitive_cycle" in {d["name"] for d in b.tool_defs()}
        rep = b._tools()["conscio.cognitive_cycle"]({"world_state": "ok",
                                                     "session_tokens": 120_000})
        assert STAGES <= set(rep)
        assert rep["action"] is None            # propose-only server -> no act
        assert eng.session_tokens_used == 120_000
    finally:
        seen.close()
        eng.close()
