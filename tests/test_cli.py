# tests/test_cli.py
"""The `conscio` CLI — version/info/reflect/plugins/bench. Offline; bench delegates."""
import pathlib
import subprocess
import sys

from conscio import __version__
from conscio.cli import main, _storage

ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestStorageDefault:
    """v1.5.1: default CLI storage routes through HERMES_HOME (env-overridable)
    rather than a hardcoded ~/.hermes path, matching session_lifecycle/session_rag.
    """

    def test_explicit_arg_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "ignored"))
        assert _storage(str(tmp_path / "explicit")) == str(tmp_path / "explicit")

    def test_default_honors_hermes_home_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
        assert _storage("") == str(tmp_path / "home" / "consciousness")

    def test_default_without_env_uses_dot_hermes(self, monkeypatch):
        monkeypatch.delenv("HERMES_HOME", raising=False)
        assert _storage("") == str(pathlib.Path.home() / ".hermes" / "consciousness")


def test_version(capsys):
    assert main(["version"]) == 0
    assert __version__ in capsys.readouterr().out          # bump-proof


def test_module_entrypoint_subprocess():
    """End-to-end: the real `python -m conscio` process (covers __main__.py)."""
    proc = subprocess.run([sys.executable, "-m", "conscio", "version"],
                          capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0
    assert __version__ in proc.stdout


def test_info_warns_on_unknown_model(capsys, tmp_path):
    assert main(["info", "no-such-model-xyz", "--storage", str(tmp_path)]) == 0
    assert "not a known model" in capsys.readouterr().err


def test_info_prints_model_facts(capsys, tmp_path):
    assert main(["info", "glm-5.1", "--storage", str(tmp_path)]) == 0
    out = capsys.readouterr().out.lower()
    assert "context" in out and "glm-5.1" in out


def test_reflect_prints_summary(capsys, tmp_path):
    assert main(["reflect", "all systems nominal",
                 "--storage", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip()


def test_plugins_lists(capsys):
    assert main(["plugins"]) == 0                  # empty installs -> headers, 0
    out = capsys.readouterr().out.lower()
    assert "adapter" in out and "sensor" in out and "tool" in out


def test_bench_delegates(monkeypatch):
    import conscio.bench as b
    called = {}

    def fake_main(argv):
        called["argv"] = argv
        return 0

    monkeypatch.setattr(b, "main", fake_main)
    assert main(["bench", "--adapter", "mock", "--cycles", "1"]) == 0
    assert called["argv"] == ["--adapter", "mock", "--cycles", "1"]


def test_no_subcommand_prints_help_nonzero(capsys):
    assert main([]) == 2
    assert capsys.readouterr().out.strip()         # help text on stdout


# ── v1.5: awake / sleep / daemon ────────────────────────────────────────────

def test_awake_then_sleep_toggle(capsys, tmp_path):
    assert main(["awake", "--storage", str(tmp_path)]) == 0
    assert "ON" in capsys.readouterr().out
    assert main(["sleep", "--storage", str(tmp_path)]) == 0
    assert "OFF" in capsys.readouterr().out


def test_awake_persists_to_storage(tmp_path):
    assert main(["awake", "--storage", str(tmp_path)]) == 0
    from conscio.engine import ConsciousnessEngine
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    try:
        assert eng.awake is True
    finally:
        eng.close()


def test_daemon_once_runs_a_cycle(tmp_path):
    assert main(["daemon", "--storage", str(tmp_path),
                 "--model", "test-model",
                 "--sensors", "host", "--once"]) == 0


def test_daemon_delegates(monkeypatch):
    import conscio.daemon as d
    called = {}

    def fake_main(argv):
        called["argv"] = argv
        return 0

    monkeypatch.setattr(d, "main", fake_main)
    assert main(["daemon", "--once", "--sensors", "host"]) == 0
    assert called["argv"] == ["--once", "--sensors", "host"]


def test_plugins_lists_reference_sensors(capsys):
    assert main(["plugins"]) == 0
    out = capsys.readouterr().out
    assert "HostSensor" in out and "AgentSensor" in out


# ── v1.8 `conscio structure` (read-only drift report) ────────────────────────
FIXTURE = ROOT / "tests" / "fixtures" / "graph_small.json"


def _plant_graph(workspace_root):
    import shutil
    d = pathlib.Path(workspace_root) / "graphify-out"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE, d / "graph.json")


def test_structure_no_consent_message(capsys, tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("CONSCIO_WORKSPACE", str(ws))
    store = tmp_path / "store"
    assert main(["structure", "--storage", str(store)]) == 0
    out = capsys.readouterr().out
    assert "no consented graph" in out and "off" in out
    assert not (store / "structural_drift.json").exists()       # read-only, wrote nothing


def test_structure_reports_first_sighting(capsys, tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    _plant_graph(ws)
    monkeypatch.setenv("CONSCIO_WORKSPACE", str(ws))
    store = tmp_path / "store"
    assert main(["consent", "project", "--storage", str(store)]) == 0
    capsys.readouterr()
    assert main(["structure", "--storage", str(store)]) == 0
    out = capsys.readouterr().out
    assert "nodes 101" in out and "hyperedges 24" in out
    assert "first sighting" in out
    # the report peeks but never advances the baseline
    assert not (store / "structural_drift.json").exists()
