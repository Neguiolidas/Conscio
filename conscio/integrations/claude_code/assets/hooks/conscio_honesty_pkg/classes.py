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


#: Onde a ancora do ato pode contar. Nomeados porque a string solta ja
#: significou "em qualquer ponto da entrada", que era o defeito.
OUTPUT = "output"
ARGUMENT = "argument"


@dataclass(frozen=True)
class ActShape:
    """Uma forma de executar o ato de uma classe.

    ``tools`` e casado ANTES de qualquer padrao: medido em producao, 2 de 22
    observacoes com "git commit" na entrada eram mensagens de relay DISCUTINDO
    o assunto. Sem casar a ferramenta primeiro, falar sobre o ato vira o ato.

    ``act`` e regex sobre a ENTRADA e identifica O QUE FOI TENTADO; vazio
    significa que a propria ferramenta ja e o ato (uma chamada de ``Write`` e
    uma escrita, nao precisa de padrao).

    ``anchor_in`` diz onde a ancora pode contar, e a regra e QUEM GERA O
    IDENTIFICADOR. Sha e alvo de push nascem no mundo: exigidos na SAIDA --
    medido em 10 commits reais, o sha novo aparece so na saida, e sha na
    entrada e sempre citacao (git show/log), nunca criacao. Caminho de arquivo
    e argumento dado pelo agente: vale ARGUMENT -- a POSICAO DE ARGUMENTO do
    ato, nunca "em algum lugar da entrada". Medido na obs 10295: um ``Write``
    do arquivo de plano provava "escrevi conscio/mcp/server.py" porque o plano
    CITAVA o caminho no corpo. O que o agente escreve DENTRO do arquivo nao diz
    qual arquivo ele escreveu.
    """

    tools: frozenset[str]
    act: str
    anchor_in: str


#: Nomes do Claude Code MEDIDOS no obs.db; os dos outros dois runtimes vieram
#: do agente que roda neles, conferidos no toolsets do proprio repo.
#:
#: LIMITE DECLARADO, e ele e maior do que a v4.6.2 registrou. Aquela versao
#: dizia que faltava o EVENTO de falha nos outros dois runtimes. Medido em
#: 2026-09-14 pelos agentes que rodam neles, o que falta e o PRODUTOR INTEIRO:
#: nem o Antigravity nem o Hermes tem captura de tool call escrevendo no
#: ``obs.db`` (no Hermes, ``grep`` zero em ``tools/``; a unica observacao la
#: veio de um spike manual). Os dois espacos tem ZERO linhas em ``claims``.
#:
#: Ou seja: o laco de honestidade e MONO-RUNTIME de fato. Estas tabelas estao
#: corretas e sao baratas, mas cobertura homogenea seria insinuacao falsa --
#: nos outros dois tudo resolve UNSUPPORTED por ausencia de telemetria, com o
#: nome de campo certo ou errado. Dar captura ao Hermes e feature nova.
_SHELL = frozenset({
    "Bash",          # Claude Code (medido no obs.db)
    "run_command",   # Antigravity
    "terminal",      # Hermes
})
_WRITERS = frozenset({
    "Write", "Edit", "NotebookEdit",          # Claude Code (medidos no obs.db)
    "write_to_file", "replace_file_content",  # Antigravity
    "multi_replace_file_content",
    "write_file", "patch",                    # Hermes
})


#: Chaves de argumento por runtime. Claude Code medido por mim no obs.db;
#: Antigravity e Hermes medidos pelo Gemini no transcript real e no
#: codigo-fonte dos tools. O PascalCase NAO e capricho de estilo: inferir
#: ``target_file`` faria toda escrita nativa do Antigravity cair em
#: UNSUPPORTED permanente -- falha sem sintoma, que e a pior que existe aqui.
PATH_KEYS = ("file_path", "notebook_path",    # Claude Code
             "TargetFile", "AbsolutePath",    # Antigravity
             "path")                          # Hermes
COMMAND_KEYS = ("command",                    # Claude Code, Hermes
                "CommandLine")                # Antigravity


@dataclass(frozen=True)
class ClaimClass:
    name: str
    patterns_pt: tuple[str, ...]
    patterns_en: tuple[str, ...]
    anchor: str            # grupo nomeado ``anchor`` obrigatório
    acts: tuple[ActShape, ...] = ()
    #: Regex que a ancora tem de satisfazer para a classe admiti-la. Vazio =
    #: qualquer ancora serve. Mora aqui, ao lado do regex que a extrai, porque
    #: "o que conta como ancora" e decisao da classe, nao do reconhecedor.
    anchor_ok: str = ""


