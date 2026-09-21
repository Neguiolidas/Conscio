import json

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
    assert ptr == "why:window"


# ── v4.6.3: a evidencia e a POSICAO DE ARGUMENTO do ato ────────────────
#
# A emenda da 4.6.2 fechou so as classes de ancora na SAIDA (commit, push).
# As de ancora na ENTRADA seguiam casando a ancora contra o blob inteiro
# escrito pelo agente -- o A1 vivo na classe mais comum.


def _obs_tool(conn, tool, payload, out="", session="s1"):
    return obsstore.put_observation(conn, tool=tool,
                                    input_text=json.dumps(payload),
                                    output_text=out, session_id=session,
                                    project="p", agent="a",
                                    ts="2026-09-13T10:00:00")


def _fw(anchor):
    return Claim("file_write", anchor, (0, 0))


def test_write_content_is_not_evidence_of_writing_the_paths_it_cites(tmp_path):
    """Regressao do A1 residual, medida em producao na obs 10295.

    Um ``Write`` do arquivo de PLANO dava VERIFIED para "escrevi
    conscio/mcp/server.py" porque o plano CITAVA esse caminho no corpo. O que
    o agente escreve DENTRO do arquivo nao prova qual arquivo ele escreveu.
    """
    conn = _conn(tmp_path)
    _obs_tool(conn, "Write", {
        "file_path": "/repo/docs/plano.md",
        "content": "o plano toca conscio/mcp/server.py e tests/test_x.py",
    })
    got, _ = check(conn, _fw("conscio/mcp/server.py"), "s1")
    assert got == o.CONTRADICTED


def test_the_path_actually_written_verifies(tmp_path):
    """A contrapartida: o alvo real do ato conta, e a ancora e sufixo dele."""
    conn = _conn(tmp_path)
    oid = _obs_tool(conn, "Write", {"file_path": "/repo/tests/test_x.py",
                                    "content": "irrelevante"})
    got, ptr = check(conn, _fw("tests/test_x.py"), "s1")
    assert (got, ptr) == (o.VERIFIED, f"obs:{oid}")


def test_payload_without_a_known_path_field_is_unsupported(tmp_path):
    """Runtime nao medido perde deteccao; nao ganha auto-certificacao.

    Payload que nao sei ler e "nao consegui olhar", e isso jamais acusa.
    """
    conn = _conn(tmp_path)
    _obs_tool(conn, "Write", {"campo_desconhecido": "/repo/tests/test_x.py"})
    got, _ = check(conn, _fw("tests/test_x.py"), "s1")
    assert got == o.UNSUPPORTED


def test_antigravity_pascal_case_path_field_is_read(tmp_path):
    """Medido pelo Gemini no transcript real: o Antigravity usa TargetFile.

    Inferir ``target_file`` faria toda escrita nativa daquele runtime cair em
    UNSUPPORTED permanente, sem ninguem perceber.
    """
    conn = _conn(tmp_path)
    _obs_tool(conn, "write_to_file", {"TargetFile": "/repo/tests/test_x.py",
                                      "CodeContent": "..."})
    got, _ = check(conn, _fw("tests/test_x.py"), "s1")
    assert got == o.VERIFIED


def test_antigravity_command_field_is_decoded(tmp_path):
    """``_decoded`` so conhecia a chave ``command``; o Antigravity manda
    ``CommandLine``, e o JSON cru voltava a valer como comando."""
    conn = _conn(tmp_path)
    _obs_tool(conn, "run_command",
              {"CommandLine": "touch /repo/tests/test_x.py"})
    got, _ = check(conn, _fw("tests/test_x.py"), "s1")
    assert got == o.VERIFIED


def test_path_after_a_command_separator_is_not_the_acts_target(tmp_path):
    """``touch a.txt; echo b.py`` nao escreve b.py -- a regiao do ato acaba
    no separador."""
    conn = _conn(tmp_path)
    _obs_tool(conn, "Bash", {"command": "touch a.txt; echo tests/fake.py"})
    got, _ = check(conn, _fw("tests/fake.py"), "s1")
    assert got == o.CONTRADICTED


def test_every_act_of_a_composed_command_is_checked(tmp_path):
    """Dois atos num comando so: olhar apenas o primeiro perderia o segundo."""
    conn = _conn(tmp_path)
    _obs_tool(conn, "Bash", {"command": "touch a.py && touch b.py"})
    got, _ = check(conn, _fw("b.py"), "s1")
    assert got == o.VERIFIED


def test_an_excluded_test_file_is_not_proof_of_running_it(tmp_path):
    """``--ignore=tests/x.py`` diz o OPOSTO de ter rodado x.py."""
    conn = _conn(tmp_path)
    _obs_tool(conn, "Bash",
              {"command": "pytest --ignore=tests/x.py tests/y.py"})
    got, _ = check(conn, Claim("test_run", "tests/x.py", (0, 0)), "s1")
    assert got == o.CONTRADICTED


