# tests/test_cli.py
"""The `conscio` CLI — version/info/reflect/plugins/bench. Offline; bench delegates."""
from conscio.cli import main


def test_version(capsys):
    assert main(["version"]) == 0
    assert "1.3.0" in capsys.readouterr().out


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
