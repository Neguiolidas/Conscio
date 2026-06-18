# tests/test_examples.py
"""Smoke-test the examples gallery so it cannot rot (and proves the surface is live)."""
import importlib.util
import pathlib

EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "examples"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"ex_{name}",
                                                  EXAMPLES / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_custom_adapter_runs(capsys):
    assert _load("custom_adapter").main() == 0
    assert capsys.readouterr().out.strip()


def test_host_guardian_runs(capsys, tmp_path):
    assert _load("host_guardian").main(storage=str(tmp_path)) == 0
    assert capsys.readouterr().out.strip()


def test_agent_companion_runs(capsys, tmp_path):
    assert _load("agent_companion").main(storage=str(tmp_path)) == 0
    assert capsys.readouterr().out.strip()


def test_host_consumer_runs(capsys, tmp_path):
    # v1.6 (#5/#9): the consumption-seam example — a host pulling advisory().
    assert _load("host_consumer").main(storage=str(tmp_path)) == 0
    out = capsys.readouterr().out
    assert out.strip()
    # it must demonstrate the executable/diagnostic provenance split (#7)
    assert "executable" in out.lower() and "diagnostic" in out.lower()
