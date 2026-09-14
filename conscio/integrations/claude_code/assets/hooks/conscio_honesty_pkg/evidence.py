"""Porta 3 do funil: o predicado deterministico.

So contesta quando a ausencia e POSITIVA -- a sessao inteira coube na consulta
e a ancora nao aparece em nenhuma chamada. Captura vazia, sessao alheia ou
janela saturada significam "nao consegui olhar", e isso e UNSUPPORTED, nunca
acusacao: um gate que confunde as duas teria travado todo turno durante a
v4.0.0, quando a captura estava inerte.

Zero imports do resto do Conscio: este modulo e vendorizado para junto do hook.
"""

from __future__ import annotations

import zlib

from . import verdicts as o
from .classes import Claim

#: Teto de linhas examinadas por afirmacao. Ler 200 observacoes com blobs custou
#: 186ms numa sessao real de 1925 -- o hook roda todo turno, entao a janela e
#: teto de custo (R2), nao preferencia.
WINDOW = 200


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


def _mentions(conn, in_h, out_h, anchor: str) -> bool:
    """Le os blobs preguicosamente: para na primeira metade que casar, em vez
    de materializar entrada e saida toda vez."""
    for h in (in_h, out_h):
        if anchor in _blob_text(conn, h):
            return True
    return False


def check(conn, claim: Claim, session_id: str,
          limit: int = WINDOW) -> tuple[str, str]:
    """Devolve ``(outcome, evidence_pointer)``.

    O ponteiro so existe quando VERIFIED: contestacao nao aponta para prova,
    aponta para ausencia -- e ausencia nao tem endereco.

    Consulta ``limit + 1`` para decidir a saturacao com um retrato unico. Um
    ``COUNT`` separado teria TOCTOU: o obsstore escreve durante o turno, entao
    uma linha inserida entre as duas queries daria saturacao falsa de forma
    intermitente.
    """
    rows = conn.execute(
        "SELECT id, in_h, out_h FROM observations WHERE session_id=?"
        " ORDER BY id DESC LIMIT ?", (session_id, limit + 1)).fetchall()
    if not rows:
        return o.UNSUPPORTED, ""          # nao consegui olhar
    # Desempacota por posicao: a conexao pode chegar com row_factory=Row (a do
    # engine) ou sem (a do obsstore.connect), e este modulo nao e dono dela.
    for oid, in_h, out_h in rows:
        if _mentions(conn, in_h, out_h, claim.anchor):
            return o.VERIFIED, f"obs:{oid}"
    if len(rows) > limit:
        # A sessao nao coube: o que nao foi lido pode conter a prova.
        return o.UNSUPPORTED, ""
    return o.CONTRADICTED, ""
