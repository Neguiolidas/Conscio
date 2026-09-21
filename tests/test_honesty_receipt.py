"""v4.6.6 A1: o veredito registra POR QUE e o que e.

check() ja distinguia seis situacoes e colapsava todas em (UNSUPPORTED, "").
O motivo existia na memoria da funcao e morria no return -- e sem ele nao ha
auditoria por classe, que e o criterio (d) da saida da sombra.
"""
import sqlite3

import conscio.honesty.evidence as evidence
from conscio.honesty import verdicts as o
from conscio.honesty.classes import Claim
from conscio.honesty.evidence import check

_SCHEMA = """
CREATE TABLE observations (id INTEGER PRIMARY KEY AUTOINCREMENT, tool TEXT,
  project TEXT DEFAULT '', agent TEXT DEFAULT '', session_id TEXT, ts TEXT,
  in_h TEXT, out_h TEXT, in_n INT DEFAULT 0, out_n INT DEFAULT 0);
CREATE TABLE blobs (h TEXT PRIMARY KEY, z BLOB, n INT);
"""


def _conn(tmp_path):
    c = sqlite3.connect(tmp_path / "obs.db")
    c.executescript(_SCHEMA)
    return c


def _obs(c, entrada, saida, tool="Bash", session="s1"):
    import zlib
    ids = []
    for texto, col in ((entrada, "in_h"), (saida, "out_h")):
        h = f"h{abs(hash(texto)) % 10**9}"
        c.execute("INSERT OR IGNORE INTO blobs (h, z, n) VALUES (?,?,?)",
                  (h, zlib.compress(texto.encode()), len(texto)))
        ids.append(h)
    cur = c.execute("INSERT INTO observations (tool, session_id, ts, in_h,"
                    " out_h) VALUES (?,?,'t',?,?)", (tool, session, *ids))
    return cur.lastrowid


def _claim(anchor="abc1234"):
    return Claim("commit", anchor, (0, 0))


def test_no_observation_says_so(tmp_path):
    got, receipt = check(_conn(tmp_path), _claim(), "s1")
    assert got == o.UNSUPPORTED
    assert receipt == "why:no_obs"


def test_a_saturated_window_says_so(tmp_path):
    c = _conn(tmp_path)
    _obs(c, "git commit", "[abc1234] done")
    for i in range(8):
        _obs(c, f"cmd {i}", f"out {i}")
    got, receipt = check(c, _claim(), "s1", limit=5)
    assert got == o.UNSUPPORTED
    assert receipt == "why:window"


def test_a_verified_claim_still_points_at_the_observation(tmp_path):
    c = _conn(tmp_path)
    oid = _obs(c, "git commit -m x", "[abc1234] done")
    assert check(c, _claim(), "s1") == (o.VERIFIED, f"obs:{oid}")


def test_a_contradiction_carries_how_wide_the_absence_was(tmp_path):
    c = _conn(tmp_path)
    for i in range(3):
        _obs(c, f"git commit -m x{i}", f"[dead{i}] done")
    got, receipt = check(c, _claim(), "s1")
    assert got == o.CONTRADICTED
    assert receipt == "absent/scanned=3"


def test_an_unknown_class_says_so(tmp_path):
    got, receipt = check(_conn(tmp_path), Claim("inexistente", "x", (0, 0)),
                         "s1")
    assert got == o.UNSUPPORTED
    assert receipt == "why:no_act"


def test_two_simultaneous_reasons_produce_a_stable_receipt(tmp_path):
    """A ordem do vocabulario e CONTRATO. Aqui valem dois motivos ao mesmo
    tempo -- janela saturada e payload ilegivel -- e 'window' vence por ser o
    mais estrutural. Sem ordem fixa, a mesma situacao daria recibos diferentes
    em execucoes diferentes."""
    c = _conn(tmp_path)
    _obs(c, '{"foo": "bar"}', "sem chave de comando")     # ilegivel
    for i in range(6):
        _obs(c, f"cmd {i}", f"out {i}")
    got, receipt = check(c, _claim(), "s1", limit=5)
    assert got == o.UNSUPPORTED
    assert receipt == "why:window"


def test_an_exhausted_budget_alone_says_budget(tmp_path, monkeypatch):
    """Orcamento estourado sem janela saturada: o recibo nomeia budget.

    BUDGET_MS e constante de modulo lida DENTRO de check(); fixa-la em 0
    esgota o orcamento no primeiro tick do laco. O estouro e deterministico
    -- nao depende de maquina lenta nem de carga --, e a janela fica
    folgada (1 linha, limit 5) para isolar o motivo.
    """
    monkeypatch.setattr(evidence, "BUDGET_MS", 0)
    c = _conn(tmp_path)
    _obs(c, "git commit", "out")
    got, receipt = check(c, _claim(), "s1", limit=5)
    assert got == o.UNSUPPORTED
    assert receipt == "why:budget"


def test_window_beats_clock_dependent_budget_when_both_hold(tmp_path,
                                                            monkeypatch):
    """O contrato de determinismo: dois motivos verdadeiros ao mesmo tempo
    NAO podem dar recibos diferentes em execucoes diferentes. `window` e
    fato ESTATICO do banco; `budget` depende do RELOGIO -- por isso window
    vence quando ambos valem. O orcamento e esgotado deterministicamente
    (BUDGET_MS=0), nao por carga: a mesma situacao que dava why:budget na
    maquina lenta e why:window na rapida agora so tem um recibo possivel.
    """
    monkeypatch.setattr(evidence, "BUDGET_MS", 0)
    c = _conn(tmp_path)
    _obs(c, "git commit", "out")
    for i in range(6):
        _obs(c, f"cmd {i}", f"out {i}")
    receipts = {check(c, _claim(), "s1", limit=5)[1] for _ in range(3)}
    assert receipts == {"why:window"}


def test_the_receipt_never_changes_the_outcome(tmp_path):
    """Invariante: o recibo e registro, nunca veredito."""
    c = _conn(tmp_path)
    oid = _obs(c, "git commit -m x", "[abc1234] done")
    outcome, receipt = check(c, _claim(), "s1")
    assert outcome == o.VERIFIED and receipt == f"obs:{oid}"
