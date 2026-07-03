import pytest
from conscio import cli as top_cli
from conscio.installer import wizard


@pytest.fixture(autouse=True)
def _base(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSCIO_BASE", str(tmp_path / ".conscio"))
    monkeypatch.delenv("CONSCIO_VAULT_DIR", raising=False)


class ScriptIO:
    def __init__(self, answers, confirms):
        self._a = list(answers)
        self._c = list(confirms)
        self.out = []

    def ask(self, prompt, default=""):
        return self._a.pop(0) if self._a else default

    def confirm(self, prompt, default=False):
        return self._c.pop(0) if self._c else default

    def echo(self, msg):
        self.out.append(str(msg))


def test_top_cli_routes_init(monkeypatch):
    called = {}
    import conscio.installer.cli as icli

    def _fake(argv):
        called["argv"] = argv
        return 0

    monkeypatch.setattr(icli, "main", _fake)
    assert top_cli.main(["init", "--repair"]) == 0
    assert called["argv"] == ["--repair"]


def test_generic_host_writes_space_and_prints_snippet(tmp_path):
    io = ScriptIO(answers=["antigravity-test"],          # label
                  confirms=[False, False, False, False,  # act/hermes/relay/initiate
                            False, False])                # graphify / awake
    rc = wizard.run_with(io, host="antigravity", repair=False,
                         model="glm-5.1", ts="T1")
    assert rc == 0
    from conscio.installer import spaces
    assert (spaces.space_dir("antigravity-test") / "instance.json").exists()
    assert any("mcpServers" in o for o in io.out)        # snippet printed


def test_claude_code_host_materializes(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CLAUDE_JSON", str(tmp_path / "claude.json"))
    io = ScriptIO(answers=["cc"], confirms=[False] * 6)
    assert wizard.run_with(io, host="claude-code", repair=False,
                           model="glm-5.1", ts="T9") == 0
    import json
    data = json.loads((tmp_path / "claude.json").read_text())
    assert "conscio" in data["mcpServers"]


def test_label_cli_flag_names_the_space(tmp_path, monkeypatch):
    # non-interactive: --label must win; stdin EOF must not hang or rename
    import conscio.installer.cli as icli
    rc = icli.main(["--host", "antigravity", "--label", "My Lab"])
    assert rc == 0
    from conscio.installer import spaces
    assert (spaces.space_dir("my-lab") / "instance.json").exists()


def test_default_label_is_host_not_duplicated(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CLAUDE_JSON", str(tmp_path / "claude.json"))
    io = ScriptIO(answers=[], confirms=[False] * 6)      # take every default
    assert wizard.run_with(io, host="claude-code", repair=False,
                           model=None, ts="T2") == 0
    from conscio.installer import spaces
    assert (spaces.space_dir("claude-code") / "instance.json").exists()
    assert not spaces.space_dir("claude-code-claude-code").exists()


def test_hermes_consent_reaches_launch_config(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CLAUDE_JSON", str(tmp_path / "claude.json"))
    io = ScriptIO(answers=["cc"],
                  confirms=[False, True, False, False,   # act/hermes/relay/init
                            False, False])               # graphify / awake
    assert wizard.run_with(io, host="claude-code", repair=False,
                           model=None, ts="T3") == 0
    args = json.loads((tmp_path / "claude.json").read_text()
                      )["mcpServers"]["conscio"]["args"]
    assert "--enable-hermes-review" in args


def test_repair_preserves_granted_flags(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CLAUDE_JSON", str(tmp_path / "claude.json"))
    (tmp_path / "claude.json").write_text(json.dumps({"mcpServers": {
        "conscio": {"command": "conscio-mcp",
                    "args": ["--storage", "/old", "--enable-act",
                             "--enable-relay"],
                    "env": {}}}}))
    io = ScriptIO(answers=["cc"], confirms=[])
    assert wizard.run_with(io, host="claude-code", repair=True,
                           model=None, ts="T4") == 0
    args = json.loads((tmp_path / "claude.json").read_text()
                      )["mcpServers"]["conscio"]["args"]
    assert "--enable-act" in args and "--enable-relay" in args


def test_initiate_consent_goes_to_daemon_not_mcp(tmp_path, monkeypatch):
    import json
    from conscio.installer import daemonctl
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CLAUDE_JSON", str(tmp_path / "claude.json"))
    seen = {}
    monkeypatch.setattr(daemonctl, "start",
                        lambda slug, extra_args: seen.update(a=extra_args) or 99)
    io = ScriptIO(answers=["cc"],
                  confirms=[False, False, False, True,   # act/hermes/relay/init
                            False, True])                # graphify / awake=YES
    assert wizard.run_with(io, host="claude-code", repair=False,
                           model=None, ts="T5") == 0
    assert "--initiate" in seen["a"]                     # daemon gets it
    args = json.loads((tmp_path / "claude.json").read_text()
                      )["mcpServers"]["conscio"]["args"]
    assert "--initiate" not in args                      # conscio-mcp must not


def test_daemon_start_failure_is_reported_not_fatal(tmp_path, monkeypatch):
    from conscio.installer import daemonctl

    def boom(slug, extra_args):
        raise daemonctl.DaemonStartError("no conscio on PATH")

    monkeypatch.setattr(daemonctl, "start", boom)
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CLAUDE_JSON", str(tmp_path / "claude.json"))
    io = ScriptIO(answers=["cc"],
                  confirms=[False, False, False, False, False, True])  # awake
    assert wizard.run_with(io, host="claude-code", repair=False,
                           model=None, ts="T6") == 0     # wizard survives
    assert any("FAILED" in o for o in io.out)            # and says so


def test_repair_rebinds_existing_space_not_a_new_one(tmp_path, monkeypatch):
    # pre-Reach default labels produced doubled slugs; repair must rebind the
    # space the config already points at, never mint a fresh empty mind
    import json
    from conscio.installer import spaces
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CLAUDE_JSON", str(tmp_path / "claude.json"))
    sp, _, _ = spaces.ensure_space("claude-code-claude-code")
    (tmp_path / "claude.json").write_text(json.dumps({"mcpServers": {
        "conscio": {"command": "conscio-mcp",
                    "args": ["--storage", str(sp)], "env": {}}}}))
    io = ScriptIO(answers=[], confirms=[])
    assert wizard.run_with(io, host="claude-code", repair=True,
                           model=None, ts="T7") == 0
    args = json.loads((tmp_path / "claude.json").read_text()
                      )["mcpServers"]["conscio"]["args"]
    assert str(sp) in args                               # same space rebound
    assert not spaces.space_dir("claude-code").exists()  # no new space minted


def test_repair_preserves_model(tmp_path, monkeypatch):
    import json
    from conscio.installer import spaces
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CLAUDE_JSON", str(tmp_path / "claude.json"))
    sp, _, _ = spaces.ensure_space("cc")
    (tmp_path / "claude.json").write_text(json.dumps({"mcpServers": {
        "conscio": {"command": "conscio-mcp",
                    "args": ["--storage", str(sp), "--model", "glm-5.1"],
                    "env": {}}}}))
    io = ScriptIO(answers=[], confirms=[])
    assert wizard.run_with(io, host="claude-code", repair=True,
                           model=None, ts="T8") == 0
    args = json.loads((tmp_path / "claude.json").read_text()
                      )["mcpServers"]["conscio"]["args"]
    assert "--model" in args and "glm-5.1" in args       # model not stripped


def test_repair_recovers_legacy_initiate_consent(tmp_path, monkeypatch):
    import json
    from conscio.installer import spaces
    monkeypatch.setenv("CLAUDE_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CLAUDE_JSON", str(tmp_path / "claude.json"))
    sp, _, _ = spaces.ensure_space("cc")
    (tmp_path / "claude.json").write_text(json.dumps({"mcpServers": {
        "conscio": {"command": "conscio-mcp",
                    "args": ["--storage", str(sp), "--initiate"],
                    "env": {}}}}))
    io = ScriptIO(answers=[], confirms=[])
    assert wizard.run_with(io, host="claude-code", repair=True,
                           model=None, ts="T9") == 0
    args = json.loads((tmp_path / "claude.json").read_text()
                      )["mcpServers"]["conscio"]["args"]
    assert "--initiate" not in args                      # never in MCP args
    assert any("--initiate" in o for o in io.out)        # told how to re-arm
