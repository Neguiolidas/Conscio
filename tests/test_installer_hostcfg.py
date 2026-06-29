import json
import pytest
from conscio.installer import hostcfg, spaces


@pytest.fixture(autouse=True)
def _base(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSCIO_BASE", str(tmp_path / ".conscio"))


def test_entry_has_storage_and_vault_env():
    e = hostcfg.mcp_server_entry("host-a", flags={}, model="glm-5.1")
    assert e["command"] == "conscio-mcp"
    assert "--storage" in e["args"]
    sp = str(spaces.space_dir("host-a"))
    assert sp in e["args"]
    assert e["env"]["CONSCIO_VAULT_DIR"] == str(spaces.vault_dir("host-a"))


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
    assert obj["mcpServers"]["conscio"]["command"] == "conscio-mcp"


def test_backup_pruning_keeps_30(tmp_path):
    cfgp = tmp_path / "c.json"
    cfgp.write_text("{}")

    def mut(o):
        o.setdefault("mcpServers", {})["conscio"] = {"command": "conscio-mcp"}

    verify = lambda o: "conscio" in o.get("mcpServers", {})
    for i in range(40):
        hostcfg.backup_then_write_json(cfgp, mutate=mut, verify=verify,
                                       ts=f"{i:04d}")
    baks = sorted(tmp_path.glob("c.json.bak.*"))
    assert len(baks) == 30                       # oldest pruned
    assert baks[-1].name == "c.json.bak.0039"    # newest kept
