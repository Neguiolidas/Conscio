# tests/test_world_model_entropy.py
from datetime import datetime, timedelta

import pytest

from conscio.world_model import WorldModel


def _put(wm, name, *, days_old=0.0, relevance=1.0, relations=0):
    """Insert an entity directly with controlled age/relevance/connectivity."""
    ts = (datetime.now() - timedelta(days=days_old)).isoformat()
    wm._data["entities"][name] = {
        "type": "system", "attributes": {}, "state": "ok",
        "last_updated": ts, "relevance": relevance,
    }
    for i in range(relations):
        other = f"{name}_rel_{i}"
        wm._data["entities"].setdefault(other, {
            "type": "system", "attributes": {}, "state": "ok",
            "last_updated": ts, "relevance": relevance,
        })
        wm._data["relations"].append({"from": name, "relation": "links", "to": other})


def test_entropy_unknown_is_max(tmp_path):
    wm = WorldModel(tmp_path)
    assert wm.entropy("does-not-exist") == 1.0


def test_entropy_bounded(tmp_path):
    wm = WorldModel(tmp_path)
    _put(wm, "x", days_old=1, relevance=1.0, relations=0)
    assert 0.0 <= wm.entropy("x") <= 1.0


def test_entropy_monotonic_in_age(tmp_path):
    wm = WorldModel(tmp_path)
    _put(wm, "young", days_old=0.1, relevance=0.5, relations=0)
    _put(wm, "old", days_old=30, relevance=0.5, relations=0)
    assert wm.entropy("old") > wm.entropy("young")


def test_entropy_connectivity_lowers_score(tmp_path):
    wm = WorldModel(tmp_path)
    _put(wm, "iso", days_old=10, relevance=0.5, relations=0)
    _put(wm, "hub", days_old=10, relevance=0.5, relations=8)
    assert wm.entropy("hub") < wm.entropy("iso")


def test_entropy_low_relevance_raises_score(tmp_path):
    wm = WorldModel(tmp_path)
    _put(wm, "hi", days_old=5, relevance=0.9, relations=2)
    _put(wm, "lo", days_old=5, relevance=0.05, relations=2)
    assert wm.entropy("lo") > wm.entropy("hi")


def test_entropy_bad_timestamp_treated_old(tmp_path):
    wm = WorldModel(tmp_path)
    wm._data["entities"]["bad"] = {
        "type": "system", "attributes": {}, "state": "ok",
        "last_updated": "not-a-date", "relevance": 1.0,
    }
    # age_norm=1.0, isolation=1.0, rel_gap=0.0 -> 0.4 + 0.3 + 0 = 0.7
    assert wm.entropy("bad") == pytest.approx(0.7)
