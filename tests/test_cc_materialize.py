import json
from pathlib import Path

import pytest

from conscio.installer import spaces
from conscio.integrations.claude_code import materialize


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
    # Count comes from the shipped assets, not a literal: adding a command
    # should not require editing this test, but losing one must still fail it.
    src = Path(materialize.__file__).parent / "assets" / "commands"
    expected = len(list(src.glob("*.md")))
    assert expected >= 14, "assets/commands lost files"

    summ = _run(tmp_path)
    cdir = tmp_path / "claude"
    assert len(list((cdir / "commands" / "conscio").glob("*.md"))) == expected
    assert (cdir / "skills" / "conscio" / "SKILL.md").is_file()
    assert (cdir / "hooks" / "conscio_awareness.py").is_file()
    assert summ["commands"] == expected and summ["skill"] and summ["hook"]


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


def test_materialize_installs_the_capture_hook_and_its_sidecar(tmp_path):
    _run(tmp_path)
    hooks = tmp_path / "claude" / "hooks"
    assert (hooks / "conscio_deepminer.py").is_file()
    cfg = json.loads((hooks / "conscio_deepminer.json").read_text())
    assert cfg["obsstore"].endswith("obsstore.py")
    assert Path(cfg["obsstore"]).is_file()
    assert str(spaces.space_dir("host-a")) == cfg["storage"]


