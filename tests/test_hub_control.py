# tests/test_hub_control.py
import json

from conscio.hub import control


def test_write_control_returns_and_persists(tmp_path):
    out = control.write_control(tmp_path, True)
    assert out["awake"] is True and isinstance(out["ts"], float)
    on_disk = json.loads((tmp_path / control.CONTROL_FILENAME).read_text())
    assert on_disk["awake"] is True


def test_write_control_overwrites(tmp_path):
    control.write_control(tmp_path, True)
    control.write_control(tmp_path, False)
    assert control.read_control(tmp_path)["awake"] is False


def test_read_control_missing_returns_empty(tmp_path):
    assert control.read_control(tmp_path) == {}


def test_read_control_corrupt_returns_empty(tmp_path):
    (tmp_path / control.CONTROL_FILENAME).write_text("{not json")
    assert control.read_control(tmp_path) == {}


def test_write_control_atomic_no_tmp_left(tmp_path):
    control.write_control(tmp_path, True)
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
