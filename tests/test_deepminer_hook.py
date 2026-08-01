"""v3.9.1 capture hook — it must never break a session, whatever happens."""
import datetime as _dt
import importlib.util
import json
import subprocess
import sys
import time
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
    """A sidecar pointing at a real obsstore and a space dir that does not exist.

    Deliberately not created. A freshly materialized instance has no storage
    directory yet, and the hook fails open, so a store that cannot be created
    drops every observation in silence. Creating it here once hid exactly that:
    the whole capture slice was inert on a fresh install and 27 tests passed.
    """
    cfg = tmp_path / "hook.json"
    cfg.write_text(json.dumps({
        "obsstore": str(Path(obsstore.__file__)),
        "storage": str(tmp_path / "space"),
    }))
    return cfg


def test_capture_works_on_an_instance_whose_storage_dir_does_not_exist_yet(
        wired, tmp_path):
    """The first tool call after install must land, not vanish."""
    assert not (tmp_path / "space").exists()
    r = run_hook("post-tool-use", {
        "session_id": "FRESH", "cwd": "/w", "tool_name": "Bash",
        "tool_input": {"command": "echo hi"},
        "tool_response": {"stdout": "NEEDLEFRESH"}}, wired)
    assert r.returncode == 0
    conn = obsstore.connect(tmp_path / "space" / "obs.db")
    assert obsstore.search(conn, "NEEDLEFRESH", session_id="FRESH", full=True)
    conn.close()


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


# ── session start ──────────────────────────────────────────────────────────

def test_session_start_injects_an_index_of_the_previous_session(wired):
    run_hook("post-tool-use", _payload(session_id="PREV",
             tool_name="Bash", tool_response={"stdout": "x"}), wired)
    run_hook("post-tool-use", _payload(session_id="PREV",
             tool_name="Read", tool_response={"stdout": "y"}), wired)
    r = run_hook("session-start", {"session_id": "CURRENT"}, wired)
    assert r.returncode == 0
    assert "PREV" in r.stdout
    assert "2 tool calls" in r.stdout                  # not a stray digit
    assert "Bash x1" in r.stdout and "Read x1" in r.stdout
    assert "conscio.recall_observations" in r.stdout   # tells the agent how to get it


def test_session_start_injects_an_index_never_the_content(wired):
    """The index must name what happened, not carry it."""
    big = "SECRETPAYLOAD " + "z " * 100_000
    run_hook("post-tool-use", _payload(session_id="PREV",
             tool_response={"stdout": big}), wired)
    r = run_hook("session-start", {"session_id": "CURRENT"}, wired)
    assert "SECRETPAYLOAD" not in r.stdout
    assert len(r.stdout) < 4096


def test_session_start_on_an_empty_store_says_nothing(wired):
    r = run_hook("session-start", {"session_id": "CURRENT"}, wired)
    assert r.returncode == 0
    assert r.stdout == ""


def test_session_start_prunes(wired):
    """Retention runs here, off the hot path."""
    c = _conn(wired)
    obsstore.put_observation(c, tool="Bash", input_text="i",
                             output_text="ANCIENTROW", project="p",
                             agent="claude-code", session_id="OLD",
                             ts="2019-01-01T00:00:00")
    c.close()
    run_hook("session-start", {"session_id": "CURRENT"}, wired)
    c = _conn(wired)
    assert obsstore.search(c, "ANCIENTROW", scope="all") == []
    c.close()


def test_session_start_ignores_the_current_session_in_the_index(wired):
    run_hook("post-tool-use", _payload(session_id="CURRENT",
             tool_response={"stdout": "mine"}), wired)
    r = run_hook("session-start", {"session_id": "CURRENT"}, wired)
    assert r.stdout == ""


# ── budget (spec 4.1.4) ────────────────────────────────────────────────────

def _load_hook_module():
    spec = importlib.util.spec_from_file_location("_hook_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_capture_stays_inside_the_60ms_p95_budget(wired):
    """spec 4.1.4 — the hook runs once per tool call; it cannot be slow."""
    mod = _load_hook_module()
    cfg = json.loads(Path(wired).read_text())
    store = mod.load_obsstore(cfg["obsstore"])
    storage = Path(cfg["storage"])
    payload = _payload(tool_response={"stdout": "y " * 4000})  # ~8 KB, realistic

    mod.on_tool(payload, store, storage)          # warm the file open
    samples = []
    for _ in range(40):
        t0 = time.perf_counter()
        mod.on_tool(payload, store, storage)
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    p95 = samples[int(len(samples) * 0.95) - 1]
    assert p95 < 60.0, f"p95={p95:.1f}ms, samples={samples[-5:]}"


def test_a_one_megabyte_payload_still_fits_the_budget(wired):
    mod = _load_hook_module()
    cfg = json.loads(Path(wired).read_text())
    store = mod.load_obsstore(cfg["obsstore"])
    payload = _payload(tool_response={"stdout": "q" * 1_000_000})
    mod.on_tool(payload, store, Path(cfg["storage"]))
    t0 = time.perf_counter()
    mod.on_tool(payload, store, Path(cfg["storage"]))
    ms = (time.perf_counter() - t0) * 1000
    assert ms < 200.0, f"{ms:.0f}ms for a 1 MiB payload"


# ── compaction bracket (v3.9.2) ────────────────────────────────────────────

def test_pre_compact_steers_the_summariser_without_blocking(wired):
    r = run_hook("pre-compact", {"session_id": "C1", "cwd": "/tmp/p"}, wired)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    hs = out["hookSpecificOutput"]
    assert hs["hookEventName"] == "PreCompact"
    assert "customInstructions" in hs
    assert out.get("continue") is not False, \
        "spec 7: blocking compaction works against the goal"


def test_post_compact_stores_the_summary_and_injects_an_index(wired):
    run_hook("post-tool-use", _payload(session_id="C1",
             tool_response={"stdout": "EARLIERWORK"}), wired)
    r = run_hook("post-compact",
                 {"session_id": "C1", "cwd": "/tmp/p",
                  "summary": "We fixed the parser and shipped v2."}, wired)
    assert r.returncode == 0
    c = _conn(wired)
    got = obsstore.search(c, "parser", session_id="C1", full=True)
    assert got and "shipped v2" in got[0]["output"]
    assert got[0]["tool"] == "compact-summary"
    c.close()
    assert "recall_observations" in r.stdout


def test_post_compact_index_never_carries_the_summary_itself(wired):
    """The summary is already in context — repeating it would be pure cost."""
    summary = "UNIQUESUMMARYTOKEN " + "detail " * 5000
    r = run_hook("post-compact", {"session_id": "C1", "summary": summary}, wired)
    assert "UNIQUESUMMARYTOKEN" not in r.stdout
    assert len(r.stdout) < 4096


def test_pre_compact_records_a_boundary_marker(wired):
    run_hook("pre-compact", {"session_id": "C1", "cwd": "/tmp/p"}, wired)
    c = _conn(wired)
    assert obsstore.search(c, "compaction started", session_id="C1", full=True)
    c.close()


def test_compaction_events_still_fail_open(wired, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"obsstore": "/nope.py", "storage": str(tmp_path)}))
    for event in ("pre-compact", "post-compact"):
        r = run_hook(event, {"session_id": "C1"}, bad)
        assert r.returncode == 0, event
        assert r.stdout == "", event
