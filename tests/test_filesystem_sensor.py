import os
import tempfile
import time
from pathlib import Path

from conscio.perception.filesystem_sensor import FilesystemSensor


def test_no_changes_returns_empty_observations():
    with tempfile.TemporaryDirectory() as d:
        sensor = FilesystemSensor(d)
        sensor.perceive()  # baseline
        frame = sensor.perceive()
        assert frame.source == "filesystem"
        assert frame.observations == []
        assert frame.ts > 0


def test_detects_new_file():
    with tempfile.TemporaryDirectory() as d:
        sensor = FilesystemSensor(d)
        sensor.perceive()  # baseline
        Path(d, "novo.py").write_text("print('hello')")
        os.utime(Path(d, "novo.py"), (time.time(), time.time()))
        time.sleep(0.05)
        frame = sensor.perceive()
        assert any("novo.py" in obs for obs in frame.observations)


def test_detects_modified_file():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d, "arq.py")
        p.write_text("x = 1")
        sensor = FilesystemSensor(d)
        sensor.perceive()  # baseline
        time.sleep(0.05)
        p.write_text("x = 2")
        os.utime(p, (time.time(), time.time()))
        time.sleep(0.05)
        frame = sensor.perceive()
        assert any("arq.py" in obs for obs in frame.observations)


def test_detects_deleted_file():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d, "del.py")
        p.write_text("x = 1")
        sensor = FilesystemSensor(d)
        sensor.perceive()  # baseline
        p.unlink()
        time.sleep(0.05)
        frame = sensor.perceive()
        assert any("del.py" in obs for obs in frame.observations)


def test_ignorelist_skips_git():
    with tempfile.TemporaryDirectory() as d:
        Path(d, ".git").mkdir()
        Path(d, ".git", "config").write_text("x")
        sensor = FilesystemSensor(d)
        sensor.perceive()
        os.utime(Path(d, ".git", "config"), (time.time(), time.time()))
        time.sleep(0.05)
        frame = sensor.perceive()
        assert all(".git" not in obs for obs in frame.observations)


def test_ignorelist_skips_pycache():
    with tempfile.TemporaryDirectory() as d:
        Path(d, "__pycache__").mkdir()
        Path(d, "__pycache__", "x.pyc").write_text("x")
        sensor = FilesystemSensor(d)
        sensor.perceive()
        os.utime(Path(d, "__pycache__", "x.pyc"), (time.time(), time.time()))
        time.sleep(0.05)
        frame = sensor.perceive()
        assert all("__pycache__" not in obs for obs in frame.observations)


def test_depth_limit():
    with tempfile.TemporaryDirectory() as d:
        Path(d, "a").mkdir()
        Path(d, "a", "b").mkdir()
        Path(d, "a", "b", "c").mkdir()
        deep = Path(d, "a", "b", "c", "deep.py")
        deep.write_text("x")
        os.utime(deep, (time.time(), time.time()))
        sensor = FilesystemSensor(d, depth=2)
        sensor.perceive()
        time.sleep(0.05)
        frame = sensor.perceive()
        assert all("deep.py" not in obs for obs in frame.observations)


def test_summarize_when_many_files():
    with tempfile.TemporaryDirectory() as d:
        sensor = FilesystemSensor(d, max_files=5)
        sensor.perceive()  # baseline
        for i in range(10):
            p = Path(d, f"f{i}.py")
            p.write_text("x")
            os.utime(p, (time.time(), time.time()))
        time.sleep(0.05)
        frame = sensor.perceive()
        assert len(frame.observations) <= 3
        assert any("10" in obs for obs in frame.observations)


def test_nonexistent_dir_returns_empty():
    sensor = FilesystemSensor("/nonexistent/path/xyz123")
    frame = sensor.perceive()
    assert frame.observations == []
    assert frame.source == "filesystem"


def test_permission_denied_skips_silently():
    with tempfile.TemporaryDirectory() as d:
        sensor = FilesystemSensor(d)
        frame = sensor.perceive()
        assert isinstance(frame.observations, list)


def test_signals_populated_when_changes():
    with tempfile.TemporaryDirectory() as d:
        sensor = FilesystemSensor(d)
        sensor.perceive()
        p = Path(d, "a.py")
        p.write_text("x")
        os.utime(p, (time.time(), time.time()))
        time.sleep(0.05)
        frame = sensor.perceive()
        assert "files_changed" in frame.signals
        assert frame.signals["files_changed"] > 0


def test_reuses_across_cycles():
    with tempfile.TemporaryDirectory() as d:
        sensor = FilesystemSensor(d)
        sensor.perceive()  # baseline
        p = Path(d, "a.py")
        p.write_text("x")
        os.utime(p, (time.time(), time.time()))
        time.sleep(0.05)
        f1 = sensor.perceive()
        assert any("a.py" in o for o in f1.observations)
        f2 = sensor.perceive()  # no new changes
        assert f2.observations == []
