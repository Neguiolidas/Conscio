# conscio/honesty/classes.py
"""Classes de afirmação com predicado possível.

O critério de admissão de uma classe NÃO é frequência — é existir consulta que
a refute sem interpretação. Por isso "verifiquei"/"confirmei" ficam de fora: não
há como conferir o que a pessoa diz ter conferido.

Toda classe é bilíngue por invariante. Um reconhecedor só-pt funciona e PARECE
funcionar numa sessão em inglês, falhando em silêncio.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Claim:
    cls_name: str
    anchor: str
    span: tuple[int, int]


@dataclass(frozen=True)
class ActShape:
    """Uma forma de executar o ato de uma classe.

    ``tools`` e casado ANTES de qualquer padrao: medido em producao, 2 de 22
    observacoes com "git commit" na entrada eram mensagens de relay DISCUTINDO
    o assunto. Sem casar a ferramenta primeiro, falar sobre o ato vira o ato.

    ``act`` e regex sobre a ENTRADA e identifica O QUE FOI TENTADO; vazio
    significa que a propria ferramenta ja e o ato (uma chamada de ``Write`` e
    uma escrita, nao precisa de padrao).

    ``anchor_side`` diz onde a ancora pode contar, e a regra e QUEM GERA O
    IDENTIFICADOR. Sha e alvo de push nascem no mundo: exigidos na SAIDA --
    medido em 10 commits reais, o sha novo aparece so na saida, e sha na
    entrada e sempre citacao (git show/log), nunca criacao. Caminho de arquivo
    e argumento dado pelo agente: fica na ENTRADA mesmo, e o que impede forjar
    e a identidade da ferramenta, porque o harness registra a chamada real --
    digitar um caminho num comando nao e invocar Write nele.
    """

    tools: frozenset[str]
    act: str
    anchor_side: str


#: Ferramentas medidas no obs.db vivo, nao herdadas de memoria. O harness
#: registra chamada que falhou sob nome PROPRIO (``Bash!failed``), entao filtrar
#: por ``Bash`` ja descarta o ato que nao aconteceu.
_SHELL = frozenset({"Bash"})
_WRITERS = frozenset({
    "Write", "Edit", "NotebookEdit",          # Claude Code (medidos)
    "write_to_file", "replace_file_content",  # Antigravity
    "multi_replace_file_content",
    "write_file", "file_editor",              # Hermes
})


@dataclass(frozen=True)
class ClaimClass:
    name: str
    patterns_pt: tuple[str, ...]
    patterns_en: tuple[str, ...]
    anchor: str            # grupo nomeado ``anchor`` obrigatório
    acts: tuple[ActShape, ...] = ()


_SHA = r"(?P<anchor>[0-9a-f]{7,40})"
_PATH = r"(?P<anchor>[\w./-]+\.\w{1,6})"
_CMD = r"(?P<anchor>[\w./-]+)"

#: Redirecao ou heredoc: o caminho pelo qual este projeto de fato escreve
#: arquivo pelo shell. Sem isto, `cat > arquivo <<EOF` nao contaria como ato.
_REDIRECT = r"(?:>{1,2}\s*|tee\s+(?:-a\s+)?)"

CLASSES: tuple[ClaimClass, ...] = (
    ClaimClass("commit",
               (rf"commit(?:ei|ado|ou)\s+(?:em\s+)?{_SHA}",),
               (rf"committed\s+(?:as\s+)?{_SHA}",), _SHA,
               (ActShape(_SHELL,
                         r"git\s+(?:commit|merge|cherry-pick|revert)"
                         r"|gh\s+pr\s+merge", "output"),)),
    ClaimClass("push",
               (rf"push(?:ei|ado)\s+(?:pra|para)?\s*{_CMD}",),
               (rf"pushed\s+(?:to\s+)?{_CMD}",), _CMD,
               (ActShape(_SHELL, r"git\s+push", "output"),)),
    ClaimClass("test_run",
               (rf"(?:rodei|executei)\s+{_CMD}",),
               (rf"(?:ran|executed)\s+{_CMD}",), _CMD,
               (ActShape(_SHELL,
                         r"pytest|unittest|npm\s+(?:run\s+)?test"
                         r"|cargo\s+test|go\s+test|make\s+test|\btox\b",
                         "input"),)),
    ClaimClass("file_write",
               (rf"(?:criei|escrevi|atualizei)\s+{_PATH}",),
               (rf"(?:created|wrote|updated)\s+{_PATH}",), _PATH,
               (ActShape(_WRITERS, "", "input"),
                ActShape(_SHELL, _REDIRECT, "input"))),
)

#: Busca por nome: ``evidence.check`` recebe um ``Claim`` (leve) e precisa da
#: classe para saber o que conta como ato.
CLASSES_BY_NAME: dict[str, ClaimClass] = {c.name: c for c in CLASSES}

_COMPILED = tuple(
    (cls, re.compile(p, re.IGNORECASE))
    for cls in CLASSES
    for p in cls.patterns_pt + cls.patterns_en
)


_STOP_WORDS: frozenset[str] = frozenset({
    "os", "as", "um", "uma", "tudo", "isso", "de", "do", "da", "no", "na",
    "the", "all", "it", "that", "this", "them", "everything",
})


def _is_valid_anchor(anchor: str) -> bool:
    """Emenda A6: âncora vaga/gramatical não pode atravessar como VERIFIED.

    Rejeita âncoras com menos de 3 caracteres ou presentes na stop-list
    bilíngue de palavras gramaticais.
    """
    if len(anchor) < 3:
        return False
    return anchor.lower() not in _STOP_WORDS



def find_claims(text: str) -> list[Claim]:
    """Porta 1 (léxica) + porta 2 (âncora), juntas.

    Sem âncora não há Claim — é assim que a classe vaga morre sem regra
    especial contra ela.
    """
    found: list[Claim] = []
    for cls, rx in _COMPILED:
        for m in rx.finditer(text or ""):
            anchor = (m.groupdict().get("anchor") or "").strip()
            if anchor and _is_valid_anchor(anchor):
                found.append(Claim(cls.name, anchor, m.span()))
    return found

