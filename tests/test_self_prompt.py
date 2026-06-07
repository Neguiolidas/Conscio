# tests/test_self_prompt.py
from conscio.self_prompt import generate_self_prompts, SelfPrompt, _SIGNAL_DRIVE
from conscio.coherence import CoherenceReport, Dissonance


class _Meta:
    def __init__(self, blind=None):
        self._data = {"blind_spots": blind or []}


class _World:
    def __init__(self, stale=None):
        self._stale = stale or []

    def stale_entities(self):
        return self._stale


def _report(dissonances):
    dims = {"epistemic": 1.0, "reality": 1.0, "ontological": 1.0, "temporal": 1.0}
    dominant = dissonances[0] if dissonances else None
    return CoherenceReport(0.5, dims, list(dissonances), dominant)


def test_empty_state_no_prompts():
    assert generate_self_prompts(_Meta(), _World(), _report([])) == []


def test_dissonance_becomes_prompt():
    d = Dissonance("ontological", 0.2, 0.8, "x")
    out = generate_self_prompts(_Meta(), _World(), _report([d]))
    assert len(out) == 1
    p = out[0]
    assert isinstance(p, SelfPrompt)
    assert p.source_signal == "ontological" and p.drive == "curiosity"
    assert "contradictory" in p.question


def test_signal_drive_mapping():
    assert _SIGNAL_DRIVE["epistemic"] == "evolution"
    assert _SIGNAL_DRIVE["temporal"] == "maintenance"
    assert _SIGNAL_DRIVE["ontological"] == "curiosity"


def test_severity_ranking_desc():
    d1 = Dissonance("temporal", 0.4, 0.6, "x")
    d2 = Dissonance("ontological", 0.1, 0.9, "x")
    out = generate_self_prompts(_Meta(), _World(), _report([d1, d2]))
    assert [p.source_signal for p in out] == ["ontological", "temporal"]


def test_blind_spot_and_stale_sources():
    out = generate_self_prompts(_Meta(blind=["trading"]), _World(stale=["botX"]), _report([]))
    sigs = {p.source_signal for p in out}
    assert "blind_spot" in sigs and "stale" in sigs
    bs = next(p for p in out if p.source_signal == "blind_spot")
    assert bs.drive == "evolution"


def test_deterministic_tiebreak_on_equal_severity():
    # blind_spot (order 4) before stale (order 5) when severities tie
    out = generate_self_prompts(_Meta(blind=["a"]), _World(stale=["b"]), _report([]))
    assert [p.source_signal for p in out] == ["blind_spot", "stale"]
