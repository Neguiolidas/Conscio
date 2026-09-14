from conscio import obsstore
from conscio.honesty import verdicts as o
from conscio.honesty.classes import Claim
from conscio.honesty.evidence import check


def _conn(tmp_path):
    return obsstore.connect(tmp_path / "obs.db")


def _obs(conn, inp, out, session="s1"):
    return obsstore.put_observation(conn, tool="Bash", input_text=inp,
                                    output_text=out, session_id=session,
                                    project="p", agent="a",
                                    ts="2026-09-13T10:00:00")


def _claim(anchor="abc1234"):
    return Claim("commit", anchor, (0, 0))


def test_matching_call_verifies(tmp_path):
    conn = _conn(tmp_path)
    oid = _obs(conn, "git commit -m x", "[abc1234] done")
    got, ptr = check(conn, _claim(), "s1")
    assert got == o.VERIFIED
    assert ptr == f"obs:{oid}"


def test_absent_call_contradicts_when_the_whole_session_is_visible(tmp_path):
    conn = _conn(tmp_path)
    _obs(conn, "ls", "file.txt")
    got, _ = check(conn, _claim(), "s1")
    assert got == o.CONTRADICTED


def test_empty_capture_is_unsupported_not_contradicted(tmp_path):
    """Nao consegui olhar nunca vira acusacao (N3/C4)."""
    conn = _conn(tmp_path)
    got, _ = check(conn, _claim(), "s1")
    assert got == o.UNSUPPORTED


def test_other_session_does_not_count(tmp_path):
    conn = _conn(tmp_path)
    _obs(conn, "git commit -m x", "[abc1234] done", session="outra")
    got, _ = check(conn, _claim(), "s1")
    assert got == o.UNSUPPORTED


# ── emenda A3: janela saturada e ignorancia, nao ausencia ──────────────

def test_saturated_window_is_unsupported_never_contradicted(tmp_path):
    """Sessao maior que a janela: o predicado ve um pedaco e NAO pode acusar.

    Medido em producao: sessoes de 1925, 1306 e 318 observacoes estouram a
    janela de 200. Acusar aqui transformaria toda afirmacao verdadeira sobre o
    passado da sessao numa mentira -- o R1, que e o risco dominante do PRD.
    """
    conn = _conn(tmp_path)
    for i in range(6):
        _obs(conn, f"cmd {i}", f"out {i}")
    got, _ = check(conn, _claim(), "s1", limit=5)
    assert got == o.UNSUPPORTED


def test_window_exactly_full_still_contradicts(tmp_path):
    """O limiar exato: a sessao inteira COUBE, entao a ausencia e positiva.

    Falso negativo que o review hostil do Hermet pegou na primeira versao da
    emenda, que comparava len(rows) == limit.
    """
    conn = _conn(tmp_path)
    for i in range(5):
        _obs(conn, f"cmd {i}", f"out {i}")
    got, _ = check(conn, _claim(), "s1", limit=5)
    assert got == o.CONTRADICTED


def test_anchor_inside_a_saturated_window_still_verifies(tmp_path):
    """Saturacao so impede ACUSAR. Encontrar continua sendo prova."""
    conn = _conn(tmp_path)
    for i in range(6):
        _obs(conn, f"cmd {i}", f"out {i}")
    oid = _obs(conn, "git commit", "[abc1234] done")     # a mais recente
    got, ptr = check(conn, _claim(), "s1", limit=5)
    assert got == o.VERIFIED
    assert ptr == f"obs:{oid}"


def test_anchor_outside_a_saturated_window_is_unsupported(tmp_path):
    """A afirmacao e VERDADEIRA e o predicado nao alcanca: cala, nao acusa."""
    conn = _conn(tmp_path)
    _obs(conn, "git commit", "[abc1234] done")           # a mais antiga
    for i in range(8):
        _obs(conn, f"cmd {i}", f"out {i}")
    got, ptr = check(conn, _claim(), "s1", limit=5)
    assert got == o.UNSUPPORTED
    assert ptr == ""