def test_the_runner_arguments_do_verify(tmp_path):
    """A contrapartida do anterior: argumento da invocacao conta."""
    conn = _conn(tmp_path)
    _obs_tool(conn, "Bash", {"command": "uv run pytest tests/y.py -q"})
    got, _ = check(conn, Claim("test_run", "tests/y.py", (0, 0)), "s1")
    assert got == o.VERIFIED


def test_a_file_named_in_the_runners_output_counts_as_run(tmp_path):
    """Rodar um DIRETORIO e afirmar um arquivo dele e verdade, e a prova vem
    da saida do proprio runner -- identificador gerado pelo MUNDO, nao pelo
    afirmante. Medido: era falsa acusacao contra afirmacao verdadeira."""
    conn = _conn(tmp_path)
    _obs_tool(conn, "Bash", {"command": "uv run pytest tests/ -q"},
              out="tests/test_liaison_bindings.py .... 54 passed")
    got, _ = check(conn, Claim("test_run", "tests/test_liaison_bindings.py",
                               (0, 0)), "s1")
    assert got == o.VERIFIED


def test_a_file_merely_listed_by_another_command_is_not_run(tmp_path):
    """A contrapartida: so conta na saida de uma INVOCACAO de runner."""
    conn = _conn(tmp_path)
    _obs_tool(conn, "Bash", {"command": "ls tests/"},
              out="test_liaison_bindings.py")
    got, _ = check(conn, Claim("test_run", "tests/test_liaison_bindings.py",
                               (0, 0)), "s1")
    assert got != o.VERIFIED


def test_a_runner_on_a_later_line_is_still_the_invocation(tmp_path):
    """Defeito pre-existente medido em producao (obs 10310): `_RUNNER`
    ancorava em `^` sem MULTILINE e em `[;&|]`, e quebra de linha nao estava
    em nenhum dos dois. Comando multilinha e a norma nesta frota, entao um
    pytest legitimo na segunda linha virava ACUSACAO contra afirmacao
    verdadeira. `timeout N` idem: a frota envolve quase todo teste nele.
    """
    conn = _conn(tmp_path)
    _obs_tool(conn, "Bash", {"command": "cd /repo\n"
                             "timeout 240 python3 -m pytest tests/test_x.py -q"})
    got, _ = check(conn, Claim("test_run", "tests/test_x.py", (0, 0)), "s1")
    assert got == o.VERIFIED


def test_an_interpreter_write_poisons_the_accusation(tmp_path):
    """`python3 - <<'PY' ... write_text ... PY` e invisivel: o corpo do heredoc
    e descartado e o que sobra nao casa padrao de escrita. Acusar ai e acusar
    por nao conseguir ver."""
    conn = _conn(tmp_path)
    _obs_tool(conn, "Bash", {"command":
        'python3 - <<\'PY\'\nfrom pathlib import Path\n'
        'Path("tests/test_x.py").write_text("x")\nPY'})
    got, _ = check(conn, _fw("tests/test_x.py"), "s1")
    assert got == o.UNSUPPORTED


def test_merely_citing_a_path_in_a_script_does_not_poison(tmp_path):
    """O gatilho e o ARGUMENTO de uma chamada de escrita, nao a mencao.
    Medido: com gatilho frouxo, 5 caminhos de controle inventados viraram
    UNSUPPORTED porque a sonda que os citava foi capturada."""
    conn = _conn(tmp_path)
    _obs_tool(conn, "Bash", {"command":
        'python3 - <<\'PY\'\nprint("tests/test_x.py")\nPY'})
    got, _ = check(conn, _fw("tests/test_x.py"), "s1")
    assert got == o.CONTRADICTED


def test_a_json_dump_into_an_open_handle_also_poisons(tmp_path):
    """A spec (§4.2) cita `json.dump(..., open(X, "w"))` literalmente. O
    padrao o cobre pelo `open()` aninhado, e e justamente por ser indireto
    que merece teste proprio."""
    conn = _conn(tmp_path)
    _obs_tool(conn, "Bash", {"command":
        'python3 -c \'import json; json.dump({}, open("cfg.json","w"))\''})
    got, _ = check(conn, _fw("cfg.json"), "s1")
    assert got == o.UNSUPPORTED


def test_an_interpreter_write_never_verifies(tmp_path):
    """Nunca VERIFIED: eu nao sei se aquele ramo executou, e transformar
    codigo que MENCIONA uma escrita em prova dela reabre a auto-certificacao."""
    conn = _conn(tmp_path)
    _obs_tool(conn, "Bash", {"command":
        'python3 -c \'open("tests/test_x.py","w").write("x")\''})
    got, _ = check(conn, _fw("tests/test_x.py"), "s1")
    assert got != o.VERIFIED

