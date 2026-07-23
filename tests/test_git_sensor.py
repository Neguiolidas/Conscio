import os
import time
import tempfile
import subprocess
from pathlib import Path

from conscio.perception.git_sensor import GitSensor


_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "t@t.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "t@t.com",
}


def _init_repo(d):
    subprocess.run(["git", "init"], cwd=d, capture_output=True, env=_GIT_ENV)
    subprocess.run(["git", "config", "user.name", "test"], cwd=d, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=d, capture_output=True)


def _commit(d, msg):
    subprocess.run(["git", "add", "-A"], cwd=d, capture_output=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=d, capture_output=True, env=_GIT_ENV)


def test_no_commits_returns_empty():
    with tempfile.TemporaryDirectory() as d:
        _init_repo(d)
        sensor = GitSensor(d)
        sensor.perceive()  # baseline
        frame = sensor.perceive()
        assert frame.source == "git"
        assert frame.observations == []


def test_detects_new_commit():
    with tempfile.TemporaryDirectory() as d:
        _init_repo(d)
        Path(d, "a.py").write_text("x=1")
        _commit(d, "first")
        sensor = GitSensor(d)
        sensor.perceive()  # baseline
        time.sleep(1.5)
        Path(d, "b.py").write_text("y=2")
        _commit(d, "second")
        frame = sensor.perceive()
        assert any("second" in obs for obs in frame.observations)


def test_idempotent_seen_hashes():
    with tempfile.TemporaryDirectory() as d:
        _init_repo(d)
        Path(d, "a.py").write_text("x=1")
        _commit(d, "first")
        sensor = GitSensor(d)
        sensor.perceive()  # baseline
        time.sleep(1.5)
        Path(d, "b.py").write_text("y=2")
        _commit(d, "second")
        f1 = sensor.perceive()
        f2 = sensor.perceive()
        assert any("second" in o for o in f1.observations)
        assert f2.observations == []


def test_non_git_dir_returns_empty():
    with tempfile.TemporaryDirectory() as d:
        sensor = GitSensor(d)
        frame = sensor.perceive()
        assert frame.observations == []


def test_git_not_installed_returns_empty():
    with tempfile.TemporaryDirectory() as d:
        _init_repo(d)
        sensor = GitSensor(d, timeout=1.0)
        sensor._git_bin = "git-nonexistent-binary-xyz"
        frame = sensor.perceive()
        assert frame.observations == []


def test_many_commits_summarized():
    with tempfile.TemporaryDirectory() as d:
        _init_repo(d)
        Path(d, "a.py").write_text("x=1")
        _commit(d, "first")
        sensor = GitSensor(d)
        sensor.perceive()
        time.sleep(1.5)
        for i in range(10):
            Path(d, f"f{i}.py").write_text(f"x={i}")
            _commit(d, f"commit-{i}")
        frame = sensor.perceive()
        assert len(frame.observations) <= 3
        assert any("10" in o for o in frame.observations)


def test_signals_populated():
    with tempfile.TemporaryDirectory() as d:
        _init_repo(d)
        Path(d, "a.py").write_text("x=1")
        _commit(d, "first")
        sensor = GitSensor(d)
        sensor.perceive()
        time.sleep(1.5)
        Path(d, "b.py").write_text("y=2")
        _commit(d, "second")
        frame = sensor.perceive()
        assert "commits_new" in frame.signals
        assert frame.signals["commits_new"] >= 1


def test_timeout_returns_empty():
    with tempfile.TemporaryDirectory() as d:
        _init_repo(d)
        sensor = GitSensor(d, timeout=0.001)  # impossibly fast
        sensor.perceive()
        frame = sensor.perceive()
        assert frame.observations == []
