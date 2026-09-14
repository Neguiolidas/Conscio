"""A evidencia tem de ser o TRACO DO ATO, nao a MENCAO DA ANCORA.

Medido em producao na v4.6.1: o predicado casava a ancora contra a ENTRADA da
observacao, que e texto escrito pelo proprio afirmante. Inventar um sha e
digita-lo em qualquer comando produzia VERIFIED -- auto-certificacao, o defeito
que esta versao existe para matar. E o registro do proprio laco, impresso na
saida de um comando, virava prova textual de que a ancora existe.
"""
import pytest

from conscio import obsstore
from conscio.honesty import verdicts as o
from conscio.honesty.classes import Claim
from conscio.honesty.evidence import check


@pytest.fixture
def conn(tmp_path):
    return obsstore.connect(tmp_path / "obs.db")


def _obs(conn, inp, out, *, tool="Bash", session="s1"):
    return obsstore.put_observation(conn, tool=tool, input_text=inp,
                                    output_text=out, session_id=session,
                                    project="p", agent="a",
                                    ts="2026-09-14T10:00:00")


def _claim(cls_name="commit", anchor="abc1234"):
    return Claim(cls_name, anchor, (0, 0))


# ── (a) a tool e casada ANTES do padrao ───────────────────────────────

def test_a_message_discussing_the_act_is_not_the_act(conn):
    """Medido: 2 de 22 observacoes com 'git commit' na entrada eram mensagens
    de relay DISCUTINDO o desenho, com sha no texto."""
    _obs(conn, "falando sobre git commit e o sha abc1234", "ok",
         tool="mcp__plugin_conscio_conscio__conscio_relay")
    got, _ = check(conn, _claim(), "s1")
    assert got != o.VERIFIED


# ── (b) a entrada tem de casar o ATO da classe ────────────────────────

def test_typing_the_anchor_without_the_act_never_verifies(conn):
    """O caso 'deadbee': sha inventado, digitado num comando qualquer."""
    _obs(conn, "echo deadbee > /tmp/nota", "")
    got, _ = check(conn, _claim(anchor="deadbee"), "s1")
    assert got != o.VERIFIED


def test_inspecting_a_commit_is_not_creating_it(conn):
    """git show/log CITAM o sha; nao o criam. Medido: sha na entrada e
    sempre citacao, nunca criacao."""
    _obs(conn, "git show abc1234", "commit abc1234\nAuthor: x")
    got, _ = check(conn, _claim(), "s1")
    assert got != o.VERIFIED


# ── (c) identificador do MUNDO exige a ancora na SAIDA ────────────────

def test_a_real_commit_verifies_from_its_output(conn):
    oid = _obs(conn, "git commit -m x", "[main abc1234] mensagem")
    got, ptr = check(conn, _claim(), "s1")
    assert got == o.VERIFIED
    assert ptr == f"obs:{oid}"


def test_the_loop_own_report_is_not_evidence(conn):
    """Ouroboros: a saida do registro do laco contem a ancora e o veredito."""
    _obs(conn, "conscio honesty recent", "commit | abc1234 -> UNSUPPORTED")
    got, _ = check(conn, _claim(), "s1")
    assert got != o.VERIFIED


# ── (d) identificador do AGENTE: a tool e a garantia ──────────────────

def test_a_native_write_tool_verifies_from_its_arguments(conn):
    """Escrita NUNCA passa por Bash em nenhum dos tres runtimes."""
    oid = _obs(conn, '{"file_path": "foo/bar.py", "content": "x"}', "ok",
               tool="Write")
    got, ptr = check(conn, Claim("file_write", "foo/bar.py", (0, 0)), "s1")
    assert got == o.VERIFIED
    assert ptr == f"obs:{oid}"


def test_a_heredoc_write_through_the_shell_also_verifies(conn):
    """O caminho que este projeto de fato usa: cat > arquivo <<EOF."""
    oid = _obs(conn, "cat > foo/bar.py <<'EOF'\nx\nEOF", "")
    got, ptr = check(conn, Claim("file_write", "foo/bar.py", (0, 0)), "s1")
    assert got == o.VERIFIED
    assert ptr == f"obs:{oid}"


def test_merely_reading_a_path_is_not_writing_it(conn):
    """Rodar pytest sobre um arquivo cita o caminho e nao o escreve -- a obs
    10623 real, que eu proprio usei como prova e nao provava nada."""
    _obs(conn, "for f in foo/bar.py; do pytest $f; done", "1 passed")
    got, _ = check(conn, Claim("file_write", "foo/bar.py", (0, 0)), "s1")
    assert got != o.VERIFIED


# ── (e) saida de erro nao e prova de ato ──────────────────────────────

def test_a_failed_act_does_not_verify(conn):
    _obs(conn, "git commit -m x", "fatal: nothing to commit abc1234")
    got, _ = check(conn, _claim(), "s1")
    assert got != o.VERIFIED


# ── A2: o filtro por tool destrava CONTRADICTED ───────────────────────

def test_inert_reads_no_longer_saturate_the_window(conn):
    """Leituras inertes afogavam a janela e faziam toda ausencia virar
    UNSUPPORTED. Filtradas no SQL, a ausencia volta a ser positiva."""
    for i in range(30):
        _obs(conn, f"ls dir{i}", f"saida {i}", tool="LS")
    _obs(conn, "git commit -m outro", "[main 9999999] outro")
    got, _ = check(conn, _claim(), "s1", limit=10)
    assert got == o.CONTRADICTED
