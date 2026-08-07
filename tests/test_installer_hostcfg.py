import json
from pathlib import Path

import pytest

from conscio.installer import hostcfg, spaces


@pytest.fixture(autouse=True)
def _base(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSCIO_BASE", str(tmp_path / ".conscio"))


def _script(directory: Path, name: str = hostcfg.MCP_SCRIPT) -> Path:
    """An executable stand-in for the installed console script."""
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.write_text("#!/bin/sh\n")
    p.chmod(0o755)
    return p


def test_entry_has_storage_and_vault_env():
    e = hostcfg.mcp_server_entry("host-a", flags={}, model="glm-5.1")
    assert Path(e["command"]).name == "conscio-mcp"
    assert "--storage" in e["args"]
    sp = str(spaces.space_dir("host-a"))
    assert sp in e["args"]
    assert e["env"]["CONSCIO_VAULT_DIR"] == str(spaces.vault_dir("host-a"))


def test_command_prefers_the_script_beside_the_interpreter(monkeypatch,
                                                           tmp_path):
    """The venv case: the host is launched without the venv on its PATH, so
    the entry must carry the interpreter's own script, not PATH's."""
    venv_bin, elsewhere = tmp_path / "venv" / "bin", tmp_path / "global"
    mine = _script(venv_bin)
    _script(elsewhere)                        # an older install, first on PATH
    monkeypatch.setattr("sys.executable", str(venv_bin / "python3"))
    monkeypatch.setenv("PATH", str(elsewhere))
    assert hostcfg.mcp_command() == str(mine)


def test_command_falls_back_to_path_for_user_installs(monkeypatch, tmp_path):
    """pip install --user puts the script in ~/.local/bin, which is NOT
    beside /usr/bin/python3 — PATH is the only way to find it."""
    interpreter, local_bin = tmp_path / "usr" / "bin", tmp_path / "local" / "bin"
    interpreter.mkdir(parents=True)
    theirs = _script(local_bin)
    monkeypatch.setattr("sys.executable", str(interpreter / "python3"))
    monkeypatch.setenv("PATH", str(local_bin))
    assert hostcfg.mcp_command() == str(theirs)


def test_command_keeps_the_bare_name_when_nothing_resolves(monkeypatch,
                                                           tmp_path):
    """Never emit a broken absolute path: with no script anywhere the bare
    name is still the host's best chance (its own PATH may differ)."""
    interpreter = tmp_path / "bin"
    interpreter.mkdir()
    monkeypatch.setattr("sys.executable", str(interpreter / "python3"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert hostcfg.mcp_command() == "conscio-mcp"


def test_command_rejects_a_relative_hit(monkeypatch, tmp_path):
    """A relative PATH entry resolves against the *host's* cwd, not ours —
    strictly worse than the bare name."""
    interpreter = tmp_path / "bin"
    interpreter.mkdir()
    _script(tmp_path / "here")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.executable", str(interpreter / "python3"))
    monkeypatch.setenv("PATH", "here")
    assert hostcfg.mcp_command() == "conscio-mcp"


def test_flags_become_args():
    e = hostcfg.mcp_server_entry("h", flags={"act": True, "relay": True},
                                 model=None)
    assert "--enable-act" in e["args"] and "--enable-relay" in e["args"]
    assert "--awake" not in e["args"]


def test_write_claude_code_backs_up_and_verifies(tmp_path):
    cfgp = tmp_path / "claude.json"
    cfgp.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    hostcfg.write_claude_code("host-a", flags={}, model="glm-5.1",
                              config_path=cfgp, ts="T1")
    data = json.loads(cfgp.read_text())
    assert "conscio" in data["mcpServers"]
    assert data["mcpServers"]["other"] == {"command": "x"}   # preserved
    assert (tmp_path / "claude.json.bak.T1").exists()        # backup made


def test_write_creates_file_when_absent(tmp_path):
    cfgp = tmp_path / "new.json"
    hostcfg.write_claude_code("h", flags={}, model=None,
                              config_path=cfgp, ts="T2")
    assert "conscio" in json.loads(cfgp.read_text())["mcpServers"]
    assert not (tmp_path / "new.json.bak.T2").exists()       # nothing to back up


def test_readback_failure_raises(tmp_path):
    cfgp = tmp_path / "c.json"
    with pytest.raises(hostcfg.HostConfigError):
        hostcfg.backup_then_write_json(
            cfgp, mutate=lambda o: None,
            verify=lambda o: "conscio" in o.get("mcpServers", {}), ts="T3")


def test_generic_snippet_is_valid_json():
    s = hostcfg.generic_snippet("h", flags={"act": True}, model="m")
    obj = json.loads(s)
    assert Path(obj["mcpServers"]["conscio"]["command"]).name == "conscio-mcp"


def test_hermes_flag_maps_to_enable_hermes_review():
    e = hostcfg.mcp_server_entry("h", flags={"hermes": True}, model=None)
    assert "--enable-hermes-review" in e["args"]


def test_initiate_flag_never_reaches_mcp_args():
    # conscio-mcp has no --initiate flag; emitting it would break server boot
    e = hostcfg.mcp_server_entry("h", flags={"initiate": True}, model=None)
    assert "--initiate" not in e["args"]


def test_rewrite_preserves_existing_entry_env(tmp_path):
    cfgp = tmp_path / "claude.json"
    cfgp.write_text(json.dumps({"mcpServers": {"conscio": {
        "command": "conscio-mcp", "args": [],
        "env": {"MY_EXTRA": "kept", "CONSCIO_VAULT_DIR": "/old"}}}}))
    hostcfg.write_claude_code("host-a", flags={}, model=None,
                              config_path=cfgp, ts="T4")
    env = json.loads(cfgp.read_text())["mcpServers"]["conscio"]["env"]
    assert env["MY_EXTRA"] == "kept"                     # user env survives
    assert env["CONSCIO_VAULT_DIR"] != "/old"            # ours wins


def test_rewrite_re_resolves_a_stale_command(monkeypatch, tmp_path):
    """`--repair` is the documented cure for a moved venv, and it only works
    because the command is rebuilt, never carried over like `env` is."""
    venv_bin = tmp_path / "new-venv" / "bin"
    mine = _script(venv_bin)
    monkeypatch.setattr("sys.executable", str(venv_bin / "python3"))
    cfgp = tmp_path / "claude.json"
    cfgp.write_text(json.dumps({"mcpServers": {"conscio": {
        "command": "/gone/old-venv/bin/conscio-mcp", "args": []}}}))
    hostcfg.write_claude_code("host-a", flags={}, model=None,
                              config_path=cfgp, ts="T5")
    assert json.loads(cfgp.read_text())["mcpServers"]["conscio"][
        "command"] == str(mine)


def test_existing_flags_roundtrip(tmp_path):
    cfgp = tmp_path / "claude.json"
    hostcfg.write_claude_code(
        "host-a", flags={"act": True, "hermes": True, "relay": False},
        model=None, config_path=cfgp, ts="T5")
    got = hostcfg.existing_flags(cfgp)
    assert got.get("act") and got.get("hermes")
    assert not got.get("relay")


def test_existing_flags_missing_file_empty(tmp_path):
    assert hostcfg.existing_flags(tmp_path / "nope.json") == {}


def test_backup_same_ts_twice_keeps_both(tmp_path):
    cfgp = tmp_path / "c.json"
    cfgp.write_text(json.dumps({"mcpServers": {"keep": {"command": "x"}}}))

    def mut(o):
        o.setdefault("mcpServers", {})["conscio"] = {"command": "conscio-mcp"}

    def verify(o):
        return "conscio" in o.get("mcpServers", {})

    hostcfg.backup_then_write_json(cfgp, mutate=mut, verify=verify, ts="SAME")
    hostcfg.backup_then_write_json(cfgp, mutate=mut, verify=verify, ts="SAME")
    assert len(list(tmp_path.glob("c.json.bak.SAME*"))) == 2   # not overwritten


def test_backup_pruning_keeps_30(tmp_path):
    cfgp = tmp_path / "c.json"
    cfgp.write_text("{}")

    def mut(o):
        o.setdefault("mcpServers", {})["conscio"] = {"command": "conscio-mcp"}

    def verify(o):
        return "conscio" in o.get("mcpServers", {})

    for i in range(40):
        hostcfg.backup_then_write_json(cfgp, mutate=mut, verify=verify,
                                       ts=f"{i:04d}")
    baks = sorted(tmp_path.glob("c.json.bak.*"))
    assert len(baks) == 30                       # oldest pruned
    assert baks[-1].name == "c.json.bak.0039"    # newest kept


def test_existing_flags_tolerates_non_dict_servers(tmp_path):
    cfgp = tmp_path / "c.json"
    cfgp.write_text(json.dumps({"mcpServers": ["corrupt"]}))
    assert hostcfg.existing_flags(cfgp) == {}            # never raises


def test_upsert_replaces_non_dict_servers():
    o = {"mcpServers": ["corrupt"]}
    hostcfg.upsert_conscio_entry(o, "h", flags={}, model=None)
    assert Path(o["mcpServers"]["conscio"]["command"]).name == "conscio-mcp"


def test_existing_flags_recovers_legacy_initiate(tmp_path):
    # pre-Reach installers emitted --initiate into the MCP args (a daemon
    # flag); repair must recover the consent even though the flag itself must
    # never re-enter the rewritten entry
    cfgp = tmp_path / "c.json"
    cfgp.write_text(json.dumps({"mcpServers": {"conscio": {
        "command": "conscio-mcp",
        "args": ["--storage", "/old", "--initiate"], "env": {}}}}))
    assert hostcfg.existing_flags(cfgp).get("initiate") is True


def test_existing_model_roundtrip(tmp_path):
    cfgp = tmp_path / "c.json"
    hostcfg.write_claude_code("h", flags={}, model="glm-5.1",
                              config_path=cfgp, ts="T7")
    assert hostcfg.existing_model(cfgp) == "glm-5.1"


def test_existing_model_absent_or_dangling(tmp_path):
    assert hostcfg.existing_model(tmp_path / "nope.json") is None
    cfgp = tmp_path / "c.json"
    cfgp.write_text(json.dumps({"mcpServers": {"conscio": {
        "args": ["--model"]}}}))                     # dangling value
    assert hostcfg.existing_model(cfgp) is None


def test_existing_slug_from_storage_arg(tmp_path):
    cfgp = tmp_path / "c.json"
    hostcfg.write_claude_code("old-space", flags={}, model=None,
                              config_path=cfgp, ts="T8")
    assert hostcfg.existing_slug(cfgp) == "old-space"
    assert hostcfg.existing_slug(tmp_path / "nope.json") is None
