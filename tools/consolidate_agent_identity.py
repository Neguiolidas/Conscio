#!/usr/bin/env python3
"""consolidate_agent_identity.py — reconcile duplicate agent identities in Conscio.

Some older installs end up with the SAME agent registered under two instance
ids (one from a pre-migration `runtime/instance.json`, one from the reactor's
`--self-id`). The isolation guard (`spaces.space_is_cross_agent`) then blocks
the legit agent from writing its own space because the space record differs.

This tool picks ONE canonical id and rewrites it everywhere:
- the space's `instance.json` (memory identity)
- `liaison.db` `agents` table (dedupe / delete orphan)
- `liaison.db` `messages` table (`from_instance` / `to_instance`)
- `liaison.db` `watcher_state` table (cursor owner)
- `tailscale_relay_service.py` TRUSTED_PEERS (optional)

Idempotent: safe to run repeatedly; backs everything up before mutating.

Usage:
  consolidate_agent_identity.py OLD_ID NEW_ID \
      --space /path/to/instances/<slug>          # space's instance.json
      --liaison /path/to/liaison.db              # mailbox
      --relay-script /path/to/.../tailscale_relay_service.py  # optional

Exit 0 = success. Prints a summary of what changed.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def _backup(path: Path) -> Path:
    p = Path(path)
    bak = p.with_name(p.name + f".bak-consolidate-{datetime.now():%Y%m%d_%H%M%S}")
    if not bak.exists():
        if str(path).endswith(".db"):
            src = sqlite3.connect(str(p))
            dst = sqlite3.connect(str(bak))
            src.backup(dst)
            dst.close(); src.close()
        else:
            shutil.copy2(p, bak)
    return bak


def _migrate_space(space: Path, old: str, new: str) -> list[str]:
    ip = space / "instance.json"
    if not ip.exists():
        return ["space instance.json ausente (pulado)"]
    _backup(ip)
    d = json.loads(ip.read_text())
    r = []
    if d.get("instance_id") == old:
        d["instance_id"] = new
        d["label"] = d.get("label", "").replace(old[:8], new[:8])
        ip.write_text(json.dumps(d, indent=2, sort_keys=True) + "\n")
        r.append(f"instance.json migrado {old[:8]}->{new[:8]}")
    else:
        r.append("instance.json ja no id certo")
    return r


def _migrate_liaison(db: Path, old: str, new: str) -> list[str]:
    _backup(db)
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    out = []
    try:
        # agents: dedupe. Se NEW ja existe, apaga OLD (orfa). Senão renomeia.
        new_exists = con.execute("SELECT 1 FROM agents WHERE instance_id=?", (new,)).fetchone()
        old_exists = con.execute("SELECT 1 FROM agents WHERE instance_id=?", (old,)).fetchone()
        if old_exists:
            if new_exists:
                con.execute("DELETE FROM agents WHERE instance_id=?", (old,))
                out.append("agents: linha orfã removida")
            else:
                con.execute("UPDATE agents SET instance_id=? WHERE instance_id=?", (new, old))
                out.append("agents: renomeada")
        # messages: sem unique; realoca. Colunas fixas (nunca derivadas de
        # input) — SQL estático, valores via parâmetros.
        n = con.execute(
            "UPDATE messages SET from_instance=? WHERE from_instance=?", (new, old)).rowcount
        if n:
            out.append(f"messages.from_instance: {n} realocadas")
        n = con.execute(
            "UPDATE messages SET to_instance=? WHERE to_instance=?", (new, old)).rowcount
        if n:
            out.append(f"messages.to_instance: {n} realocadas")
        # watcher_state: tem UNIQUE. Vida do cursor órfão.
        w_new = con.execute("SELECT 1 FROM watcher_state WHERE peer=?", (new,)).fetchone()
        w_old = con.execute("SELECT 1 FROM watcher_state WHERE peer=?", (old,)).fetchone()
        if w_old:
            if w_new:
                con.execute("DELETE FROM watcher_state WHERE peer=?", (old,))
                out.append("watcher_state: órfão removido (novo já tem cursor)")
            else:
                con.execute("UPDATE watcher_state SET peer=? WHERE peer=?", (new, old))
                out.append("watcher_state: cursor renomeado")
        con.commit()
    finally:
        con.close()
    return out or ["liaison: nada a alterar"]


def _migrate_relay_script(script: Path, old: str, new: str) -> list[str]:
    if not script or not script.exists():
        return []
    _backup(script)
    src = script.read_text()
    if old not in src:
        return ["relay-script: id não aparece (pulado)"]
    script.write_text(src.replace(old, new))
    return ["relay-script: referência substituída"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old_id", help="instance id duplicado/antigo a remover")
    ap.add_argument("new_id", help="instance id canônico a manter")
    ap.add_argument("--space", default="", help="path do espaço (instances/<slug>)")
    ap.add_argument("--liaison", default="", help="path do liaison.db")
    ap.add_argument("--relay-script", default="", help="script de relay opcional")
    args = ap.parse_args()

    if not args.space and not args.liaison:
        print("erro: forneca pelo menos --space ou --liaison", file=sys.stderr)
        return 2

    report = []
    if args.space:
        report += _migrate_space(Path(args.space).expanduser(), args.old_id, args.new_id)
    if args.liaison:
        report += _migrate_liaison(Path(args.liaison).expanduser(), args.old_id, args.new_id)
    if args.relay_script:
        report += _migrate_relay_script(Path(args.relay_script).expanduser(),
                                        args.old_id, args.new_id)

    print("== Consolidacao de identidade ==")
    print(f"   {args.old_id[:8]}... -> {args.new_id[:8]}...")
    for line in report:
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())