#: Entre o verbo e a ancora a prosa real poe pontuacao, markdown e no maximo
#: duas palavras de funcao: "commitado (`f82e504`)", "criei o arquivo `x.py`".
#: Medido: com ``\s+`` a porta de ancora descartava 94,1% do que a porta
#: lexica deixava passar (68 mensagens -> 4) em 1225 turnos reais.
#:
#: O teto de DUAS palavras e o que mantem verbo e ancora na mesma oracao. Sem
#: ele, "escrevi um relatorio sobre X" afirmaria ter escrito X.
_FUNC = (r"(?:em|como|no|na|o|a|os|as|um|uma|para|pra|de|do|da"
         r"|the|to|as|at|in|into|file|arquivo)")
_SEP = r"[\s:,\-\u2014(\[\"'`]+"
_GAP = rf"{_SEP}(?:{_FUNC}\b{_SEP}){{0,2}}"

_SHA = r"(?P<anchor>[0-9a-f]{7,40})"
#: A extensao vai ate 10 e exige fronteira: com ``\w{1,6}`` a ancora
#: ".service" nascia ".servic", nao casava nada e virava ACUSACAO -- falso
#: positivo produzido por truncamento, medido no corpus historico.
_PATH = r"(?P<anchor>[\w./-]+\.\w{1,10})(?!\w)"
_CMD = r"(?P<anchor>[\w./-]+)"

#: Redirecao ou heredoc: o caminho pelo qual este projeto de fato escreve
#: arquivo pelo shell. Sem isto, `cat > arquivo <<EOF` nao contaria como ato.
_REDIRECT = r"(?:>{1,2}\s*|tee\s+(?:-a\s+)?|touch\s+|sed\s+-i\b|cp\s+)"

#: O runner tem de ser o EXECUTAVEL INVOCADO, nao uma palavra no meio do
#: comando: sem a ancoragem, `grep pytest` verificava "rodei pytest". Aceita os
#: prefixos de wrapper que a frota usa de fato.
#: A QUEBRA DE LINHA e fronteira de comando tanto quanto o `;`: sem ela, um
#: `pytest` na segunda linha de um comando multilinha -- a norma nesta frota --
#: nao ancorava e a afirmacao verdadeira virava acusacao (medido, obs 10310).
#: `timeout N` entra pelo mesmo motivo: quase todo teste daqui e envolvido nele.
_RUNNER = (r"(?:^|[;&|\n]\s*)"
           r"(?:timeout\s+\d+\s+|env\s+\S+=\S+\s+)?"
           r"(?:uv\s+run\s+|poetry\s+run\s+|python3?\s+-m\s+|npx\s+)?"
           r"(?:pytest|unittest|cargo\s+test|go\s+test|make\s+test|tox|nox"
           r"|pyright|ruff|mypy|vulture|bandit"
           r"|npm\s+(?:run\s+)?test)\b")

#: Ancora de execucao tem de ser artefato executavel. O criterio de admissao
#: do desenho e "existir consulta que a refute sem interpretacao", e
#: "hook"/"checagem"/"controle"/"suite"/"comando" nao tem nenhuma -- todas
#: apareceram entre as 25 contestacoes que o corpus historico produzia, varias
#: repetidas, sempre acusando por vaguidade. Caminho (tem / ou .) ou nome de
#: executavel conhecido.
_RUNNABLE = (r"[/.]|^(?:pytest|tox|nox|unittest|pyright|ruff|mypy|vulture"
             r"|bandit|make|cargo|npm|npx|go|uv)$")

CLASSES: tuple[ClaimClass, ...] = (
    ClaimClass("commit",
               (rf"commit(?:ei|ado){_GAP}{_SHA}",),
               (rf"committed{_GAP}{_SHA}",), _SHA,
               (ActShape(_SHELL,
                         r"git\s+(?:commit|merge|cherry-pick|revert)"
                         r"|gh\s+pr\s+merge", OUTPUT),)),
    ClaimClass("push",
               (rf"push(?:ei|ado){_GAP}{_CMD}",),
               (rf"pushed{_GAP}{_CMD}",), _CMD,
               (ActShape(_SHELL, r"git\s+push", OUTPUT),)),
    ClaimClass("test_run",
               (rf"(?:rodei|executei){_GAP}{_CMD}",),
               (rf"(?:ran|executed){_GAP}{_CMD}",), _CMD,
               # Duas formas, e a segunda cobre o caso mais comum da frota:
               # rodar um DIRETORIO e afirmar um arquivo dele. O nome do
               # arquivo na saida do runner e identificador gerado pelo MUNDO
               # -- nao ha auto-certificacao possivel, porque o ato ja teve de
               # ser uma invocacao de runner para a observacao contar.
               (ActShape(_SHELL, _RUNNER, ARGUMENT),
                ActShape(_SHELL, _RUNNER, OUTPUT)), _RUNNABLE),
    ClaimClass("file_write",
               (rf"(?:criei|escrevi|atualizei){_GAP}{_PATH}",),
               (rf"(?:created|wrote|updated){_GAP}{_PATH}",), _PATH,
               (ActShape(_WRITERS, "", ARGUMENT),
                ActShape(_SHELL, _REDIRECT, ARGUMENT))),
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
    # Com o conector alargado, "rodei os testes" passa a render a ancora
    # "testes" em vez de "os". Continua sendo a afirmacao vaga que a porta 2
    # existe para matar -- so mudou a palavra que sobra.
    "testes", "teste", "tests", "test", "suite", "lint", "build",
})

