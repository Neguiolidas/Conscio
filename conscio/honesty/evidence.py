"""Porta 3 do funil: o predicado deterministico.

So contesta quando a ausencia e POSITIVA -- a sessao inteira coube na consulta
e a ancora nao aparece em nenhuma chamada. Captura vazia, sessao alheia ou
janela saturada significam "nao consegui olhar", e isso e UNSUPPORTED, nunca
acusacao: um gate que confunde as duas teria travado todo turno durante a
v4.0.0, quando a captura estava inerte.

Zero imports do resto do Conscio: este modulo e vendorizado para junto do hook.
"""

from __future__ import annotations

import json
import re
import time
import zlib

from . import verdicts as o
from .classes import ARGUMENT, CLASSES_BY_NAME, COMMAND_KEYS, PATH_KEYS, Claim

#: Teto de LINHAS: rede de seguranca contra sessao patologica, nao o limite
#: efetivo. O limite real e o de tempo abaixo.
WINDOW = 5000

#: Teto de TEMPO por afirmacao, em milissegundos. A restricao sempre foi custo,
#: entao e o custo que se limita: um teto de linhas punia sessao longa mesmo
#: quando barata e premiava sessao curta e cara.
#:
#: CONDICAO QUE TORNA ISTO DEFENSAVEL, e ela e o desenho inteiro: um veredito
#: que depende do relogio so pode variar no lado que NAO acusa. Estourar o
#: orcamento resolve UNSUPPORTED ("nao consegui olhar"), nunca CONTRADICTED.
#: Maquina carregada perde deteccao; jamais inventa uma.
#:
#: Tres numeros medidos na maior sessao real (1925 observacoes, ~15MB de
#: blobs), e os tres precisam estar aqui porque um so engana: ~123ms e o custo
#: ESTAVEL do scan completo, ~809ms foi o outlier de cache de pagina frio no
#: primeiro acesso, e 800ms e o teto -- 6.5x o estavel, cobrindo o outlier
#: medido.
BUDGET_MS = 800


def _blob_text(conn, h) -> str:
    """Reimplementado de proposito: este modulo nao pode importar ``obsstore``,
    porque e copiado para fora do pacote. Formato identico -- zlib sobre o
    conteudo enderecado por sha256, em ``blobs(h, z, n)``."""
    if not h:
        return ""
    row = conn.execute("SELECT z FROM blobs WHERE h=?", (h,)).fetchone()
    if row is None:
        return ""
    try:
        return zlib.decompress(row[0]).decode("utf-8", "replace")
    except zlib.error:
        return ""


#: Separador que o harness usa para marcar chamada que FALHOU no proprio nome
#: da ferramenta (``Bash!failed``, medido no obs.db vivo).
_FAIL_MARK = "!"


def _normalize_tool(raw: str) -> tuple[str, bool]:
    """Nome canonico da ferramenta + se a chamada falhou.

    O erro e lido do NOME, nunca do texto da saida. Escanear a saida por
    "fatal:" ou "Traceback" e o mesmo defeito que o ``verify()`` tinha: lexico
    passando por semantico. Medido em 400 chamadas BEM-SUCEDIDAS desta base,
    2% seriam descartadas como erro -- entre elas um ``git commit`` legitimo
    (a saida trazia "command not found" de outro trecho do script) e, com
    ironia, o proprio comando que escreveu o detector, porque o codigo-fonte
    dele contem os padroes que ele procura.
    """
    name, sep, _ = raw.partition(_FAIL_MARK)
    return name, bool(sep)


#: Corpo de heredoc: DADO que o comando escreve, nunca comando.
_HEREDOC = re.compile(r"<<-?\s*'?\"?(\w+)'?\"?.*?^\1", re.DOTALL | re.MULTILINE)


