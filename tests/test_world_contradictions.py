# tests/test_world_contradictions.py
from conscio.world_model import WorldModel, STATE_LOG_MAX


class FakeDetector:
    """Flags owns/sold relations and operational/offline states (no embedder)."""
    def relations_contradict(self, a, b):
        return {a, b} == {"owns", "sold"}
    def states_contradict(self, a, b):
        return {a, b} == {"operational", "offline"}


def test_list_relations_returns_copies(tmp_path):
    w = WorldModel(tmp_path)
    w.add_entity("a", "system")
    w.add_entity("b", "system")
    w.add_relation("a", "owns", "b")
    rels = w.list_relations()
    rels[0]["relation"] = "MUTATED"
    assert w.list_relations()[0]["relation"] == "owns"  # store untouched


def test_entity_count(tmp_path):
    w = WorldModel(tmp_path)
    assert w.entity_count() == 0
    w.add_entity("a", "system")
    assert w.entity_count() == 1


def test_state_log_appends_on_change(tmp_path):
    w = WorldModel(tmp_path)
    w.add_entity("svc", "system", state="operational")
    w.update_state("svc", "offline")
    log = [e["state"] for e in w.get_entity("svc")["state_log"]]
    assert log == ["operational", "offline"]


def test_state_log_dedups_consecutive_identical(tmp_path):
    w = WorldModel(tmp_path)
    w.add_entity("svc", "system", state="operational")
    w.update_state("svc", "operational")  # no change → no new entry
    assert len(w.get_entity("svc")["state_log"]) == 1


def test_state_log_capped(tmp_path):
    w = WorldModel(tmp_path)
    w.add_entity("svc", "system", state="s0")
    for i in range(1, STATE_LOG_MAX + 3):
        w.update_state("svc", f"s{i}")
    assert len(w.get_entity("svc")["state_log"]) == STATE_LOG_MAX


def test_mark_contradictions_flags_relation_pairs(tmp_path):
    w = WorldModel(tmp_path)
    w.add_entity("gw", "system")
    w.add_entity("svc", "system")
    w.add_relation("gw", "owns", "svc")
    w.add_relation("gw", "sold", "svc")
    flagged = w.mark_contradictions(FakeDetector())
    assert "gw" in flagged
    assert w.contradicted_entities() == ["gw"]


def test_mark_contradictions_flags_state_log(tmp_path):
    w = WorldModel(tmp_path)
    w.add_entity("svc", "system", state="operational")
    w.update_state("svc", "offline")
    flagged = w.mark_contradictions(FakeDetector())
    assert "svc" in flagged


def test_mark_dry_run_does_not_write(tmp_path):
    w = WorldModel(tmp_path)
    w.add_entity("gw", "system")
    w.add_entity("svc", "system")
    w.add_relation("gw", "owns", "svc")
    w.add_relation("gw", "sold", "svc")
    flagged = w.mark_contradictions(FakeDetector(), dry_run=True)
    assert "gw" in flagged                       # would-flag reported
    assert w.contradicted_entities() == []       # but nothing persisted


def test_legacy_entity_without_state_log_is_safe(tmp_path):
    w = WorldModel(tmp_path)
    w.add_entity("e", "system")
    # Simulate a pre-v0.8 entity: strip the state_log key.
    w._data["entities"]["e"].pop("state_log", None)
    assert w.mark_contradictions(FakeDetector()) == []
    assert w.contradicted_entities() == []


def test_mark_clears_stale_flag_on_rerun(tmp_path):
    # Writing `contradicted` onto EVERY entity (not just flagged) clears a prior
    # flag once the contradiction is gone.
    w = WorldModel(tmp_path)
    w.add_entity("gw", "system")
    w.add_entity("svc", "system")
    w.add_relation("gw", "owns", "svc")
    w.add_relation("gw", "sold", "svc")
    w.mark_contradictions(FakeDetector())
    assert w.contradicted_entities() == ["gw"]
    # Resolve: drop the contradicting relations, re-run → flag cleared.
    w._data["relations"] = [r for r in w._data["relations"] if r["relation"] != "sold"]
    w.mark_contradictions(FakeDetector())
    assert w.contradicted_entities() == []


def test_readd_clears_cached_contradicted_flag(tmp_path):
    # Docstring contract: re-perceiving an entity drops its stale cached verdict.
    w = WorldModel(tmp_path)
    w.add_entity("gw", "system")
    w._data["entities"]["gw"]["contradicted"] = True
    w.add_entity("gw", "system")  # re-add
    assert w.get_entity("gw").get("contradicted") is None
    assert w.contradicted_entities() == []


def test_orphan_from_relation_not_flagged(tmp_path):
    # A relation whose `from` is not a modeled entity is skipped, so the returned
    # set always equals the cached set (no return/cache divergence).
    w = WorldModel(tmp_path)
    w.add_entity("svc", "system")
    w.add_relation("ghost", "owns", "svc")
    w.add_relation("ghost", "sold", "svc")
    flagged = w.mark_contradictions(FakeDetector())
    assert flagged == []
    assert w.contradicted_entities() == []


def test_state_log_contradiction_ages_out(tmp_path):
    # Bounded-window self-resolution: once the opposed state rolls past
    # STATE_LOG_MAX, the entity stops being flagged.
    w = WorldModel(tmp_path)
    w.add_entity("svc", "system", state="operational")
    w.update_state("svc", "offline")
    assert "svc" in w.mark_contradictions(FakeDetector())
    # Push the opposed pair out of the bounded window with neutral states.
    for i in range(STATE_LOG_MAX + 1):
        w.update_state("svc", f"neutral{i}")
    assert w.mark_contradictions(FakeDetector()) == []
