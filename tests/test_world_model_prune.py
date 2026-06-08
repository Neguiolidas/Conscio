"""Tests for WorldModel.prune_stale — destructive purge of stale entities."""
from datetime import datetime, timedelta

import pytest

from conscio.world_model import WorldModel


@pytest.fixture
def wm(tmp_path):
    return WorldModel(tmp_path)


def _age_entity(wm, name, hours_old, relevance):
    """Force an entity's age and relevance directly in the data store."""
    old_ts = (datetime.now() - timedelta(hours=hours_old)).isoformat()
    wm._data["entities"][name]["last_updated"] = old_ts
    wm._data["entities"][name]["relevance"] = relevance
    wm._save()


def test_prune_removes_low_relevance(wm):
    wm.add_entity("fresh", "system", state="ok")
    wm.add_entity("faded", "system", state="old")
    _age_entity(wm, "faded", hours_old=1, relevance=0.05)  # below default 0.2
    removed = wm.prune_stale()
    assert "faded" in removed
    assert wm.get_entity("faded") is None
    assert wm.get_entity("fresh") is not None


def test_prune_removes_aged_out(wm):
    wm.add_entity("ancient", "system", state="x")
    # 10 days old but decent relevance — exceeds default max_age_hours=168 (7d)
    _age_entity(wm, "ancient", hours_old=240, relevance=0.9)
    removed = wm.prune_stale()
    assert "ancient" in removed
    assert wm.get_entity("ancient") is None


def test_prune_removes_relations_of_pruned_entity(wm):
    wm.add_entity("a", "system")
    wm.add_entity("b", "system")
    wm.add_relation("a", "links", "b")
    _age_entity(wm, "a", hours_old=1, relevance=0.0)
    wm.prune_stale()
    # relation referencing 'a' must be gone
    assert all(r["from"] != "a" and r["to"] != "a" for r in wm._data["relations"])


def test_prune_dry_run_changes_nothing(wm):
    wm.add_entity("doomed", "system")
    _age_entity(wm, "doomed", hours_old=1, relevance=0.0)
    would = wm.prune_stale(dry_run=True)
    assert "doomed" in would
    assert wm.get_entity("doomed") is not None  # not actually removed


def test_prune_persists_across_reload(wm, tmp_path):
    wm.add_entity("keep", "system", state="ok")
    wm.add_entity("drop", "system")
    _age_entity(wm, "drop", hours_old=1, relevance=0.0)
    wm.prune_stale()
    reloaded = WorldModel(tmp_path)
    assert reloaded.get_entity("drop") is None
    assert reloaded.get_entity("keep") is not None


def test_prune_keeps_fresh_relevant_entities(wm):
    wm.add_entity("active", "system", state="ok")  # fresh, relevance ~1.0
    removed = wm.prune_stale()
    assert removed == []


def test_prune_dry_run_matches_real_prune_after_decay(wm):
    # High stored relevance but old enough that exp-decay drops it below 0.2.
    # dry_run must predict the SAME removal a real run performs.
    wm.add_entity("decaying", "system", state="x")
    _age_entity(wm, "decaying", hours_old=60, relevance=1.0)  # decays to ~0.05
    preview = wm.prune_stale(dry_run=True)
    assert "decaying" in preview                    # dry_run predicts removal
    assert wm.get_entity("decaying") is not None    # but did NOT remove
    removed = wm.prune_stale(dry_run=False)
    assert "decaying" in removed                     # real run removes it
    assert wm.get_entity("decaying") is None
    assert set(preview) == set(removed)              # preview matched reality


def test_list_entities_top_n_by_relevance(tmp_path):
    from conscio.world_model import WorldModel
    wm = WorldModel(tmp_path)
    wm.add_entity("alpha", "concept", state="idle")
    wm.add_entity("beta", "concept", state="active")
    wm.add_entity("gamma", "concept", state="idle")
    wm._data["entities"]["alpha"]["relevance"] = 0.2
    wm._data["entities"]["beta"]["relevance"] = 0.9
    wm._data["entities"]["gamma"]["relevance"] = 0.5

    top = wm.list_entities(limit=2)
    assert [e["name"] for e in top] == ["beta", "gamma"]
    assert top[0]["state"] == "active"          # carries the full entity dict


def test_list_entities_empty_world(tmp_path):
    from conscio.world_model import WorldModel
    wm = WorldModel(tmp_path)
    assert wm.list_entities() == []
