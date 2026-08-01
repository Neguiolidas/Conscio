"""v3.9.1 capture hook — it must never break a session, whatever happens."""
import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conscio import obsstore

HOOK = (Path(__file__).parent.parent / "conscio" / "integrations" /
        "claude_code" / "assets" / "hooks" / "conscio_deepminer.py")


def run_hook(event, payload, cfg_path=None, timeout=30):
    """Invoke the hook exactly as Claude Code does: argv event, JSON on stdin."""
    argv = [sys.executable, str(HOOK), event]
    if cfg_path:
        argv += ["--config", str(cfg_path)]
    return subprocess.run(argv, input=json.dumps(payload), capture_output=True,
                          text=True, timeout=timeout)


@pytest.fixture()
def wired(tmp_path):
    """A sidecar pointing at a real obsstore and a real space dir."""
    cfg = tmp_path / "hook.json"
    cfg.write_text(json.dumps({
        "obsstore": str(Path(obsstore.__file__)),
        "storage": str(tmp_path / "space"),
    }))
    (tmp_path / "space").mkdir()
    return cfg


def test_unknown_event_exits_clean_and_silent(wired):
    r = run_hook("no-such-event", {"session_id": "S"}, wired)
    assert r.returncode == 0
    assert r.stdout == ""


def test_malformed_stdin_never_fails(wired):
    r = subprocess.run([sys.executable, str(HOOK), "post-tool-use",
                        "--config", str(wired)],
                       input="not json at all{{{", capture_output=True, text=True)
    assert r.returncode == 0
    assert r.stdout == ""


def test_empty_stdin_never_fails(wired):
    r = subprocess.run([sys.executable, str(HOOK), "post-tool-use",
                        "--config", str(wired)],
                       input="", capture_output=True, text=True)
    assert r.returncode == 0


def test_missing_config_never_fails(tmp_path):
    r = run_hook("post-tool-use", {"session_id": "S", "tool_name": "Bash"},
                 tmp_path / "absent.json")
    assert r.returncode == 0
    assert r.stdout == ""


def test_unreadable_obsstore_path_never_fails(tmp_path):
    cfg = tmp_path / "hook.json"
    cfg.write_text(json.dumps({"obsstore": "/nonexistent/obsstore.py",
                               "storage": str(tmp_path)}))
    r = run_hook("post-tool-use", {"session_id": "S", "tool_name": "Bash"}, cfg)
    assert r.returncode == 0
    assert r.stdout == ""


def test_no_argv_event_at_all_never_fails(wired):
    r = subprocess.run([sys.executable, str(HOOK)], input="{}",
                       capture_output=True, text=True)
    assert r.returncode == 0
    assert r.stdout == ""


# ── capture ────────────────────────────────────────────────────────────────

def _payload(**kw):
    base = {"session_id": "HOOK-S1", "cwd": "/tmp/proj",
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
            "tool_response": {"stdout": "on branch main", "stderr": ""}}
    base.update(kw)
    return base


def _conn(cfg):
    storage = Path(json.loads(Path(cfg).read_text())["storage"])
    return obsstore.connect(storage / "obs.db")


def test_post_tool_use_records_the_call(wired):
    assert run_hook("post-tool-use", _payload(), wired).returncode == 0
    c = _conn(wired)
    got = obsstore.search(c, "on branch main", session_id="HOOK-S1", full=True)
    assert len(got) == 1
    assert got[0]["tool"] == "Bash"
    assert "git status" in got[0]["input"]
    assert "on branch main" in got[0]["output"]
    c.close()


def test_capture_writes_nothing_to_stdout(wired):
    """PostToolUse must not substitute output — silence is the contract."""
    r = run_hook("post-tool-use", _payload(), wired)
    assert r.stdout == ""


def test_large_output_is_stored_whole(wired):
    big = "HEAD " + "x " * 200_000 + " TAILTOKEN"
    run_hook("post-tool-use", _payload(tool_response={"stdout": big}), wired)
    c = _conn(wired)
    got = obsstore.search(c, "TAILTOKEN", session_id="HOOK-S1", full=True)
    assert got and len(got[0]["output"]) > 100_000
    c.close()


def test_project_comes_from_cwd(wired):
    run_hook("post-tool-use", _payload(cwd="/tmp/alpha"), wired)
    c = _conn(wired)
    got = obsstore.search(c, "on branch main", scope="project",
                          project="/tmp/alpha", full=True)
    assert len(got) == 1
    c.close()


def test_failure_event_is_recorded_and_flagged(wired):
    run_hook("post-tool-use-failure",
             _payload(tool_response={"error": "command not found: frobnicate"}),
             wired)
    c = _conn(wired)
    got = obsstore.search(c, "frobnicate", session_id="HOOK-S1", full=True)
    assert len(got) == 1
    assert got[0]["tool"] == "Bash!failed"
    c.close()


def test_sessions_do_not_bleed_into_each_other(wired):
    run_hook("post-tool-use", _payload(session_id="AAA",
             tool_response={"stdout": "ALPHAONLY"}), wired)
    run_hook("post-tool-use", _payload(session_id="BBB",
             tool_response={"stdout": "BETAONLY"}), wired)
    c = _conn(wired)
    assert len(obsstore.search(c, "ALPHAONLY", session_id="BBB")) == 0
    assert len(obsstore.search(c, "ALPHAONLY", session_id="AAA")) == 1
    c.close()


def test_missing_session_id_is_still_recorded_under_a_placeholder(wired):
    p = _payload()
    del p["session_id"]
    assert run_hook("post-tool-use", p, wired).returncode == 0
    c = _conn(wired)
    assert len(obsstore.search(c, "on branch main", scope="all")) == 1
    c.close()


def test_timestamps_are_naive_utc_like_the_engine(wired):
    """Two writers, one ts column — a local-time hook would break ordering."""
    before = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    run_hook("post-tool-use", _payload(), wired)
    c = _conn(wired)
    ts = c.execute("SELECT ts FROM observations").fetchone()[0]
    c.close()
    after = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    got = _dt.datetime.fromisoformat(ts)
    assert before.replace(microsecond=0) <= got <= after + _dt.timedelta(seconds=1), (
        f"{ts} is not naive UTC — local time would drift by the UTC offset")


def test_hook_never_imports_the_conscio_package(wired):
    """The reason obsstore is loaded by path: the package costs ~0.28s."""
    r = subprocess.run(
        [sys.executable, "-X", "importtime", str(HOOK), "post-tool-use",
         "--config", str(wired)],
        input=json.dumps(_payload()), capture_output=True, text=True)
    assert r.returncode == 0
    assert "conscio/__init__" not in r.stderr
    assert "sentence_transformers" not in r.stderr
