"""TDD para ModeRouter — leitura de daemon_control.json + chunkifica output."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from conscio.mcp.mode_router import ModeRouter

# ── Fixtures ──

@pytest.fixture
def tmp_storage():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def council_result():
    """Resultado real do council deterministico (sem adapter)."""
    return {
        "question": "Should I deploy?",
        "voices": [
            {"role": "architect", "analysis": "Coherence: 0.85; 5 entities",
             "concerns": ["23 stale entities may indicate drift"], "vote": "hold"},
            {"role": "skeptic", "analysis": "No recurring errors; 1 pending",
             "concerns": ["Unresolved evolution proposals may conflict"], "vote": "hold"},
            {"role": "pragmatist", "analysis": "Metabolic: vital 0%",
             "concerns": [], "vote": "proceed"},
            {"role": "critic", "analysis": "No adapter — using deterministic; 5 anomalies",
             "concerns": ["5 recent anomaly(s)"], "vote": "hold"},
        ],
        "recommendation": "hold",
        "votes_summary": {"proceed": 1, "hold": 3, "veto": 0},
    }


@pytest.fixture
def llm_council_result(council_result):
    """Copia com one adicionado LLM analysis no critic."""
    result = json.loads(json.dumps(council_result))
    result["voices"][3]["analysis"] = "LLM analysis: risk of regression high; 5 anomalies"
    return result


# ── Testes de leitura do daemon_control.json ──

def test_no_daemon_control_defaults_to_compact(tmp_storage):
    """Sem daemon_control.json, modo default = compact."""
    router = ModeRouter(tmp_storage)
    assert router.complexity == "compact"


def test_reads_prompt_complexity_from_daemon_control(tmp_storage):
    """Lê prompt_complexity do daemon_control.json."""
    ctrl = tmp_storage / "daemon_control.json"
    ctrl.write_text(json.dumps({"prompt_complexity": "minimal"}))
    router = ModeRouter(tmp_storage)
    assert router.complexity == "minimal"


def test_missing_prompt_complexity_defaults_compact(tmp_storage):
    """Sem campo prompt_complexity → compact."""
    ctrl = tmp_storage / "daemon_control.json"
    ctrl.write_text(json.dumps({"model": "deepseek"}))
    router = ModeRouter(tmp_storage)
    assert router.complexity == "compact"


def test_corrupted_daemon_control_defaults_compact(tmp_storage):
    """JSON corrompido → compact."""
    ctrl = tmp_storage / "daemon_control.json"
    ctrl.write_text("not json")
    router = ModeRouter(tmp_storage)
    assert router.complexity == "compact"


# ── Council formatting ──

def test_format_council_minimal(tmp_storage, council_result):
    ctrl = tmp_storage / "daemon_control.json"
    ctrl.write_text(json.dumps({"prompt_complexity": "minimal"}))
    router = ModeRouter(tmp_storage)

    result = router.format_council(council_result)
    assert result["mode"] == "deterministic"
    assert result["recommendation"] == "hold"
    assert "votes" in result
    assert result["votes"] == {"proceed": 1, "hold": 3, "veto": 0}
    # So veredito — sem voices
    assert "voices" not in result
    assert len(json.dumps(result)) < 200


def test_format_council_compact(tmp_storage, council_result):
    ctrl = tmp_storage / "daemon_control.json"
    ctrl.write_text(json.dumps({"prompt_complexity": "compact"}))
    router = ModeRouter(tmp_storage)

    result = router.format_council(council_result)
    assert result["mode"] == "deterministic"
    assert result["recommendation"] == "hold"
    assert "voices" in result
    # compact = top concern por voz
    assert len(result["voices"]) == 4
    for v in result["voices"]:
        assert "role" in v
        assert "vote" in v
        assert "top_concern" in v
        assert "analysis" not in v  # analysis removed
    assert len(json.dumps(result)) < 800


def test_format_council_full(tmp_storage, council_result):
    ctrl = tmp_storage / "daemon_control.json"
    ctrl.write_text(json.dumps({"prompt_complexity": "full"}))
    router = ModeRouter(tmp_storage)

    result = router.format_council(council_result)
    assert result["mode"] == "deterministic"
    assert result["recommendation"] == "hold"
    assert "voices" in result
    # full = tudo preservado
    assert "analysis" in result["voices"][0]


def test_format_council_agent_host(tmp_storage, council_result):
    ctrl = tmp_storage / "daemon_control.json"
    ctrl.write_text(json.dumps({"prompt_complexity": "agent_host"}))
    router = ModeRouter(tmp_storage)

    result = router.format_council(council_result)
    assert result["mode"] == "agent_host"
    assert result["question"] == "Should I deploy?"
    assert "hint" in result
    # Sem veredito MCP
    assert "recommendation" not in result
    assert "produce 4 voices" in result["hint"].lower()


# ── LLM mode detection ──

def test_detect_llm_mode(tmp_storage, llm_council_result):
    ctrl = tmp_storage / "daemon_control.json"
    ctrl.write_text(json.dumps({"prompt_complexity": "compact"}))
    router = ModeRouter(tmp_storage)

    result = router.format_council(llm_council_result)
    assert result["mode"] == "llm"


# ── Cognitive cycle ──

def test_format_cognitive_cycle_minimal(tmp_storage):
    ctrl = tmp_storage / "daemon_control.json"
    ctrl.write_text(json.dumps({"prompt_complexity": "minimal"}))
    router = ModeRouter(tmp_storage)

    cycle_result = {
        "coherence": {"score": 0.85, "dominant": "reality"},
        "dissonance": ["stale entities: 23", "pending proposals: 1"],
        "reflection_quality": "LOW",
        "self_prompt": "why are predictions diverging?",
        "model": "glm-5.2",
    }
    result = router.format_cognitive_cycle(cycle_result)
    assert result["mode"] == "deterministic"
    assert "coherence" in result
    assert "dissonance" not in result  # minimal = so scores
    assert len(json.dumps(result)) < 300


def test_format_cognitive_cycle_compact(tmp_storage):
    ctrl = tmp_storage / "daemon_control.json"
    ctrl.write_text(json.dumps({"prompt_complexity": "compact"}))
    router = ModeRouter(tmp_storage)

    cycle_result = {
        "coherence": {"score": 0.85, "dominant": "reality"},
        "dissonance": ["stale entities: 23", "old proposals: 5"],
        "reflection_quality": "LOW",
        "self_prompt": "why divergence?",
    }
    result = router.format_cognitive_cycle(cycle_result)
    assert result["mode"] == "deterministic"
    assert "coherence" in result
    assert "top_dissonance" in result
    assert len(result.get("top_dissonance", [])) <= 3

# ── format_evaluate: the shape EvaluationReport.to_dict() actually produces ──

def _report_dict(tmp_storage):
    """A real report, not a hand-written stub — the mismatch was in the stub."""
    from conscio.engine import ConsciousnessEngine
    eng = ConsciousnessEngine(model_name="t", storage_path=str(tmp_storage))
    try:
        return eng.evaluate("some task", None).to_dict()
    finally:
        eng.close()


@pytest.mark.parametrize("complexity", ["minimal", "compact", "full"])
def test_format_evaluate_accepts_the_real_report_shape(tmp_storage, complexity):
    """conscio.evaluate returned -32603 for every minimal/compact caller.

    to_dict() gives ``overall`` as a float and the axes under ``axes``;
    format_evaluate read ``axis_scores`` and called ``.get`` on the float.
    """
    (tmp_storage / "daemon_control.json").write_text(
        json.dumps({"prompt_complexity": complexity}))
    out = ModeRouter(tmp_storage).format_evaluate(_report_dict(tmp_storage))
    assert isinstance(out, dict)
    assert isinstance(out["overall"], (int, float))
    assert out["mode"] in ("deterministic", "llm")


def test_format_evaluate_names_the_weakest_axis_in_compact_modes(tmp_storage):
    (tmp_storage / "daemon_control.json").write_text(
        json.dumps({"prompt_complexity": "minimal"}))
    report = _report_dict(tmp_storage)
    out = ModeRouter(tmp_storage).format_evaluate(report)
    axes = {a["axis"]: a["score"] for a in report["axes"]}
    assert out["weakest"] == min(axes, key=lambda k: axes[k])
    assert out["strongest"] == max(axes, key=lambda k: axes[k])
