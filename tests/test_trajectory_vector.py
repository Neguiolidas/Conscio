# tests/test_trajectory_vector.py
from types import SimpleNamespace
from conscio.session_lifecycle import (
    SessionSummary, enrich_with_conscio, format_heartbeat, format_handoff,
    HB_MAX_CHARS,
)
from conscio.shard_engine import Shard


class _Goal:
    def __init__(self, desc):
        self.description = desc


def _fake_engine(shard=None, goals=("ship recall layering",), entities=None):
    world = SimpleNamespace(
        list_entities=lambda limit=5: list(entities or []),
        stale_entities=lambda: [],
    )
    goals_mod = SimpleNamespace(active_goals=lambda: [_Goal(g) for g in goals])
    meta = SimpleNamespace(average_confidence=lambda: 0.5)
    shard_engine = SimpleNamespace(current=shard)
    return SimpleNamespace(world=world, goals=goals_mod, meta=meta, shard_engine=shard_engine)


def test_summary_has_soft_fields_with_defaults():
    s = SessionSummary()
    assert s.trajectory == ""
    assert s.vibes == ""
    assert s.identity_anchor == ""


def test_enrich_derives_trajectory_from_shard_and_goal():
    s = SessionSummary()
    enrich_with_conscio(s, _fake_engine(shard=Shard.ENGINEER))
    assert s.trajectory == "ENGINEER → ship recall layering"


def test_enrich_overwrites_existing_trajectory():
    s = SessionSummary(trajectory="stale direction")
    enrich_with_conscio(s, _fake_engine(shard=Shard.JANITOR, goals=("clean up",)))
    assert s.trajectory == "JANITOR → clean up"


def test_enrich_leaves_llm_only_fields_untouched():
    s = SessionSummary(vibes="frustrated but progressing", identity_anchor="methodical debugger")
    enrich_with_conscio(s, _fake_engine(shard=Shard.ENGINEER))
    assert s.vibes == "frustrated but progressing"
    assert s.identity_anchor == "methodical debugger"


def test_enrich_trajectory_goal_only_when_no_shard():
    s = SessionSummary()
    enrich_with_conscio(s, _fake_engine(shard=None, goals=("explore options",)))
    assert s.trajectory == "explore options"


def test_enrich_uses_list_entities():
    s = SessionSummary()
    ents = [{"name": "WorldModel", "state": "stable"}]
    enrich_with_conscio(s, _fake_engine(shard=Shard.ENGINEER, entities=ents))
    assert "WorldModel:stable" in s.world_model_entities


def test_heartbeat_includes_trajectory_within_budget():
    s = SessionSummary(session_id="abc", model="glm", title="t",
                       trajectory="ENGINEER → ship recall layering",
                       vibes="calm", identity_anchor="careful")
    hb = format_heartbeat(s)
    assert "ENGINEER → ship recall layering" in hb
    assert "calm" in hb
    assert len(hb) <= HB_MAX_CHARS


def test_heartbeat_omits_empty_soft_fields():
    s = SessionSummary(session_id="abc", model="glm", title="t")
    hb = format_heartbeat(s)
    assert "Trajetória" not in hb
    assert "Vibe" not in hb


def test_handoff_includes_soft_fields():
    s = SessionSummary(session_id="abc", model="glm", active_goals=["g"],
                       trajectory="ENGINEER → x", vibes="steady", identity_anchor="builder")
    doc = format_handoff(s)
    assert "ENGINEER → x" in doc
    assert "steady" in doc
    assert "builder" in doc
