"""v3.9.1 capture hook — it must never break a session, whatever happens."""
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
