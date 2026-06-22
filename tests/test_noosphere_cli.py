# tests/test_noosphere_cli.py
import sqlite3
from conscio.noosphere import cli, quarantine


def _fp(text):
    from conscio.agency.fingerprint import goal_fingerprint
    return goal_fingerprint(text)


def _seed(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE skills (id INTEGER PRIMARY KEY, goal_fp TEXT,"
        " goal_text TEXT, tool_seq TEXT, plan_template TEXT,"
        " successes INT, failures INT);")
    conn.execute(
        "INSERT INTO skills (goal_fp, goal_text, tool_seq, plan_template,"
        " successes, failures) VALUES (?,?,?,?,?,?)",
        (_fp("deploy"), "deploy", '["a"]',
         '[{"tool":"a","args":{},"rationale":"r"}]', 3, 0))
    conn.commit()
    conn.close()


def test_publish_then_import_via_cli(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _seed(a / "conscio.db")
    noo = str(tmp_path / "noosphere.db")
    assert cli.main(["publish", "--storage", str(a), "--noosphere", noo]) == 0
    assert cli.main(["import", "--storage", str(b), "--noosphere", noo]) == 0
    rows = quarantine.list_rows(b / "noosphere_quarantine.db")
    assert len(rows) == 1 and rows[0].import_status == "quarantined"


def test_id_set_label(tmp_path):
    assert cli.main(["id", "--storage", str(tmp_path), "--set-label", "prod"]) == 0
    from conscio.noosphere import identity
    assert identity.load_or_create(tmp_path).label == "prod"


def test_list_and_show(tmp_path, capsys):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _seed(a / "conscio.db")
    noo = str(tmp_path / "noosphere.db")
    cli.main(["publish", "--storage", str(a), "--noosphere", noo])
    cli.main(["import", "--storage", str(b), "--noosphere", noo])
    rid = quarantine.list_rows(b / "noosphere_quarantine.db")[0].id
    assert cli.main(["list", "--storage", str(b)]) == 0
    assert cli.main(["show", "--storage", str(b), "--quarantine", str(rid)]) == 0
    out = capsys.readouterr().out
    assert "deploy" in out


def test_unknown_subcommand_returns_2(tmp_path):
    # argparse raises SystemExit(2); main() catches it and returns the code.
    assert cli.main(["bogus"]) == 2


def test_show_bad_rowid_returns_1_no_traceback(tmp_path, capsys):
    rc = cli.main(["show", "--storage", str(tmp_path), "--quarantine", "notanint"])
    assert rc == 1
    assert "error:" in capsys.readouterr().out


def test_show_without_selector_returns_2(tmp_path):
    assert cli.main(["show", "--storage", str(tmp_path)]) == 2


def test_conscio_cli_routes_noosphere(monkeypatch):
    from conscio import cli as top
    called = {}
    monkeypatch.setattr("conscio.noosphere.cli.main",
                        lambda argv: called.update(argv=argv) or 0)
    assert top.main(["noosphere", "id", "--storage", "/tmp/x"]) == 0
    assert called["argv"] == ["id", "--storage", "/tmp/x"]