class TestObsstoreIsVendored:
    """The sidecar used to point at obsstore.py inside the install tree. That
    path dangles on a pipx upgrade, an editable switch, or a venv rebuild — and
    the hook fails open, so it stops recording in silence. A copy beside the
    hook cannot be invalidated by anything that touches the install."""

    def test_the_copy_lives_beside_the_hook(self, tmp_path):
        _run(tmp_path)
        hooks = tmp_path / "claude" / "hooks"
        cfg = json.loads((hooks / "conscio_deepminer.json").read_text())
        assert Path(cfg["obsstore"]).parent == hooks
        assert (hooks / "conscio_obsstore.py").is_file()

    def test_the_copy_is_the_packaged_module(self, tmp_path):
        _run(tmp_path)
        vendored = (tmp_path / "claude" / "hooks" / "conscio_obsstore.py")
        assert (vendored.read_bytes()
                == materialize.obsstore_src().read_bytes())

    def test_the_copy_loads_standalone(self, tmp_path):
        """The hook loads it by absolute path, never as part of the package."""
        import importlib.util
        _run(tmp_path)
        path = tmp_path / "claude" / "hooks" / "conscio_obsstore.py"
        spec = importlib.util.spec_from_file_location("_vendored_obs", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert callable(mod.connect) and callable(mod.put_observation)

    def test_reinstall_refreshes_a_stale_copy(self, tmp_path):
        _run(tmp_path)
        vendored = tmp_path / "claude" / "hooks" / "conscio_obsstore.py"
        vendored.write_text("# stale build\n", encoding="utf-8")
        _run(tmp_path)
        assert vendored.read_bytes() == materialize.obsstore_src().read_bytes()


class TestReadBinding:
    """`govern status` asks the sidecar where observations land, because the
    CLI's own storage is a different space and reporting its obs.db describes a
    database nothing writes to."""

    def test_reports_the_bound_space_and_a_healthy_hook(self, tmp_path):
        _run(tmp_path)
        b = materialize.read_binding(tmp_path / "claude")
        assert b is not None
        assert b["storage"] == spaces.space_dir("host-a")
        assert b["ok"] is True
        assert b["version"]

    def test_a_dangling_obsstore_is_not_ok(self, tmp_path):
        """The one failure the hook cannot report itself: it fails open, so a
        missing obsstore looks exactly like a session with no tool calls."""
        _run(tmp_path)
        (tmp_path / "claude" / "hooks" / "conscio_obsstore.py").unlink()
        assert materialize.read_binding(tmp_path / "claude")["ok"] is False

    def test_no_bundle_installed_is_none_not_an_error(self, tmp_path):
        assert materialize.read_binding(tmp_path / "nothing-here") is None

    def test_a_corrupt_sidecar_is_none_not_an_error(self, tmp_path):
        _run(tmp_path)
        sidecar = tmp_path / "claude" / "hooks" / "conscio_deepminer.json"
        sidecar.write_text("{not json", encoding="utf-8")
        assert materialize.read_binding(tmp_path / "claude") is None


def test_materialize_registers_all_three_capture_events(tmp_path):
    _run(tmp_path)
    settings = json.loads((tmp_path / "claude" / "settings.json").read_text())
    for event, arg in (("SessionStart", "session-start"),
                       ("PostToolUse", "post-tool-use"),
                       ("PostToolUseFailure", "post-tool-use-failure")):
        blob = json.dumps(settings["hooks"][event])
        assert "conscio_deepminer.py" in blob, event
        assert arg in blob, event


def test_awareness_hook_survives_alongside_the_capture_hook(tmp_path):
    _run(tmp_path)
    settings = json.loads((tmp_path / "claude" / "settings.json").read_text())
    blob = json.dumps(settings["hooks"]["SessionStart"])
    assert "conscio_awareness.py" in blob and "conscio_deepminer.py" in blob


def test_registration_is_idempotent(tmp_path):
    _run(tmp_path)
    _run(tmp_path)
    settings = json.loads((tmp_path / "claude" / "settings.json").read_text())
    blob = json.dumps(settings["hooks"]["PostToolUse"])
    assert blob.count("conscio_deepminer.py") == 1


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


def test_hook_command_survives_spaces_in_path(tmp_path):
    import shlex

    spaces.ensure_space("host-a")
    cdir = tmp_path / "my claude"                        # path with a space
    materialize.materialize(
        "host-a", flags={}, model=None, ts="T1",
        claude_dir=cdir, claude_json=tmp_path / "claude.json")
    settings = json.loads((cdir / "settings.json").read_text())

    def command_for(event, script):
        """Find a hook by script name — never by list position, which shifts
        as soon as another hook registers for the same event."""
        for group in settings["hooks"][event]:
            for h in group["hooks"]:
                if script in h["command"]:
                    return h["command"]
        raise AssertionError(f"{script} not registered for {event}")

    parts = shlex.split(command_for("SessionStart", "conscio_awareness.py"))
    assert parts[0] == "python3"
    assert parts[1].endswith("conscio_awareness.py")     # ONE arg, not split

    # the capture hook carries an extra argv token, so a split path would put
    # the event name in the wrong position and it would dispatch to nothing
    parts = shlex.split(command_for("PostToolUse", "conscio_deepminer.py"))
    assert parts[0] == "python3"
    assert parts[1].endswith("conscio_deepminer.py")
    assert parts[2] == "post-tool-use"
    assert len(parts) == 3


def test_copy_tree_is_recursive(tmp_path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "top.md").write_text("t")
    (src / "sub" / "deep.md").write_text("d")
    n = materialize._copy_tree(src, tmp_path / "dst")
    assert n == 2
    assert (tmp_path / "dst" / "sub" / "deep.md").read_text() == "d"


def test_materialize_recovers_from_corrupt_claude_json(tmp_path):
    (tmp_path / "claude.json").write_text("{ this is not valid json ")
    _run(tmp_path)
    # backup of the corrupt original is preserved...
    assert list(tmp_path.glob("claude.json.bak.*"))
    # ...and a fresh, valid config with the conscio entry was written
    data = json.loads((tmp_path / "claude.json").read_text())
    assert "conscio" in data["mcpServers"]


def test_copy_tree_skips_pycache(tmp_path):
    src = tmp_path / "src"
    (src / "__pycache__").mkdir(parents=True)
    (src / "__pycache__" / "junk.pyc").write_text("x")
    (src / "real.md").write_text("r")
    n = materialize._copy_tree(src, tmp_path / "dst")
    assert n == 1
    assert not (tmp_path / "dst" / "__pycache__").exists()


def test_materialize_registers_the_compaction_bracket(tmp_path):
    _run(tmp_path)
    settings = json.loads((tmp_path / "claude" / "settings.json").read_text())
    for event, arg in (("PreCompact", "pre-compact"),
                       ("PostCompact", "post-compact")):
        blob = json.dumps(settings["hooks"][event])
        assert "conscio_deepminer.py" in blob and arg in blob, event