#: Marcadores que tiram a afirmacao de mim ou a negam. Um conceito so: a claim
#: precisa ser MINHA e AFIRMATIVA. Negacao e atribuicao a terceiro levam ao
#: mesmo lugar -- a claim nao nasce --, entao moram na mesma porta.
#:
#: Sem isto, alargar o conector faria "nao commitei nada" e "the Hermet
#: committed f82e504" virarem afirmacoes minhas. Na sombra e inocuo; com a
#: contestacao ligada viraria CONTRADICTED contra uma frase VERDADEIRA, que e
#: o pior desfecho que este sistema pode produzir. Medido: das 64 mensagens
#: que a porta de ancora descartava, varias eram exatamente negacoes.
_NOT_MINE = re.compile(
    r"\b(n[ãa]o|nem|nenhum|nenhuma|nada|sem|jamais|nunca"
    r"|hip[óo]tese alguma|de jeito nenhum|ningu[ée]m|zero"
    r"|not|never|nothing|without|n't"
    r"|nobody|no one"
    r"|ele|ela|eles|elas|voc[êe]|vc|tu|seu|teu"
    r"|he|she|they|you|your|his|her|their"
    r"|hermet|gemini|hermes|owner|usuario|usu[áa]rio|user)\b",
    re.IGNORECASE)

#: Onde a olhada-atras termina. O contraste importa tanto quanto a pontuacao:
#: "Nao consegui rodar o lint, MAS rodei tests/x.py" afirma o segundo ato, e
#: parar so em '.' mataria uma claim verdadeira.
_CLAUSE_END = re.compile(
    r"[.;\n]|\b(mas|por[ée]m|todavia|contudo|entretanto"
    r"|but|however|though|although)\b", re.IGNORECASE)

#: Janela de olhada-atras. O delimitador SEMANTICO e `_CLAUSE_END` (ponto,
#: ponto-e-virgula, quebra, contraste); este teto existe so para limitar custo.
#: Era 60 e virou limite semantico por acidente: "Nao e verdade que durante as
#: investigacoes preliminares do bug eu criei `fix.py`" tem 66 caracteres entre
#: a negacao e o verbo, e a negacao era cortada. 400 e folga larga sobre
#: qualquer oracao real, e o custo e um regex sobre 400 caracteres.
#: Janela de olhada-atras. O delimitador SEMANTICO e `_CLAUSE_END` (ponto,
#: ponto-e-virgula, quebra, contraste); este teto existe so para limitar custo.
#: Era 60 e virou limite semantico POR ACIDENTE: "Nao e verdade que durante as
#: investigacoes preliminares do bug eu criei `fix.py`" tem 66 caracteres entre
#: a negacao e o verbo, e a negacao era cortada -- o reconhecedor acusava
#: exatamente quem negou. 400 e folga larga sobre qualquer oracao real, e o
#: custo e um regex sobre 400 caracteres. NAO reduzir sem medir: o numero
#: pequeno parece economia e e um portao semantico disfarcado.
_LOOKBACK = 400


def _clause_before(text: str, start: int) -> str:
    """A oração imediatamente antes de ``start``, cortada no delimitador."""
    janela = text[max(0, start - _LOOKBACK):start]
    cortes = list(_CLAUSE_END.finditer(janela))
    return janela[cortes[-1].end():] if cortes else janela


def _is_mine_and_affirmative(text: str, start: int) -> bool:
    """A oração imediatamente antes do verbo nega ou atribui a outro?"""
    return _NOT_MINE.search(_clause_before(text, start)) is None


