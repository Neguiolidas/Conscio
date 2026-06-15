"""v1.5 AgentSensor — read another agent's session state (Risk.LOW).

Conscio as the consciousness-layer that *watches* a peer agent (an OpenClaw /
Claude Code worker backed by Conscio storage). It surfaces the peer's open
goals, last reflection, and last handoff as observations. It is strictly
READ-ONLY — the peer's bytes are identical before and after perceive() — and a
missing/locked/malformed source degrades to an "unavailable" frame, never a
crash.
"""
import hashlib
import json

from conscio.perception import AgentSensor, PerceptionFrame
from conscio.risk import Risk


def _digest(path):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(path.rglob("*")) if p.is_file()}


def _peer(tmp_path, name="peer"):
    d = tmp_path / name
    d.mkdir()
    (d / "state_summary.json").write_text(json.dumps({
        "active_goals": ["ship v1.5", "watch host"],
        "last_reflection": "ledger looks healthy",
        "awake": True,
    }))
    (d / "_session_handoff.md").write_text(
        "# Handoff\nfixed auth bug; deployed; next: write tests\n")
    return d


def test_perceive_surfaces_goals_reflection_handoff(tmp_path):
    frame = AgentSensor(_peer(tmp_path)).perceive()
    assert isinstance(frame, PerceptionFrame)
    assert frame.source == "agent"
    text = "\n".join(frame.observations)
    assert "ship v1.5" in text                        # open goals
    assert "ledger looks healthy" in text             # last reflection
    assert "auth bug" in text or "Handoff" in text    # handoff snippet
    assert frame.signals.get("open_goals") == 2.0


def test_risk_is_low():
    assert AgentSensor.risk is Risk.LOW


def test_read_only_does_not_mutate_peer(tmp_path):
    peer = _peer(tmp_path)
    before = _digest(peer)
    AgentSensor(peer).perceive()
    assert _digest(peer) == before                    # byte-identical, no writes


def test_missing_source_yields_unavailable_frame(tmp_path):
    frame = AgentSensor(tmp_path / "nope").perceive()
    assert frame.source == "agent"
    assert any("unavailable" in o for o in frame.observations)
    assert not (tmp_path / "nope").exists()           # never creates the path


def test_malformed_state_is_skipped_handoff_still_read(tmp_path):
    d = tmp_path / "peer"
    d.mkdir()
    (d / "state_summary.json").write_text("{not json")
    (d / "_latest_heartbeat.md").write_text("alive: cycle 42\n")
    frame = AgentSensor(d).perceive()                 # bad json ignored, no raise
    text = "\n".join(frame.observations)
    assert "cycle 42" in text
    assert "open_goals" not in frame.signals          # unreadable state -> no signal


def test_to_world_state_prefix(tmp_path):
    ws = AgentSensor(_peer(tmp_path)).perceive().to_world_state()
    assert ws.startswith("[agent]")


def test_named_peer_label(tmp_path):
    frame = AgentSensor(_peer(tmp_path), name="claude-code").perceive()
    assert any("claude-code" in o for o in frame.observations)
