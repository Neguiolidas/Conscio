import json
import subprocess
from unittest.mock import MagicMock, patch

from conscio.integrations.neurata import NeurataBridge


def _mock_bridge(mock_proc):
    """Helper: create bridge with mocked subprocess."""
    with patch("shutil.which", return_value="/usr/bin/neurata"), \
         patch("subprocess.run", return_value=mock_proc):
        return NeurataBridge()


def test_not_available_when_binary_missing():
    with patch("shutil.which", return_value=None):
        b = NeurataBridge()
    assert b.available is False
    assert b.query("test") is None
    assert b.deposit("body") is None


def test_query_returns_parsed_json():
    mock_proc = MagicMock()
    mock_proc.stdout = json.dumps({
        "contract_version": 1, "ok": True,
        "results": [{"title": "skill-x", "content": "snippet"}]
    })
    mock_proc.returncode = 0
    with patch("shutil.which", return_value="/usr/bin/neurata"), \
         patch("subprocess.run", return_value=mock_proc):
        b = NeurataBridge()
        result = b.query("firebase")
    assert result is not None
    assert result["ok"] is True


def test_query_caches_by_context():
    mock_proc = MagicMock()
    mock_proc.stdout = json.dumps({"contract_version": 1, "ok": True})
    mock_proc.returncode = 0
    with patch("shutil.which", return_value="/usr/bin/neurata"), \
         patch("subprocess.run", return_value=mock_proc) as mock_run:
        b = NeurataBridge()
        count_after_probe = mock_run.call_count
        b.query("x", context_hash="ctx1")
        b.query("x", context_hash="ctx1")
        assert mock_run.call_count == count_after_probe + 1


def test_cache_miss_different_context():
    mock_proc = MagicMock()
    mock_proc.stdout = json.dumps({"contract_version": 1, "ok": True})
    mock_proc.returncode = 0
    with patch("shutil.which", return_value="/usr/bin/neurata"), \
         patch("subprocess.run", return_value=mock_proc) as mock_run:
        b = NeurataBridge()
        count_after_probe = mock_run.call_count
        b.query("x", context_hash="ctx1")
        b.query("x", context_hash="ctx2")
        assert mock_run.call_count == count_after_probe + 2


def test_invalid_json_returns_none():
    mock_proc = MagicMock()
    mock_proc.stdout = "not json at all"
    mock_proc.returncode = 0
    with patch("shutil.which", return_value="/usr/bin/neurata"), \
         patch("subprocess.run", return_value=mock_proc):
        b = NeurataBridge()
        r = b.query("test")
    assert r is None


def test_timeout_returns_none():
    with patch("shutil.which", return_value="/usr/bin/neurata"), \
         patch("subprocess.run",
               side_effect=subprocess.TimeoutExpired("cmd", 5)):
        b = NeurataBridge()
    assert b.available is False
    assert b.query("test") is None


def test_deposit_called():
    mock_proc = MagicMock()
    mock_proc.stdout = json.dumps({"contract_version": 1, "ok": True})
    mock_proc.returncode = 0
    with patch("shutil.which", return_value="/usr/bin/neurata"), \
         patch("subprocess.run", return_value=mock_proc):
        b = NeurataBridge()
        r = b.deposit("content here", type="skill")
    assert r is not None
    assert r["ok"] is True


def test_shelf_insights():
    mock_proc = MagicMock()
    mock_proc.stdout = json.dumps({
        "contract_version": 1, "ok": True, "insights": []
    })
    mock_proc.returncode = 0
    with patch("shutil.which", return_value="/usr/bin/neurata"), \
         patch("subprocess.run", return_value=mock_proc):
        b = NeurataBridge()
        r = b.shelf_insights()
    assert r is not None
    assert r["ok"] is True


def test_nonzero_returncode_returns_none():
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = ""
    with patch("shutil.which", return_value="/usr/bin/neurata"), \
         patch("subprocess.run", return_value=mock_proc):
        b = NeurataBridge()
        r = b.query("test")
    assert r is None
