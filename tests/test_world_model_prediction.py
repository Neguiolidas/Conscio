# tests/test_world_model_prediction.py
from datetime import datetime, timedelta

from conscio.world_model import WorldModel


def test_record_prediction_returns_surprise(tmp_path):
    wm = WorldModel(tmp_path)
    assert wm.record_prediction("btc", "up", "down") is True
    assert wm.record_prediction("btc", "up", "up") is False


def test_error_rate_window(tmp_path):
    wm = WorldModel(tmp_path)
    wm.record_prediction("a", "x", "y")   # error
    wm.record_prediction("b", "x", "x")   # ok
    assert wm.recent_prediction_error_rate(24) == 0.5


def test_error_rate_empty_is_zero(tmp_path):
    wm = WorldModel(tmp_path)
    assert wm.recent_prediction_error_rate(24) == 0.0


def test_prediction_log_retention_drops_ancient(tmp_path):
    wm = WorldModel(tmp_path)
    old_ts = (datetime.now() - timedelta(hours=200)).isoformat()
    wm._data["prediction_log"] = [
        {"entity": "old", "expected": "a", "actual": "b", "error": 1, "ts": old_ts}
    ]
    wm.record_prediction("new", "a", "a")
    log = wm._data["prediction_log"]
    assert all(e["entity"] != "old" for e in log)   # >7d dropped
    assert any(e["entity"] == "new" for e in log)


def test_prediction_log_hard_cap(tmp_path):
    wm = WorldModel(tmp_path)
    now = datetime.now().isoformat()
    wm._data["prediction_log"] = [
        {"entity": f"e{i}", "expected": "a", "actual": "b", "error": 1, "ts": now}
        for i in range(600)
    ]
    wm.record_prediction("last", "a", "a")
    assert len(wm._data["prediction_log"]) == 500
    assert wm._data["prediction_log"][-1]["entity"] == "last"
