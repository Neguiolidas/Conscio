"""Porta 3 do funil: o predicado deterministico.

So contesta quando a ausencia e POSITIVA -- a sessao inteira coube na consulta
e a ancora nao aparece em nenhuma chamada. Captura vazia, sessao alheia ou
janela saturada significam "nao consegui olhar", e isso e UNSUPPORTED, nunca
acusacao: um gate que confunde as duas teria travado todo turno durante a
v4.0.0, quando a captura estava inerte.

Zero imports do resto do Conscio: este modulo e vendorizado para junto do hook.
"""

from __future__ import annotations

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
#: Medido no obs.db vivo, pior caso (ancora ausente, scan completo da sessao de
#: 1466 observacoes Bash) -- e o numero DEPENDE DO CACHE, entao vao os dois:
#: ~809ms com cache frio, ~46-91ms quente, medido repetindo a mesma varredura.
#: Em producao o hook nasce num processo novo a cada turno, entao o frio e o
#: numero que importa e 500ms trunca essa sessao pela metade.
#:
#: Truncar resolve UNSUPPORTED, nunca acusacao: ausencia so e POSITIVA quando
#: olhamos tudo. Um orcamento apertado perde deteccao; nunca inventa uma.
BUDGET_MS = 500


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


#: Saida que denuncia ato que nao aconteceu. O harness ja registra chamada
#: falha sob nome proprio (``Bash!failed``), entao isto cobre o resto: comando
#: que sai 0 e ainda assim nao fez o ato.
_FAILED = re.compile(
    r"fatal:|^error:|Traceback \(most recent call last\)|\brejected\b"
    r"|command not found|No such file or directory", re.IGNORECASE | re.MULTILINE)


def _is_failure(output: str) -> bool:
    return bool(_FAILED.search(output or ""))


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
    tools = sorted({t for shape in cls.acts for t in shape.tools})
    placeholders = ",".join("?" * len(tools))
    rows = conn.execute(
        "SELECT id, tool, in_h, out_h FROM observations"
        f" WHERE session_id=? AND tool IN ({placeholders})"
        " ORDER BY id DESC LIMIT ?",
        (session_id, *tools, limit + 1)).fetchall()
    if not rows:
        return o.UNSUPPORTED, ""          # nao consegui olhar

    esgotado = False
    for oid, tool, in_h, out_h in rows:
        if time.monotonic() > deadline:
            esgotado = True               # nao vi o resto: nao posso acusar
            break
        shapes = [sh for sh in cls.acts if tool in sh.tools]
        if not shapes:
            continue
        entrada = _blob_text(conn, in_h)
        saida = None
        for shape in shapes:
            if shape.act and not re.search(shape.act, entrada, re.IGNORECASE):
                continue                  # a ferramenta serve, o ato nao e este
            if saida is None:
                saida = _blob_text(conn, out_h)
            if _is_failure(saida):
                continue                  # o ato foi tentado e falhou
            campo = saida if shape.anchor_side == "output" else entrada
            if claim.anchor in campo:
                return o.VERIFIED, f"obs:{oid}"

    if esgotado or len(rows) > limit:
        # A sessao nao coube no orcamento: o que nao foi lido pode conter a
        # prova, e ausencia so e POSITIVA quando olhamos tudo.
        return o.UNSUPPORTED, ""
    return o.CONTRADICTED, ""