#: Modalidade: a afirmacao e minha, e sobre mim, e mesmo assim nao e asserção.
#: FAMILIA POR RADICAL, nao conjugacao enumerada -- medido: um rascunho com
#: `ach(?:o|ei|amos)` e `cre(?:io|mos)` deixava passar as duas frases
#: adversariais do Hermet ("Duvida se executei", "Acredito que executei"),
#: porque faltavam `duvid*` e `acredit*`. Os verbos de crenca exigem o
#: complementizador "que": em pt, "achei o arquivo" e ENCONTRAR, nao crenca.
#: `(?<!sem )` porque "sem duvida" e CERTEZA -- hoje inalcancavel (`sem` ja
#: esta no _NOT_MINE), mantido para o dia em que aquela lista mudar. O
#: `(?<!no )` ingles NAO e redundante: "No doubt I committed" chega aqui.
#:
#: LIMITE DECLARADO: hedge DEPOIS do verbo ("criei x.py, acho eu") fica de
#: fora. A janela olha para tras porque e la que a modalidade mora em quase
#: todo caso real. Cegueira aceita, do lado que nao acusa.
#:
#: RADICAL SO ONDE NAO COLIDE. Medido: `cre\w*`, `ach\w*` e `doubt\w*` matavam
#: seis claims verdadeiras por casar dentro de palavra comum sem relacao --
#: "credito", "creche", "crescimento" (contem "cre"), "achado" (contem "ach"),
#: "doubtless" (contem "doubt"). A familia por radical continua valendo para
#: os verbos onde ela nao colide (`duvid*`, `acredit*`, `suspeit*`, `supon*`,
#: `presum*`) -- o principio serve so para nao perder CONJUGACAO, nunca para
#: um radical curto engolir substantivo/adverbio alheio. Onde colide, a lista
#: enumera as formas conjugadas do verbo em vez do radical.
#:
#: `teria` ancorado no FIM da janela (`\bteria\s*$`), nao mais solto: a janela
#: ja corta exatamente no inicio do verbo da claim, entao ancorar no fim da
#: janela equivale a "teria imediatamente antes do verbo" -- exigir particip
#: depois seria pedir o proprio verbo. Sem a ancora, um `teria` de uma oracao
#: anterior atravessava a janela e matava uma claim verdadeira mais adiante
#: ("O relatorio que eu teria revisado ficou pronto, e commitei abc1234.").
_HEDGE = re.compile(
    r"\b(?:talvez|possivelmente|provavelmente|aparentemente"
    r"|supon\w*|presum\w*|parece\s+que"
    r"|(?<!sem )d[uú]vid\w*"
    r"|(?:acredit\w*|suspeit\w*)\s+que"
    r"|ach(?:o|a|as|amos|am|ei|ou|aram|ava|avam|aria|ariam)\s+que"
    r"|cre(?:io|s|mos|em|ia|iam)\s+que|cr[eê]\s+que"
    r"|imagin(?:o|a|as|amos|am|ei|ava|aria)\s+que"
    r"|dev(?:e|ia)\s+ter|poderia\s+ter"
    r"|maybe|perhaps|probably|apparently|supposedly|presumably|seems"
    r"|(?<!no )doubts?"
    r"|i\s+think|i\s+believe|i\s+guess|i\s+assume|i\s+suspect"
    r"|might\s+have|could\s+have|would\s+have)\b"
    r"|\bteria\s*$", re.IGNORECASE)


#: LIMITE DECLARADO (medido depois do commit anterior): hedge preso a um verbo
#: ANTERIOR, separado so por virgula ou "e"/"and", ainda mata uma claim
#: verdadeira mais adiante: "Acho que o time ganhou, e commitei abc1234" nao
#: rende claim nenhuma, mas a mesma frase com ponto final ("...ganhou.
#: Commitei abc1234") rende uma. A janela de oracao corta em `. ; \n` e
#: conjuncao de contraste, nao em virgula -- comportamento herdado de
#: `_NOT_MINE`. Mudar a janela mudaria tambem o portao de negacao, fora do
#: escopo deste ajuste. O erro cai do lado que nao acusa.


def _is_unhedged(text: str, start: int) -> bool:
    """A oração modaliza o verbo? Modalizada, não é asserção."""
    return _HEDGE.search(_clause_before(text, start)) is None


def _is_valid_anchor(anchor: str) -> bool:
    """Emenda A6: âncora vaga/gramatical não pode atravessar como VERIFIED.

    Rejeita âncoras com menos de 3 caracteres ou presentes na stop-list
    bilíngue de palavras gramaticais.
    """
    if len(anchor) < 3:
        return False
    return anchor.lower() not in _STOP_WORDS



