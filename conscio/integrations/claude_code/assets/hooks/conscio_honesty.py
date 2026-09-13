#!/usr/bin/env python3
"""Stop hook do laco de honestidade (v4.6 E2) -- MODO SOMBRA.

Grava desfecho e NAO contesta ninguem: nenhum caminho aqui escreve em stdout.
A contestacao so liga depois do portao de corpus (C5b).

O pacote ``conscio`` NAO esta instalado ao lado do plugin -- ele vem do PyPI
por uvx. Tudo que este hook usa e vendorizado pelo materialize.py e carregado
por caminho. Toda falha sai com 0: hook que derruba turno e pior que hook que
nao roda.
"""

import argparse
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path


def _load_module(path: Path, name: str):
    """Carrega um modulo vendorizado por caminho.

    ``spec`` e ``spec.loader`` sao None quando o arquivo nao existe ou nao e
    carregavel -- exatamente o caso em que a vendorizacao falhou. Falhar aqui
    com ImportError nomeando o caminho e melhor que um AttributeError sobre
    None tres linhas adiante.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"vendored module not loadable: {name} at {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--storage", required=True)
    ap.add_argument("--honesty", required=True,
                    help="diretorio do pacote honesty (vendorizado)")
    ap.add_argument("--obsstore", required=True,
                    help="caminho do MODULO obsstore.py, nao do banco")
    args = ap.parse_args(argv)

    payload = json.loads(sys.stdin.read() or "{}")
    if payload.get("stop_hook_active"):
        return 0                       # turno nascido de bloqueio: so observa
    text = payload.get("last_assistant_message") or ""
    session = payload.get("session_id") or ""
    if not text or not session:
        return 0

    honesty_dir = Path(args.honesty).expanduser().resolve()
    sys.path.insert(0, str(honesty_dir.parent))
    pkg = honesty_dir.name
    recognise = __import__(f"{pkg}.recognizer", fromlist=["recognise"]).recognise
    ClaimStore = __import__(f"{pkg}.store", fromlist=["ClaimStore"]).ClaimStore
    expire_stale = __import__(f"{pkg}.sweep", fromlist=["expire_stale"]).expire_stale
    obsstore = _load_module(Path(args.obsstore).expanduser().resolve(),
                            "conscio_obsstore_vendored")

    storage = Path(args.storage).expanduser()
    storage.mkdir(parents=True, exist_ok=True)
    db_path = storage / "conscio.db"

    findings = recognise(obsstore.connect(storage / "obs.db"), text, session)
    if findings:
        store = ClaimStore(db_path)
        try:
            for f in findings:
                store.record(session, f.cls_name, f.anchor, f.outcome,
                             f.evidence)
        finally:
            store.close()

    # Emenda A1: a varredura de expiracao roda AQUI, no unico gatilho que ja
    # acontece todo turno. Sem isto, pendencia vencida so seria derivada na
    # leitura e nunca ficaria registrada -- e expire_stale nao teria chamador
    # nenhum em producao. So abre o banco se ele ja existe: um espaco que nunca
    # gravou acao nao ganha um conscio.db vazio por causa do hook.
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        try:
            expire_stale(conn)
        finally:
            conn.close()
    return 0                           # sombra: stdout permanece vazio


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        sys.exit(0)                    # falha aberta e silenciosa, por desenho
