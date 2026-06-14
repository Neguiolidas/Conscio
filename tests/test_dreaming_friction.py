# tests/test_dreaming_friction.py
from datetime import datetime, timedelta

from conscio.engine import ConsciousnessEngine
from conscio.dreaming import DreamCycle


def _engine(tmp_path):
    e = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    e._session_rag = ConsciousnessEngine._RAG_DISABLED
    return e


def _index_old_reflection(e, content, days_old=10):
    sid = e.content_store.index(label="r", content=content, category="reflection")
    old_ts = (datetime.utcnow() - timedelta(days=days_old)).isoformat()
    e.content_store.db.execute(
        "UPDATE sources SET indexed_at=? WHERE id=?", (old_ts, sid)
    )
    e.content_store.db.commit()
    return sid


def _mark_recent_entity(e, name):
    e.world._data["entities"][name] = {
        "type": "system", "attributes": {}, "state": "ok",
        "last_updated": datetime.now().isoformat(), "relevance": 1.0,
    }
    e.world._save()


def test_dream_prune_uses_entropy(tmp_path):
    e = _engine(tmp_path)
    ts = (datetime.now() - timedelta(days=30)).isoformat()
    e.world._data["entities"]["junk"] = {
        "type": "system", "attributes": {}, "state": "ok",
        "last_updated": ts, "relevance": 0.02,
    }
    e.world._data["entities"]["hub"] = {
        "type": "system", "attributes": {}, "state": "ok",
        "last_updated": ts, "relevance": 0.9,
    }
    for i in range(8):
        o = f"o{i}"
        e.world._data["entities"][o] = {
            "type": "system", "attributes": {}, "state": "ok",
            "last_updated": ts, "relevance": 0.9,
        }
        e.world._data["relations"].append({"from": "hub", "relation": "links", "to": o})
    e.world._save()

    report = e.dream()
    assert "junk" in report.entities_pruned
    assert "hub" not in report.entities_pruned          # connectivity rescued it
    assert report.reflections_deferred == 0             # new field exists, default 0
    e.close()


def test_friction_defers_changed_entity(tmp_path):
    e = _engine(tmp_path)
    cyc = DreamCycle(crystallize_after_days=0, crystallize_min_count=1)
    sid = _index_old_reflection(e, "Saturn API timeout observed")
    _mark_recent_entity(e, "Saturn")
    consolidated, deferred = cyc._crystallize(e, pruned=[], dry_run=False)
    assert deferred == 1
    assert consolidated == 0
    assert e.content_store.get_source(sid) is not None   # survived, not deleted
    e.close()


def test_friction_allows_unrelated_reflection(tmp_path):
    e = _engine(tmp_path)
    cyc = DreamCycle(crystallize_after_days=0, crystallize_min_count=1)
    sid = _index_old_reflection(e, "Jupiter weather is calm")
    _mark_recent_entity(e, "Saturn")
    consolidated, deferred = cyc._crystallize(e, pruned=[], dry_run=False)
    assert deferred == 0
    assert consolidated == 1
    assert e.content_store.get_source(sid) is None        # crystallized + deleted
    e.close()


def test_friction_short_name_does_not_over_defer(tmp_path):
    e = _engine(tmp_path)
    cyc = DreamCycle(crystallize_after_days=0, crystallize_min_count=1)
    _index_old_reflection(e, "capture the flag at noon")
    _mark_recent_entity(e, "a")      # too short -> ignored
    _mark_recent_entity(e, "cap")    # substring of 'capture' -> word-boundary blocks
    consolidated, deferred = cyc._crystallize(e, pruned=[], dry_run=False)
    assert deferred == 0
    assert consolidated == 1
    e.close()


def test_friction_whole_word_match_defers(tmp_path):
    e = _engine(tmp_path)
    cyc = DreamCycle(crystallize_after_days=0, crystallize_min_count=1)
    _index_old_reflection(e, "API timeout on node alpha")
    _mark_recent_entity(e, "API")    # whole word in text -> defer
    consolidated, deferred = cyc._crystallize(e, pruned=[], dry_run=False)
    assert deferred == 1
    assert consolidated == 0
    e.close()
