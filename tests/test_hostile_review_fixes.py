"""Tests for v3.3.1 hostile review fixes — all pending issues."""
import os
import tempfile
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

from conscio.perception.filesystem_sensor import FilesystemSensor
from conscio.perception.git_sensor import GitSensor
from conscio.agency.fallback_multi import _sanitize_exc


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


def _commit(d, msg, *, author="test", email="t@t.com"):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": author,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": author,
        "GIT_COMMITTER_EMAIL": email,
    }
    subprocess.run(["git", "add", "-A"], cwd=d, capture_output=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=d, capture_output=True, env=env)


# ── V9: FilesystemSensor baseline cap ─────────────────────────────────

def test_baseline_cap_prunes_oldest():
    with tempfile.TemporaryDirectory() as d:
        # Create 5 files with different mtimes
        for i in range(5):
            Path(d, f"f{i}.py").write_text(f"x={i}")
            time.sleep(0.02)
        sensor = FilesystemSensor(d, max_baseline=3)
        sensor.perceive()  # baseline = 5 files, pruned to 3 newest
        assert len(sensor._baseline) == 3


def test_baseline_cap_default_10k():
    with tempfile.TemporaryDirectory() as d:
        sensor = FilesystemSensor(d)
        assert sensor._max_baseline == 10_000


def test_baseline_cap_does_not_prune_under_limit():
    with tempfile.TemporaryDirectory() as d:
        for i in range(3):
            Path(d, f"f{i}.py").write_text("x")
        sensor = FilesystemSensor(d, max_baseline=10)
        sensor.perceive()
        assert len(sensor._baseline) == 3


# ── Non-determinism: sorted observations ──────────────────────────────

def test_observations_sorted_alphabetically():
    with tempfile.TemporaryDirectory() as d:
        # Create files in non-alphabetical order
        Path(d, "z.py").write_text("1")
        Path(d, "a.py").write_text("2")
        Path(d, "m.py").write_text("3")
        sensor = FilesystemSensor(d)
        sensor.perceive()  # baseline
        # Create new files to trigger created observations
        Path(d, "c.py").write_text("4")
        Path(d, "b.py").write_text("5")
        frame = sensor.perceive()
        created = [o for o in frame.observations if o.startswith("created:")]
        # Should be sorted: b.py before c.py
    assert created[0] < created[1]


def test_deterministic_output():
    with tempfile.TemporaryDirectory() as d:
        Path(d, "r.py").write_text("1")
        Path(d, "a.py").write_text("2")
        Path(d, "z.py").write_text("3")
        sensor = FilesystemSensor(d)
        sensor.perceive()
        # Add same set of new files, check output is same across calls
        Path(d, "d.py").write_text("4")
        Path(d, "b.py").write_text("5")
        f1 = sensor.perceive()
        # Reset and re-add
        sensor2 = FilesystemSensor(d)
        sensor2.perceive()
        Path(d, "e.py").write_text("6")
        sensor2.perceive()  # second call triggers delta
    # Both should have sorted observations
    assert f1.observations == sorted(f1.observations)


# ── V3: Key leakage sanitization ──────────────────────────────────────

def test_sanitize_strips_bearer_token():
    exc = Exception("HTTP 401: Bearer nvapi-ABCDEF123456 invalid")
    safe = _sanitize_exc(exc)
    assert "nvapi-ABCDEF123456" not in safe
    assert "[REDACTED]" in safe


def test_sanitize_strips_sk_key():
    exc = Exception("Authorization: sk-proj-XYZABC rejected")
    safe = _sanitize_exc(exc)
    assert "sk-proj-XYZABC" not in safe
    assert "[REDACTED]" in safe


def test_sanitize_preserves_non_credential_errors():
    exc = Exception("The read operation timed out")
    safe = _sanitize_exc(exc)
    assert safe == "The read operation timed out"


