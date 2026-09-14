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
from .classes import CLASSES_BY_NAME, Claim

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


def _command_only(entrada: str) -> str:
    """A entrada sem os corpos de heredoc.

    Um ``cat > arquivo <<EOF`` carrega o conteudo INTEIRO do arquivo dentro da
    entrada, e casar o padrao do ato contra ele faz um arquivo que FALA de um
    ato parecer o ato. Medido: o comando que escreveu os testes desta emenda
    casava "git commit" porque um docstring citava a expressao, e a saida
    trazia a ancora porque mostrava o proprio arquivo -- escrever o teste que
    prova a afirmacao falsa virava a prova de que ela era verdadeira.
    """
    return _HEREDOC.sub("", _decoded(entrada))


def _decoded(entrada: str) -> str:
    """O comando de fato, fora do envelope JSON da chamada.

    A entrada e gravada como o payload JSON da ferramenta, onde a quebra de
    linha e a sequencia ``\\n`` e nao um caractere -- casar heredoc sobre o
    texto cru nunca fecha, porque o delimitador jamais aparece no inicio de uma
    linha real.
    """
    try:
        payload = json.loads(entrada or "")
    except (ValueError, TypeError):
        return entrada or ""
    if isinstance(payload, dict):
        campo = payload.get("command")
        if isinstance(campo, str):
            return campo
    return entrada or ""


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

    esgotado = False
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
            if shape.act and not re.search(shape.act, _command_only(entrada),
                                            re.IGNORECASE):
                continue                  # a ferramenta serve, o ato nao e este
            if saida is None:
                saida = _blob_text(conn, out_h)
            campo = saida if shape.anchor_side == "output" else entrada
            if claim.anchor in campo:
                return o.VERIFIED, f"obs:{oid}"

    if esgotado or len(rows) > limit:
        # A sessao nao coube no orcamento: o que nao foi lido pode conter a
        # prova, e ausencia so e POSITIVA quando olhamos tudo.
        return o.UNSUPPORTED, ""
    return o.CONTRADICTED, ""
