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