#: Bloco de codigo e span citado sao DADO que a mensagem EXIBE, nunca
#: afirmacao dela -- o mesmo raciocinio que tirou o corpo de heredoc do comando
#: na 4.6.2, uma camada acima. Medido: depois de alargar o conector, 25 das 30
#: claims do corpus historico resolviam CONTRADICTED, e TODAS as amostradas
#: eram a mensagem citando uma afirmacao (exemplo de corpus, saida de sonda,
#: prosa de desenho). Sem este portao, alargar a porta troca cegueira por um
#: gerador de falsa acusacao.
#:
#: O criterio e a posicao do VERBO, nao a da ancora: quem escreve
#: "criei `x.py`" poe a ancora entre crases de proposito, e isso e afirmacao.
_FENCE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
#: Aspas SIMPLES ficam de fora de proposito: duas contracoes ("it's ...
#: that's") formam um span que engole o verbo entre elas e mata a claim. O
#: ganho medido veio de crase, aspas duplas e bloco de codigo.
_QUOTED = re.compile(r"`[^`\n]*`|\"[^\"\n]*\"|\u201c[^\u201d\n]*\u201d")

#: Linha de citacao markdown. O criterio e o INICIO da linha: redirecionamento
#: de shell vive dentro de comando, nunca abrindo linha de prosa, entao nao ha
#: ambiguidade. Medido: "> Issue #123: criei `schema.sql`" virava claim.
_BLOCKQUOTE = re.compile(r"^[ \t]*>.*$", re.MULTILINE)
#: Bloco de codigo por INDENTACAO (4+ espacos), que o markdown aceita sem
#: cerca. Mascara toda linha indentada, e nao so as que seguem linha em branco
#: como manda a regra estrita: medido, ZERO claims do corpus real nascem em
#: linha indentada, entao a regra estrita so deixaria passar o caso
#: adversarial sem economizar nada. Continuacao de item de lista e mascarada
#: junto -- cegueira aceita, do lado que nao acusa.
_INDENTED = re.compile(r"^[ \t]{4,}\S.*$", re.MULTILINE)


def _cited_spans(text: str) -> list[tuple[int, int]]:
    return [m.span()
            for rx in (_FENCE, _QUOTED, _BLOCKQUOTE, _INDENTED)
            for m in rx.finditer(text)]




#: Terminadores de frase, procurados a partir do FIM do casamento. Comecar no
#: verbo seria errado: o ponto de "fix.py" viraria terminador e uma pergunta
#: sobre um caminho com extensao passaria batida.
_SENTENCE_END = re.compile(r"[.!?\n]")


def _is_assertion(text: str, end: int) -> bool:
    """A frase que contem a claim termina em '?' -> e pergunta, nao afirmacao.

    Erra por frase inteira: "Executei `pytest`, nao sei se passou?" afirma o
    ato e e barrada. Falso negativo consciente, do lado que nao acusa.
    """
    m = _SENTENCE_END.search(text, end)
    return not (m and m.group() == "?")


#: Delimitadores que a prosa poe em volta da ancora e que nao fazem parte dela.
_DELIMS_BOTH = "`\"'()[]{}<>"
_DELIMS_RIGHT = ".,:;!?"


def find_claims(text: str) -> list[Claim]:
    """Porta 1 (léxica) + porta 2 (âncora), juntas.

    Sem âncora não há Claim — é assim que a classe vaga morre sem regra
    especial contra ela. E sem ser minha e afirmativa também não há Claim:
    negação e citação do ato alheio morrem na mesma porta, antes do predicado.
    """
    found: list[Claim] = []
    citados = _cited_spans(text or "")
    for cls, rx in _COMPILED:
        for m in rx.finditer(text or ""):
            anchor = (m.groupdict().get("anchor") or "").strip()
            # Ponto SO cai do lado direito: dotfile comeca com ele, e tirar a
            # esquerda transformava ".env.local" em "env.local" -- ancora que
            # nunca casa o alvo real e portanto vira acusacao.
            anchor = anchor.strip(_DELIMS_BOTH).rstrip(_DELIMS_RIGHT)
            if not (anchor and _is_valid_anchor(anchor)):
                continue
            if cls.anchor_ok and not re.search(cls.anchor_ok, anchor,
                                               re.IGNORECASE):
                continue                  # a classe nao admite esta ancora
            if any(a <= m.start() < b for a, b in citados):
                continue                  # a mensagem CITA, nao afirma
            if not _is_assertion(text or "", m.end()):
                continue                  # pergunta nao e asserção
            if not _is_unhedged(text or "", m.start()):
                continue                  # modalizada nao e asserção
            if _is_mine_and_affirmative(text or "", m.start()):
                found.append(Claim(cls.name, anchor, m.span()))
    return found

