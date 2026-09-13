import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HOOK = Path("conscio/integrations/claude_code/assets/hooks/conscio_honesty.py")


def _run(payload, storage):
    """--honesty aponta para o pacote NO REPO; no plugin ele e vendorizado."""
    return subprocess.run(
        [sys.executable, str(HOOK), "--storage", str(storage),
         "--honesty", "conscio/honesty", "--obsstore", "conscio/obsstore.py"],
        input=json.dumps(payload), capture_output=True, text=True)


def _payload(**over):
    base = {"hook_event_name": "Stop", "session_id": "s1",
            "last_assistant_message": "commitei em abc1234",
            "stop_hook_active": False}
    base.update(over)
    return base


def test_shadow_mode_never_writes_to_stdout(tmp_path):
    out = _run(_payload(), tmp_path)
    assert out.stdout.strip() == ""
    assert out.returncode == 0


def test_it_actually_records(tmp_path):
    """exit 0 nao prova nada neste hook por desenho; prove pela linha gravada."""
    _run(_payload(), tmp_path)
    rows = sqlite3.connect(tmp_path / "conscio.db").execute(
        "SELECT cls_name, outcome FROM claims").fetchall()
    assert rows == [("commit", "UNSUPPORTED")]


def test_malformed_stdin_exits_zero(tmp_path):
    out = subprocess.run(
        [sys.executable, str(HOOK), "--storage", str(tmp_path),
         "--honesty", "conscio/honesty", "--obsstore", "conscio/obsstore.py"],
        input="nao e json", capture_output=True, text=True)
    assert out.returncode == 0


def test_continued_turn_records_nothing(tmp_path):
    _run(_payload(stop_hook_active=True), tmp_path)
    assert not (tmp_path / "conscio.db").exists()


def test_the_hook_sweeps_expired_pendings(tmp_path):
    """EMENDA A1: sem isto, expire_stale nao tem chamador nenhum em producao.

    Grava uma pendencia vencida ANTES, roda o hook, e afirma a transicao pela
    EXECUCAO do hook -- nao por chamada direta ao ledger.
    """
    db = tmp_path / "conscio.db"
    from conscio.agency.ledger import ActionLedger
    ledger = ActionLedger(db)
    row_id = ledger.record(goal_fp="g", tool="bash", args_json="{}",
                           rationale="r", tier="T1", status="executed")
    ledger._conn.execute("UPDATE actions SET ts=? WHERE id=?",
                         (time.time() - 40 * 86400, row_id))
    ledger._conn.commit()
    ledger.close()

    _run(_payload(), tmp_path)

    assert ActionLedger(db).get(row_id)["outcome"] == "UNSUPPORTED"


def test_a_fresh_space_without_actions_does_not_break_the_hook(tmp_path):
    """O hook roda em espaco novo, onde a tabela actions nem existe."""
    out = _run(_payload(), tmp_path)
    assert out.returncode == 0
    assert out.stdout.strip() == ""


def test_the_vendored_package_runs_with_conscio_unimportable(tmp_path):
    """O smoke que vale: copia o pacote para fora do repo E PROIBE importar
    ``conscio``.

    Sem o bloqueio o teste nao prova nada nesta maquina, onde o conscio esta
    instalado em modo editavel e um ``import conscio`` acidental dentro de
    honesty/ resolveria em silencio. No plugin do usuario ele NAO resolve --
    o pacote vem do PyPI por uvx e nao esta ao lado do hook. Foi assim que a
    captura da v4.0.0 ficou inerte: exit 0 sem ter feito nada.
    """
    import shutil
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    shutil.copytree("conscio/honesty", bundle / "conscio_honesty_pkg")
    shutil.copy2("conscio/obsstore.py", bundle / "conscio_obsstore.py")
    shutil.copy2(HOOK, bundle / "conscio_honesty.py")

    (bundle / "runner.py").write_text(
        "import runpy, sys\n"
        "class Blocker:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'conscio' or name.startswith('conscio.'):\n"
        "            raise ImportError('blocked by the vendoring smoke: ' + name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, Blocker())\n"
        "sys.argv = sys.argv[1:]\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n", encoding="utf-8")

    space = tmp_path / "space"
    out = subprocess.run(
        [sys.executable, str(bundle / "runner.py"),
         str(bundle / "conscio_honesty.py"), "--storage", str(space),
         "--honesty", str(bundle / "conscio_honesty_pkg"),
         "--obsstore", str(bundle / "conscio_obsstore.py")],
        input=json.dumps(_payload()), capture_output=True, text=True,
        cwd=str(tmp_path))

    assert out.returncode == 0
    rows = sqlite3.connect(space / "conscio.db").execute(
        "SELECT cls_name, outcome FROM claims").fetchall()
    assert rows == [("commit", "UNSUPPORTED")], (
        "o pacote vendorizado nao gravou com conscio bloqueado: algum modulo "
        f"de honesty/ importa para fora. stderr={out.stderr[-400:]}")
