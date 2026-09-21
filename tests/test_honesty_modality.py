# tests/test_honesty_modality.py
"""v4.6.6 item 2: afirmacao modalizada nao e afirmacao.

Desenho do Hermet: FAMILIA, nao lista de verbos. Que "talvez" e "poderia"
nao vazassem antes era acidente de conjugacao, nao cobertura.
"""
import pytest

from conscio.honesty.classes import find_claims

HEDGED = [
    # As DUAS do Hermet, verbatim (review hostil dele, nao transcript real).
    "Duvida se executei `pytest` ontem",
    "Acredito que executei `pytest`",
    # duvida
    "Talvez eu tenha commitado abc1234",
    "Possivelmente criei config.py",
    "Maybe I committed abc1234",
    "I probably wrote config.py",
    "Duvido que criei config.py",
    "I doubt I ran tests/test_x.py",
    # crenca (exigem o complementizador 'que' em pt)
    "Acho que rodei tests/test_x.py",
    "Creio que commitei abc1234",
    "I think I committed abc1234",
    "I believe I wrote config.py",
    # suposicao
    "Suponho que criei config.py",
    "Presumo que rodei tests/test_x.py",
    "I assume I ran tests/test_x.py",
    "Supposedly I committed abc1234",
    # hipotese -- o VERBO tem de ser um que o reconhecedor enxergue:
    # "Deve ter criado config.py" produz ZERO claims (participio nao nasce
    # claim), entao testaria nada. Medido.
    "Poderia ter commitado abc1234",
    "Deve ter commitado abc1234",
    "Teria commitado abc1234",
    "I might have committed abc1234",
    "I could have committed abc1234",
    # aparencia
    "Parece que rodei tests/test_x.py",
    "Apparently I committed abc1234",
    "Estou achando que commitei abc1234",
    "Estou imaginando que criei config.py",
    "Estou crendo que commitei abc1234",
    "Teria, honestamente, commitado abc1234",
    "It's doubtful I committed abc1234",
    # v4.6.6 fix round (achado do Gemini): 7 formas que vazavam por omissao
    # nas listas ou restricao de pronome. HEDGED e UNHEDGED existem juntos de
    # proposito: radical solto mata as claims verdadeiras de UNHEDGED,
    # enquanto enumeracao incompleta deixa vazar as formas de HEDGED.
    "Penso que criei config.py",
    "Pensei que criei config.py",
    "Ache que criei config.py",
    "Achavamos que criei config.py",
    "Imaginou que criei config.py",
    "I thought I created config.py",
    "We think we committed abc1234",
]

UNHEDGED = [
    "Commitei abc1234",
    "Criei config.py",
    "I committed abc1234",
    "Rodei tests/test_x.py",
    # A ARMADILHA: 'achei' aqui e ENCONTRAR, nao crenca -- sem 'que' nao e modal.
    "Achei o arquivo velho e criei config.py",
    # Contraste fecha a oracao: o hedge nao alcanca a segunda afirmacao.
    "Talvez eu tenha errado, mas commitei abc1234",
    # 'no doubt' e CERTEZA: o lookbehind (?<!no ) existe para isto, e foi medido.
    "No doubt I committed abc1234",
    # Fix round 1 (achado do coordenador): radical solto engolia palavra comum
    # sem relacao com o verbo de crenca -- medido, seis falsos negativos.
    "O credito que pedi saiu, e commitei abc1234",
    "A creche que escolhi abriu, e criei config.py",
    "O crescimento que tivemos ajudou, e commitei abc1234",
    "O achado que fizemos rendeu, e criei config.py",
    "Doubtless I committed abc1234",
    "O relatorio que eu teria revisado ficou pronto, e commitei abc1234.",
]


@pytest.mark.parametrize("frase", HEDGED)
def test_a_hedged_claim_does_not_survive_the_gate(frase):
    assert find_claims(frase) == [], f"vazou: {frase!r}"


@pytest.mark.parametrize("frase", UNHEDGED)
def test_a_plain_claim_still_becomes_a_claim(frase):
    """Controle negativo: sem ele o gate vira cegueira e mata claim verdadeira."""
    assert find_claims(frase), f"matou uma afirmacao real: {frase!r}"