def _payload(entrada: str) -> dict | None:
    """O envelope JSON da chamada, ou ``None`` quando a entrada nao e um.

    A entrada e gravada como o payload JSON da ferramenta, onde a quebra de
    linha e a sequencia ``\\n`` e nao um caractere -- casar heredoc sobre o
    texto cru nunca fecha, porque o delimitador jamais aparece no inicio de uma
    linha real.
    """
    try:
        payload = json.loads(entrada or "")
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _field(payload: dict, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        valor = payload.get(k)
        if isinstance(valor, str) and valor:
            return valor
    return None


def _command(entrada: str) -> str | None:
    """O comando de fato, sem os corpos de heredoc. ``None`` = nao sei ler.

    Um ``cat > arquivo <<EOF`` carrega o conteudo INTEIRO do arquivo dentro da
    entrada, e casar o padrao do ato contra ele faz um arquivo que FALA de um
    ato parecer o ato. Medido: o comando que escreveu os testes de uma emenda
    anterior casava "git commit" porque um docstring citava a expressao.

    Um payload que nao traz nenhuma chave de comando conhecida devolve
    ``None``, e isso vale UNSUPPORTED. Antes cairia no JSON cru, e o padrao do
    ato voltava a casar contra o blob inteiro -- o A1 entrando por outra porta.
    """
    payload = _payload(entrada)
    if payload is None:
        return _HEREDOC.sub("", entrada or "")   # o texto ja e o comando
    bruto = _field(payload, COMMAND_KEYS)
    return None if bruto is None else _HEREDOC.sub("", bruto)


#: Onde a regiao de um ato termina. Cortar aqui so consegue ENCURTAR a regiao:
#: um ``;`` dentro de aspas trunca e resolve UNSUPPORTED. O erro cai sempre do
#: lado que nao acusa -- o mesmo invariante que sustenta o teto de tempo.
_SEPARATOR = re.compile(r"[;&|\n]")


def _act_regions(comando: str, act: str):
    """Cada ocorrencia do ato com seus argumentos, ate o proximo separador.

    TODAS as ocorrencias, nao a primeira: ``touch a.py && touch b.py`` tem dois
    atos, e olhar so o primeiro perderia o segundo. A regiao comeca no proprio
    casamento para que o executavel conte como alvo -- e o que mantem "rodei
    pytest" verificavel sem regra especial.
    """
    for m in re.finditer(act, comando, re.IGNORECASE):
        fim = _SEPARATOR.search(comando, m.end())
        yield comando[m.start():fim.start() if fim else len(comando)]


def _norm(bruto: str | None) -> str:
    """Forma comparavel de um caminho: sem delimitador de prosa, sem esquema
    ``file://`` (o Antigravity cita caminho assim) e sem ``./`` redundante."""
    s = (bruto or "").strip().strip("`\"'.,()[]{}<>")
    s = s.removeprefix("file://")
    while s.startswith("./"):
        s = s[2:]
    return s


def _hits(alvo: str | None, ancora: str) -> bool:
    """Fronteira de caminho, nunca substring.

    ``--ignore=tests/x.py`` diz o OPOSTO de ter rodado x.py, e substring o
    transformaria em prova do que ele nega.
    """
    a, t = _norm(ancora), _norm(alvo)
    return bool(a) and (t == a or t.endswith("/" + a))


def _targets(shape, entrada: str) -> list[str] | None:
    """Os alvos do ato nesta observacao, ou ``None`` quando nao sei ler.

    ``None`` nao e "nao casou": e "nao consegui olhar", e poisona o veredito
    para UNSUPPORTED. Runtime nao medido perde deteccao; nunca ganha acusacao.
    """
    if not shape.act:                       # a ferramenta E o ato
        payload = _payload(entrada)
        campo = _field(payload, PATH_KEYS) if payload is not None else None
        return [campo] if campo else None
    comando = _command(entrada)
    if comando is None:
        return None
    return [tok for regiao in _act_regions(comando, shape.act)
            for tok in regiao.split()]


def check(conn, claim: Claim, session_id: str,
          limit: int = WINDOW) -> tuple[str, str]:
    """Devolve ``(outcome, evidence_pointer)``.

    A evidencia e o TRACO DO ATO, nunca a mencao da ancora. Uma observacao so
    conta quando a ferramenta executa a classe, a entrada identifica o ato, a
    ancora aparece do lado que quem a gerou permite, e a saida nao denuncia
    falha. Casar apenas a ancora aceitava texto escrito pelo proprio afirmante
    -- inventar um sha e digita-lo num comando dava VERIFIED.

    O ponteiro so existe quando VERIFIED: contestacao aponta para ausencia, e
    ausencia nao tem endereco.

    O filtro por ferramenta roda no SQL, antes de descomprimir blob: as
    centenas de leituras inertes de uma sessao (Read, LS, Grep) deixam de
    ocupar a janela, e com isso a ausencia volta a ser POSITIVA em sessao real
    -- era o que tornava CONTRADICTED inalcancavel.

    Na v4.6.3 a ancora de ARGUMENT deixou de valer "em algum lugar da entrada"
    e passou a exigir a POSICAO DE ARGUMENTO do ato. Sem isso, escrever um
    arquivo que CITA outro caminho provava ter escrito o caminho citado.
    """
    cls = CLASSES_BY_NAME.get(claim.cls_name)
    if cls is None or not cls.acts:
        return o.UNSUPPORTED, ""          # classe sem ato definido: nao olho

    deadline = time.monotonic() + BUDGET_MS / 1000.0
    # Traz tambem a variante marcada como falha: normalizar exige ver o nome
    # cru, e nome EXATO (nunca substring) -- o obs guarda nomes com prefixo de
    # harness (mcp__plugin_..._remember), e casar por substring deixaria uma
    # claim de arquivo virar VERIFIED pelo texto de outra ferramenta.
    base = {t for shape in cls.acts for t in shape.tools}
    tools = sorted(base | {f"{t}{_FAIL_MARK}failed" for t in base})
    placeholders = ",".join("?" * len(tools))
    rows = conn.execute(
        "SELECT id, tool, in_h, out_h FROM observations"
        f" WHERE session_id=? AND tool IN ({placeholders})"
        " ORDER BY id DESC LIMIT ?",
        (session_id, *tools, limit + 1)).fetchall()
    if not rows:
        return o.UNSUPPORTED, ""          # nao consegui olhar

    esgotado = ilegivel = False
    for oid, raw_tool, in_h, out_h in rows:
        if time.monotonic() > deadline:
            esgotado = True               # nao vi o resto: nao posso acusar
            break
        tool, failed = _normalize_tool(raw_tool)
        if failed:
            continue                      # o ato foi tentado e nao aconteceu
        shapes = [sh for sh in cls.acts if tool in sh.tools]
        if not shapes:
            continue
        entrada = _blob_text(conn, in_h)
        saida = None
        for shape in shapes:
            if shape.anchor_in == ARGUMENT:
                alvos = _targets(shape, entrada)
                if alvos is None:
                    ilegivel = True       # payload que nao sei ler
                elif any(_hits(alvo, claim.anchor) for alvo in alvos):
                    return o.VERIFIED, f"obs:{oid}"
                continue
            # OUTPUT: identificador que nasce no mundo, exigido na saida.
            comando = _command(entrada)
            if comando is None:
                ilegivel = True
                continue
            if shape.act and not re.search(shape.act, comando, re.IGNORECASE):
                continue                  # a ferramenta serve, o ato nao e este
            if saida is None:
                saida = _blob_text(conn, out_h)
            if claim.anchor in saida:
                return o.VERIFIED, f"obs:{oid}"

    if esgotado or ilegivel or len(rows) > limit:
        # A sessao nao coube no orcamento: o que nao foi lido pode conter a
        # prova, e ausencia so e POSITIVA quando olhamos tudo.
        return o.UNSUPPORTED, ""
    return o.CONTRADICTED, ""
