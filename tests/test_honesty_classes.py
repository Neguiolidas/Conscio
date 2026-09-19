# tests/test_honesty_classes.py
from conscio.honesty.classes import CLASSES, find_claims


def test_commit_claim_with_anchor_is_found():
    claims = find_claims("commitei em abc1234 e segui")
    assert [c.cls_name for c in claims] == ["commit"]
    assert claims[0].anchor == "abc1234"


def test_english_is_covered_too():
    claims = find_claims("committed abc1234")
    assert [c.cls_name for c in claims] == ["commit"]


def test_claim_without_anchor_is_dropped():
    """Sem artefato nomeado não há predicado possível."""
    assert find_claims("commitei tudo") == []


def test_vague_verification_is_never_a_class():
    assert find_claims("verifiquei e confirmei que está certo") == []


def test_every_class_is_bilingual():
    """Classe que exista num idioma só falha o build, não o silêncio."""
    for cls in CLASSES:
        assert cls.patterns_pt, f"{cls.name} sem padrão pt"
        assert cls.patterns_en, f"{cls.name} sem padrão en"


def test_stop_words_and_short_anchors_are_rejected():
    """Emenda A6: âncora vaga/gramatical não pode atravessar como VERIFIED."""
    assert find_claims("rodei os testes") == []
    assert find_claims("rodei tudo de novo") == []
    assert find_claims("pushei tudo") == []
    assert find_claims("commitei tudo") == []
    pytest_claim = find_claims("rodei pytest")
    assert len(pytest_claim) == 1
    assert pytest_claim[0].cls_name == "test_run"
    assert pytest_claim[0].anchor == "pytest"

    push_claim = find_claims("pushei pra origin")
    assert len(push_claim) == 1
    assert push_claim[0].cls_name == "push"
    assert push_claim[0].anchor == "origin"



# ── v4.6.3: a claim tem de ser PRIMEIRA PESSOA e AFIRMATIVA ────────────
#
# Medido em 1225 mensagens reais: a porta lexica passa 68 e a de ancora
# passa 4 (queda de 94,1%), porque o padrao exigia a ancora colada no verbo.
# Alargar o conector sem este portao transformaria negacao e citacao em
# afirmacao minha.


def _anchors(text):
    return [c.anchor for c in find_claims(text)]


def test_anchor_delimited_by_markdown_is_found():
    """Prosa real da frota: 'commitado (`f82e504`)'. A crase e o parentese
    entre verbo e ancora eram o que cegava o reconhecedor."""
    assert _anchors("commitado (`f82e504`)") == ["f82e504"]


def test_anchor_after_an_article_is_found():
    assert _anchors("criei o arquivo `tests/test_x.py`") == ["tests/test_x.py"]


def test_negated_claim_never_becomes_a_claim():
    """'nao commitei nada' nao e afirmacao a verificar; e ausencia dela.

    Sem este portao, alargar o conector faria o reconhecedor contestar
    exatamente quem disse a verdade.
    """
    assert find_claims("nao commitei nada") == []
    assert find_claims("nao criei tests/test_x.py ainda") == []


def test_a_contrast_conjunction_ends_the_negation_scope():
    """'Nao consegui X, mas rodei Y' afirma Y. A janela de negacao tem de
    parar no contraste, senao mata claim verdadeira."""
    assert _anchors("Nao consegui rodar o lint, mas rodei tests/x.py") == [
        "tests/x.py"]


def test_an_act_attributed_to_someone_else_is_not_my_claim():
    """O predicado olha a MINHA sessao. Citar o ato de outro agente viraria
    CONTRADICTED contra uma frase verdadeira -- o pior desfecho possivel."""
    assert find_claims("o Hermet commitou f82e504") == []
    # Em ingles o verbo e neutro quanto a pessoa: aqui so a olhada-atras
    # separa o meu ato do ato de outro agente.
    assert find_claims("the Hermet committed f82e504") == []
    assert find_claims("you wrote tests/test_x.py") == []


