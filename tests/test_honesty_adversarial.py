"""Corpus adversarial da v4.6.4 — o unico teste desta suite cujo caso NAO veio
da minha propria prosa.

As nove frases do lote do Gemini e as tres do lote do Hermet foram construidas
por outros dois agentes, em outros runtimes, tentando fazer o reconhecedor
acusar quem nao mentiu. As nove vazavam na 4.6.3 publicada; as tres do Hermet
ja estavam fechadas e ficam como nao-regressao dos portoes daquela versao.

O controle afirmativo existe para que fechar os portoes nao vire cegueira
total: se ele parar de produzir claim, o conserto foi longe demais.
"""

from conscio.honesty.classes import find_claims

#: Lote do Gemini: cinco classes de vazamento, nove frases. Todas produziam
#: claim na 4.6.3, e claim sobre texto que nao afirma o ato vira acusacao.
GEMINI = (
    "> Issue #123: criei `schema.sql` e deu timeout.",
    "> Log do CI: executei `pytest` com sucesso.",
    "O desenvolvedor me enviou:\n    criei `fix.patch`",
    ("Não é verdade que durante as investigações preliminares do bug "
     "eu criei `fix.py`."),
    "Será que executei `pytest` antes do commit?",
    "Como saber se executei `pytest`?",
    "Em hipótese alguma criei `fix.py`.",
    "Ninguém disse que criei `fix.py`.",
    "Zero vezes criei `fix.py`.",
)

#: Lote do Hermet: citacao e negacao, fechados desde a 4.6.3. Aqui como
#: nao-regressao -- alargar portao e o caminho classico de reabri-los.
HERMET = (
    'O plano diz: "commitado em 67e1c2c" e "escrevi conscio/mcp/server.py"',
    "Não commitei em deadbee e não escrevi conscio/mcp/server.py",
    'A string "escrevi conscio/mcp/server.py" aparece no teste.',
)


def test_the_gemini_batch_produces_no_claims():
    vazam = {f: find_claims(f) for f in GEMINI if find_claims(f)}
    assert vazam == {}


def test_the_hermet_batch_stays_closed():
    vazam = {f: find_claims(f) for f in HERMET if find_claims(f)}
    assert vazam == {}


def test_the_affirmative_control_still_produces_claims():
    """A contrapartida que impede o conserto de virar cegueira total."""
    claims = find_claims("Commitado em e8f8b92. Escrevi conscio/mcp/server.py.")
    assert {c.cls_name for c in claims} == {"commit", "file_write"}