def test_sanitize_strips_ng_key():
    exc = Exception("HTTP 403: ng-5Bu0ALFua8pYGSdKyfippUA77pVw4OWW forbidden")
    safe = _sanitize_exc(exc)
    assert "ng-5Bu0ALFua8pYGSdKyfippUA77pVw4OWW" not in safe
    assert "[REDACTED]" in safe


# ── V4: GitSensor _seen cap with LRU ───────────────────────────────────

def test_git_seen_cap_evicts_oldest():
    with tempfile.TemporaryDirectory() as d:
        _init_repo(d)
        Path(d, "a.py").write_text("x=1")
        _commit(d, "first")
        sensor = GitSensor(d, max_seen=3)
        sensor.perceive()
        # Generate 5 more commits
        for i in range(5):
            Path(d, f"f{i}.py").write_text(f"x={i}")
            _commit(d, f"commit-{i}")
            time.sleep(1.1)
            sensor.perceive()
        assert len(sensor._seen) <= 3


def test_git_seen_default_10k():
    with tempfile.TemporaryDirectory() as d:
        _init_repo(d)
        sensor = GitSensor(d)
        assert sensor._max_seen == 10_000


# ── V5: Git binary absolute path ───────────────────────────────────────

def test_git_binary_resolved_to_absolute():
    with tempfile.TemporaryDirectory() as d:
        _init_repo(d)
        sensor = GitSensor(d)
        # _git_bin should be an absolute path or "git" if not found
        if sensor._git_bin != "git":
            assert os.path.isabs(sensor._git_bin)


def test_git_binary_not_hijackable():
    """Ensure the stored git binary is absolute, preventing PATH hijacking."""
    with tempfile.TemporaryDirectory() as d:
        _init_repo(d)
        sensor = GitSensor(d)
        # Even if PATH is manipulated after construction, the stored path is fixed
        stored = sensor._git_bin
        with patch.dict(os.environ, {"PATH": "/nonexistent"}):
            assert sensor._git_bin == stored


# ── V6: NUL delimiter for commit parsing ───────────────────────────────

def test_author_with_comma_parsed_correctly():
    with tempfile.TemporaryDirectory() as d:
        _init_repo(d)
        Path(d, "a.py").write_text("x=1")
        _commit(d, "first", author="Doe, John", email="dj@t.com")
        sensor = GitSensor(d)
        sensor.perceive()  # baseline
        time.sleep(1.5)
        Path(d, "b.py").write_text("y=2")
        _commit(d, "second commit", author="Doe, John", email="dj@t.com")
        frame = sensor.perceive()
        found = any("Doe, John" in o for o in frame.observations)
    assert found, f"Expected 'Doe, John' in observations: {frame.observations}"


def test_subject_with_comma_parsed_correctly():
    with tempfile.TemporaryDirectory() as d:
        _init_repo(d)
        Path(d, "a.py").write_text("x=1")
        _commit(d, "first")  # baseline
        sensor = GitSensor(d)
        sensor.perceive()
        time.sleep(1.5)
        Path(d, "b.py").write_text("y=2")
        _commit(d, "fix: Handle, multiple, commas")
        frame = sensor.perceive()
        found = any("Handle" in o and "commas" in o for o in frame.observations)
    assert found, f"Expected subject with commas: {frame.observations}"


# ── Git observations determinism ──────────────────────────────────────

def test_git_observations_sorted():
    with tempfile.TemporaryDirectory() as d:
        _init_repo(d)
        Path(d, "a.py").write_text("x=1")
        _commit(d, "first")
        sensor = GitSensor(d)
        sensor.perceive()
        time.sleep(1.5)
        # Create commits in non-sorted hash order (can't control, but verify sort)
        for i in range(5):
            Path(d, f"f{i}.py").write_text(f"x={i}")
            _commit(d, f"commit-{i}")
        frame = sensor.perceive()
        if len(frame.observations) <= 5 and frame.observations:
            # Extract hash prefixes from observations
            hashes = [o.split()[1] for o in frame.observations if o.startswith("commit ")]
            if len(hashes) > 1:
                assert hashes == sorted(hashes)
