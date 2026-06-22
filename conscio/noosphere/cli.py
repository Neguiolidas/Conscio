# conscio/noosphere/cli.py
"""`conscio noosphere ...` — publish / import / list / show / id.

Engine-free; argparse only. Wired into the top-level conscio CLI via an
early route (see conscio/cli.py), mirroring the bench/daemon pattern."""
from __future__ import annotations

import argparse
import json

from . import (audit, catalog, identity, importer, publish, quarantine,
               record_publish)
from .paths import quarantine_db_path, resolve_noosphere, resolve_storage


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="conscio noosphere",
        description="Share locally-proven skills across same-host instances.")
    sub = p.add_subparsers(dest="cmd", metavar="<cmd>")

    def _storage_arg(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--storage", default="",
                        help="instance storage dir (default: $HERMES_HOME/consciousness)")

    def _noosphere_arg(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--noosphere", default="",
                        help="shared catalog path (default: $HERMES_HOME/noosphere.db)")

    for name, helptext in (
            ("publish", "publish this instance's proven skills"),
            ("import", "import foreign skills into quarantine")):
        sp = sub.add_parser(name, help=helptext)
        _storage_arg(sp)
        _noosphere_arg(sp)

    pr = sub.add_parser("publish-record",
                        help="publish this instance's behavioral record")
    _storage_arg(pr)
    _noosphere_arg(pr)

    pa = sub.add_parser("audit",
                        help="audit peers' behavioral records (read-only)")
    _storage_arg(pa)
    _noosphere_arg(pa)
    pa.add_argument("--instance", default="",
                    help="audit only this origin_instance_id")

    pl = sub.add_parser("list", help="list quarantined imports (or --catalog)")
    _storage_arg(pl)
    _noosphere_arg(pl)
    pl.add_argument("--catalog", action="store_true",
                    help="list the shared catalog instead of local quarantine")

    ps = sub.add_parser("show", help="show one quarantine row or catalog entry")
    _storage_arg(ps)
    _noosphere_arg(ps)
    ps.add_argument("--quarantine", metavar="ROWID",
                    help="show local quarantine row by id")
    ps.add_argument("--catalog", nargs=2, metavar=("ORIGIN_ID", "SHA256"),
                    help="show shared catalog entry by origin + content hash")

    pid = sub.add_parser("id", help="show or rename this instance's identity")
    _storage_arg(pid)
    pid.add_argument("--set-label", default=None, help="set a human label")
    return p


def _cmd_publish(args) -> int:
    res = publish.run(storage=args.storage, noosphere=args.noosphere)
    print(f"published {res.published} (skipped {res.skipped} already present, "
          f"{res.considered} proven considered, {res.malformed} malformed)")
    return 0


def _cmd_import(args) -> int:
    res = importer.run(storage=args.storage, noosphere=args.noosphere)
    print(f"quarantined {res.quarantined}, rejected {res.rejected}, "
          f"skipped {res.skipped} already present")
    return 0


def _cmd_list(args) -> int:
    if args.catalog:
        for cr in catalog.read_all(resolve_noosphere(args.noosphere)):
            print(f"{cr.origin_label}  {cr.content_sha256[:12]}  {cr.goal_text}")
    else:
        qdb = quarantine_db_path(resolve_storage(args.storage))
        for qr in quarantine.list_rows(qdb):
            print(f"#{qr.id}  {qr.origin_label}  [{qr.import_status}/"
                  f"{qr.revalidation_result}]  {qr.goal_text}")
    return 0


def _cmd_show(args) -> int:
    if args.quarantine is not None:
        qdb = quarantine_db_path(resolve_storage(args.storage))
        qrow = quarantine.get(qdb, int(args.quarantine))
        if qrow is None:
            print("not found")
            return 1
        showable = qrow.revalidation_result in ("ok", "fp_mismatch", "malformed")
        print(json.dumps({
            "id": qrow.id, "origin_instance_id": qrow.origin_instance_id,
            "origin_label": qrow.origin_label, "imported_ts": qrow.imported_ts,
            "import_status": qrow.import_status,
            "revalidation_result": qrow.revalidation_result,
            "revalidation_error": qrow.revalidation_error,
            "goal_text": qrow.goal_text,
            "artifact": json.loads(qrow.artifact_json.decode("utf-8"))
            if showable else "<unparseable>"}, indent=2))
        return 0
    if args.catalog is not None:
        origin, sha = args.catalog
        crow = catalog.get(resolve_noosphere(args.noosphere), origin, sha)
        if crow is None:
            print("not found")
            return 1
        print(json.dumps({
            "origin_instance_id": crow.origin_instance_id,
            "origin_label": crow.origin_label, "published_ts": crow.published_ts,
            "content_sha256": crow.content_sha256, "goal_text": crow.goal_text,
            "artifact": json.loads(crow.artifact_json.decode("utf-8"))}, indent=2))
        return 0
    print("show requires --quarantine ROWID or --catalog ORIGIN_ID SHA256")
    return 2


def _cmd_id(args) -> int:
    storage = resolve_storage(args.storage)
    ident = (identity.set_label(storage, args.set_label)
             if args.set_label is not None
             else identity.load_or_create(storage))
    print(f"{ident.instance_id}  {ident.label}")
    return 0


def _cmd_publish_record(args) -> int:
    res = record_publish.run(storage=args.storage, noosphere=args.noosphere)
    print(f"published {res.published} (skipped {res.skipped} already present, "
          f"{res.entries} entries)")
    return 0


def _cmd_audit(args) -> int:
    rep = audit.run(storage=args.storage, noosphere=args.noosphere,
                    instance=(args.instance or None))
    if not rep.peers and not rep.rejected_bundles:
        print("no peer records found")
        return 0
    for p in rep.peers:
        max_level = max((t.trust_level for t in p.tools), default=1)
        print(f"{p.origin_label} [{p.origin_instance_id[:8]}]  {p.verdict}  "
              f"acc {round(100 * p.overall_accuracy)}% over {p.attempts}  "
              f"L{max_level}  quarantines {len(p.quarantined_goals)}  "
              f"RED {p.executed_after_fail} / YELLOW {p.executed_unaudited}")
    if rep.rejected_bundles:
        print("rejected bundles:")
        for origin, label, reason in rep.rejected_bundles:
            print(f"  {label} [{origin[:8]}]  {reason}")
    return 0


def main(argv: list[str]) -> int:
    parser = _build_parser()
    try:                                  # argparse calls sys.exit on bad args
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 0
    dispatch = {"publish": _cmd_publish, "import": _cmd_import,
                "list": _cmd_list, "show": _cmd_show, "id": _cmd_id,
                "publish-record": _cmd_publish_record, "audit": _cmd_audit}
    handler = dispatch.get(args.cmd)
    if handler is None:
        parser.print_help()
        return 2
    try:
        return handler(args)
    except (ValueError, identity.NoosphereIdentityError) as exc:
        # ValueError covers bad rowid (int()), json.JSONDecodeError and
        # UnicodeDecodeError (both subclass ValueError) and label validation.
        print(f"error: {exc}")
        return 1
