import json
import pytest
from conscio.integrations.claude_code import materialize
from conscio.installer import spaces


@pytest.fixture(autouse=True)
def _base(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSCIO_BASE", str(tmp_path / ".conscio"))


def _run(tmp_path, **kw):
    spaces.ensure_space("host-a")
    return materialize.materialize(
        "host-a", flags={"act": False}, model="glm-5.1", ts="T1",
        claude_dir=tmp_path / "claude", claude_json=tmp_path / "claude.json",
        **kw)


def test_materialize_copies_commands_skill_hook(tmp_path):
    summ = _run(tmp_path)
    cdir = tmp_path / "claude"
    assert len(list((cdir / "commands" / "conscio").glob("*.md"))) == 10
    assert (cdir / "skills" / "conscio" / "SKILL.md").is_file()
    assert (cdir / "hooks" / "conscio_awareness.py").is_file()
    assert summ["commands"] == 10 and summ["skill"] and summ["hook"]


def test_materialize_registers_mcp_with_storage_and_vault(tmp_path):
    _run(tmp_path)
    data = json.loads((tmp_path / "claude.json").read_text())
    entry = data["mcpServers"]["conscio"]
    assert entry["command"] == "conscio-mcp"
    assert str(spaces.space_dir("host-a")) in entry["args"]
    assert entry["env"]["CONSCIO_VAULT_DIR"] == str(spaces.vault_dir("host-a"))


def test_materialize_registers_sessionstart_hook(tmp_path):
    _run(tmp_path)
    settings = json.loads((tmp_path / "claude" / "settings.json").read_text())
    blob = json.dumps(settings["hooks"]["SessionStart"])
    assert "conscio_awareness.py" in blob


def test_materialize_idempotent(tmp_path):
    _run(tmp_path)
    _run(tmp_path)                                  # second run must not double
    data = json.loads((tmp_path / "claude.json").read_text())
    assert list(data["mcpServers"]).count("conscio") == 1
    settings = json.loads((tmp_path / "claude" / "settings.json").read_text())
    blob = json.dumps(settings["hooks"]["SessionStart"])
    assert blob.count("conscio_awareness.py") == 1   # not appended twice


def test_materialize_preserves_existing_mcp_and_hooks(tmp_path):
    (tmp_path / "claude.json").write_text(json.dumps(
        {"mcpServers": {"other": {"command": "x"}}}))
    (tmp_path / "claude").mkdir()
    (tmp_path / "claude" / "settings.json").write_text(json.dumps(
        {"hooks": {"SessionStart": [{"hooks": [{"type": "command",
                                                "command": "echo hi"}]}]}}))
    _run(tmp_path)
    data = json.loads((tmp_path / "claude.json").read_text())
    assert "other" in data["mcpServers"] and "conscio" in data["mcpServers"]
    settings = json.loads((tmp_path / "claude" / "settings.json").read_text())
    blob = json.dumps(settings["hooks"]["SessionStart"])
    assert "echo hi" in blob and "conscio_awareness.py" in blob


def test_materialize_recovers_from_corrupt_claude_json(tmp_path):
    (tmp_path / "claude.json").write_text("{ this is not valid json ")
    _run(tmp_path)
    # backup of the corrupt original is preserved...
    assert list(tmp_path.glob("claude.json.bak.*"))
    # ...and a fresh, valid config with the conscio entry was written
    data = json.loads((tmp_path / "claude.json").read_text())
    assert "conscio" in data["mcpServers"]