def test_the_connector_does_not_cross_the_clause():
    """'escrevi um relatorio sobre X' nao afirma ter escrito X."""
    assert find_claims(
        "escrevi um relatorio sobre conscio/mcp/server.py") == []


# ── v4.6.3: citacao nao e afirmacao, e substantivo nao e artefato ──────
#
# Medido sobre o corpus historico DEPOIS de alargar o conector: 25 de 30
# claims resolviam CONTRADICTED, e todas as amostradas eram a mensagem
# CITANDO uma afirmacao. Alargar a porta sem estes dois portoes troca cegueira
# por um gerador de falsa acusacao -- pior que o defeito original.


def test_a_claim_inside_a_code_fence_is_citation():
    """Bloco de codigo e DADO que a mensagem exibe, nunca afirmacao dela --
    o mesmo raciocinio que tirou o corpo de heredoc do comando na 4.6.2."""
    texto = ("exemplos do corpus:\n```\n"
             "Rodei o hook do jeito que o Claude Code roda\n"
             "criei `conscio-relay-reactor.service`\n```\n")
    assert find_claims(texto) == []


def test_a_claim_inside_quotes_is_citation():
    assert find_claims(
        'a claim "escrevi conscio/mcp/server.py" deu VERIFIED') == []


def test_the_anchor_itself_may_still_wear_backticks():
    """O criterio e a posicao do VERBO, nao da ancora: quem escreve
    "criei `x.py`" poe a ancora entre crases de proposito."""
    assert _anchors("criei `tests/test_x.py`") == ["tests/test_x.py"]


def test_a_vague_noun_is_not_a_runnable_anchor():
    """'rodei o hook' nao tem consulta que o refute sem interpretacao --
    o criterio de admissao do desenho. Eram 6 das 25 contestacoes."""
    assert find_claims("rodei o hook") == []
    assert find_claims("rodei a checagem") == []


def test_a_named_checker_is_a_runnable_anchor():
    """'rodei pyright' e verificavel; acusar aqui seria contestar verdade."""
    assert _anchors("rodei pyright") == ["pyright"]


def test_a_long_extension_is_not_truncated():
    """`\\w{1,6}` cortava ".service" em ".servic": a ancora nascia errada, nao
    casava nada e virava ACUSACAO. Medido no corpus historico."""
    assert _anchors("criei `conscio-relay-reactor.service`") == [
        "conscio-relay-reactor.service"]


def test_apostrophes_do_not_swallow_a_claim():
    """Duas contracoes ("it's ... that's") formavam um span de aspas simples
    que engolia o verbo entre elas e matava a claim. Aspas simples saem do
    portao de citacao: o ganho medido veio de crase, aspas duplas e fence."""
    assert _anchors("it's done: committed abc1234 and that's it") == ["abc1234"]


def test_a_leading_dot_survives_the_delimiter_strip():
    """`_DELIMS` tem '.' e `strip` tira dos DOIS lados: ".env.local" virava
    "env.local", que nunca casa o alvo real e vira ACUSACAO. Delimitador de
    prosa a esquerda nao inclui ponto -- dotfile comeca com ele."""
    assert _anchors("criei `.env.local`") == [".env.local"]


def test_a_blockquote_line_is_citation():
    """Citar log e issue com '>' e idioma universal de analise de causa.
    Medido pelo Gemini e reproduzido aqui: as duas frases viravam claim."""
    assert find_claims("> Issue #123: criei `schema.sql` e deu timeout.") == []
    assert find_claims("> Log do CI: executei `pytest` com sucesso.") == []


def test_a_redirect_mid_line_is_not_a_blockquote():
    """O criterio e o INICIO da linha: redirecionamento vive dentro de
    comando, nunca abrindo linha de prosa."""
    assert _anchors("criei `a.py` rodando cat > a.py") == ["a.py"]

