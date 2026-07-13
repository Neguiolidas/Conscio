"""`conscio` CLI resolves its model from config.json, not only env/arg.

Same class of bug as the MCP server: with no positional/``--model`` and no
CONSCIO_MODEL, the CLI baked an empty model into argparse and fell through to
a 128k heuristic. It must consult config.json ``model`` (config > env), while
an explicit CLI model still wins.
"""
import pytest

from conscio.cli import main
import conscio.adapter_config as ac


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("CONSCIO_MODEL", raising=False)


def test_info_resolves_model_from_config(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(ac, "load_config", lambda: {"model": "glm-5.2"})
    rc = main(["info", "--storage", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "glm-5.2" in out
    assert "1000k" in out          # 1M window resolved, not the 128k heuristic


def test_explicit_cli_model_beats_config(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(ac, "load_config", lambda: {"model": "glm-5.2"})
    rc = main(["info", "kimi-k2.6", "--storage", str(tmp_path)])
    assert rc == 0
    assert "kimi-k2.6" in capsys.readouterr().out


def test_env_used_when_no_config(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(ac, "load_config", lambda: {})
    monkeypatch.setenv("CONSCIO_MODEL", "glm-5.1")
    rc = main(["info", "--storage", str(tmp_path)])
    assert rc == 0
    assert "glm-5.1" in capsys.readouterr().out
