from __future__ import annotations

import errno
import json
import os
import shutil
import sys
import time
from collections.abc import Mapping
from pathlib import Path

from conscio.installer.durable import (
    _write_json_atomic,
    plugin_data_roots,
    plugin_pointer_path,
    remove_migration_lock,
    remove_refused_marker,
    write_migration_lock,
    write_pointer_atomic,
)
from conscio.installer.spaces import slugify, space_dir
from conscio.mcp.host_identity import derive_host_identity

UNIT_MAP = {
    "claude-code": "conscio-relay-reactor-claude",
    "claude": "conscio-relay-reactor-claude",
    "gemini": "conscio-relay-reactor-gemini",
    "antigravity": "conscio-relay-reactor-gemini",
    "freebuff": "conscio-relay-reactor-freebuff",
    "zcode": "conscio-relay-reactor-zcode",
    "default": "conscio-relay-reactor",
}


def _find_active_legacy_procs(legacy_path: Path, proc_root: Path) -> list[dict]:
    legacy_str = str(legacy_path.resolve())
    legacy_raw = str(legacy_path)
    active = []
    self_pid = os.getpid()
    if not proc_root.exists():
        return []
    for p_entry in proc_root.iterdir():
        if not p_entry.name.isdigit():
            continue
        try:
            pid = int(p_entry.name)
        except ValueError:
            continue
        if pid == self_pid:
            continue
        cmdline_file = p_entry / "cmdline"
        if not cmdline_file.is_file():
            continue
        try:
            raw = cmdline_file.read_bytes()
            args = [a for a in raw.decode("utf-8", errors="replace").split(chr(0)) if a]
            cmdline_str = " ".join(args)
            if legacy_str in cmdline_str or legacy_raw in cmdline_str:
                active.append({"pid": pid, "cmdline": cmdline_str, "args": args})
        except Exception:
            continue
    return active


def migrate_space_cmd(
    slug: str | None = None,
    quiet_minutes: int = 10,
    proc_root: Path = Path("/proc"),
    env: Mapping[str, str] | None = None,
) -> int:
    """Execute conscio space migrate following Spec Section 3 strict 8 atomic steps."""
    if env is None:
        env = os.environ

    # Step 0: Slug determination
    if not slug:
        if "CLAUDE_PLUGIN_DATA" in env and "ZCODE_PLUGIN_DATA" not in env:
            slug = "claude-code"
        elif "ZCODE_PLUGIN_DATA" in env:
            slug = "zcode"
        else:
            host_ident = derive_host_identity(env)
            if host_ident.runtime:
                slug = slugify(host_ident.runtime)

    if not slug:
        print("conscio space migrate: cannot determine space slug. Pass --slug <name> explicitly.", file=sys.stderr)
        return 3

    slug = slugify(slug)
    durable_target = space_dir(slug)

    # Locate legacy_path and plugin_dir
    roots = plugin_data_roots(env)
    legacy_path: Path | None = None
    plugin_dir: Path | None = None

    if roots:
        for r in roots:
            if (r / "space").is_dir():
                legacy_path = r / "space"
                plugin_dir = r
                break
            elif (r / "instance.json").is_file():
                legacy_path = r
                plugin_dir = r
                break
            else:
                legacy_path = r / "space"
                plugin_dir = r
                break

    if legacy_path is None:
        if slug in ("claude", "claude-code"):
            def_root = Path.home() / ".claude" / "plugins" / "data" / "conscio@conscio"
            plugin_dir = def_root
            legacy_path = def_root / "space"
        elif slug == "zcode":
            def_root = Path.home() / ".zcode" / "cli" / "plugins" / "data" / "conscio@conscio"
            plugin_dir = def_root
            legacy_path = def_root / "space"

    if legacy_path is None or (not legacy_path.exists() and not durable_target.exists()):
        print(f"conscio space migrate: no legacy space found for slug {slug}.", file=sys.stderr)
        return 3

    # Check if already migrated
    pointer_file = plugin_dir / "space-pointer.json" if plugin_dir else None
    if pointer_file and pointer_file.exists():
        try:
            data = json.loads(pointer_file.read_text(encoding="utf-8"))
            if data.get("target") == str(durable_target) and durable_target.exists():
                if legacy_path.exists() and not any(legacy_path.iterdir()):
                    print(f"nothing to migrate: space for {slug} is already at {durable_target}")
                    return 0
        except Exception:
            pass

    if legacy_path.exists() and not any(legacy_path.iterdir()) and durable_target.exists():
        print(f"nothing to migrate: space for {slug} is already at {durable_target}")
        return 0

    # Gate 1: Process-zero check
    active_procs = _find_active_legacy_procs(legacy_path, proc_root)
    if active_procs:
        unit = UNIT_MAP.get(slug, f"conscio-relay-reactor-{slug}")
        print(f"migration deferred: {len(active_procs)} active process(es) on the legacy path:", file=sys.stderr)
        for proc in active_procs:
            cmdline = proc["cmdline"]
            cmd_trunc = cmdline[:60] + "..." if len(cmdline) > 60 else cmdline
            print(f"  PID {proc['pid']}: {cmd_trunc} (systemctl --user stop {unit})", file=sys.stderr)
        return 2

    # Gate 2: Mtime-quieto check
    if quiet_minutes > 0 and legacy_path.exists():
        cutoff = time.time() - (quiet_minutes * 60)
        recent_mod = []
        for root_dir, _, files in os.walk(legacy_path):
            for f in files:
                fpath = Path(root_dir) / f
                try:
                    if fpath.stat().st_mtime > cutoff:
                        recent_mod.append(fpath)
                except OSError:
                    pass
        if recent_mod:
            print(
                f"migration deferred: files in legacy path were modified in the last {quiet_minutes} minute(s)",
                file=sys.stderr,
            )
            return 2

    # Step 1: Criar lock .migrating-<slug>
    write_migration_lock(slug)

    # Step 2: Backup rotativo de 2 gerações
    base_dir = durable_target.parent.parent
    backups_dir = base_dir / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    backup_dest = backups_dir / f"pre-migrate-{ts}"
    backup_dest.mkdir(parents=True, exist_ok=True)

    if legacy_path.exists():
        for item in legacy_path.iterdir():
            if item.is_dir():
                shutil.copytree(item, backup_dest / item.name)
            else:
                shutil.copy2(item, backup_dest / item.name)

    existing_backups = []
    for b in backups_dir.iterdir():
        if b.is_dir() and b.name.startswith("pre-migrate-"):
            existing_backups.append(b)
    existing_backups.sort(key=lambda p: p.name)
    while len(existing_backups) > 2:
        old_b = existing_backups.pop(0)
        shutil.rmtree(old_b)

    # Step 3: Mover CONTEÚDO de space/ para durable_target
    durable_target.mkdir(parents=True, exist_ok=True)
    if legacy_path.exists():
        for item in list(legacy_path.iterdir()):
            dest = durable_target / item.name
            try:
                os.rename(str(item), str(dest))
            except OSError as exc:
                if exc.errno == errno.EXDEV:
                    if item.is_dir():
                        shutil.copytree(item, dest, dirs_exist_ok=True)
                        shutil.rmtree(item)
                    else:
                        shutil.copy2(item, dest)
                        if dest.stat().st_size != item.stat().st_size:
                            raise RuntimeError(f"cross-fs verification failed for {item}")
                        item.unlink()
                else:
                    raise
        legacy_path.mkdir(parents=True, exist_ok=True)

    # Step 4: Gravar lápide migrated-from.json na raiz durável
    tombstone_payload = {
        "schema": 1,
        "origin": str(legacy_path.resolve()),
        "runtime": slug,
        "slug": slug,
        "migrated_ts": time.time(),
    }
    _write_json_atomic(durable_target / "migrated-from.json", tombstone_payload)

    # Step 5: Gravar ponteiro space-pointer.json na pasta do plugin
    pointer_path = plugin_pointer_path(legacy_path, env)
    write_pointer_atomic(pointer_path, target=durable_target, runtime=slug, slug=slug)

    # Step 6: Apagar space-refused.json se existir
    remove_refused_marker(legacy_path, env)

    # Step 7: Remover lock .migrating-<slug>
    remove_migration_lock(slug)

    # Step 8: Imprimir units systemd regenerados apontando para o durável no stdout
    db_path = durable_target / "liaison.db"
    self_id = "default"
    inst_json = durable_target / "instance.json"
    if inst_json.exists():
        try:
            idata = json.loads(inst_json.read_text(encoding="utf-8"))
            self_id = idata.get("instance_id") or self_id
        except Exception:
            pass

    unit_content = (
        "[Unit]\n"
        f"Description=Conscio relay reactor ({slug})\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        f"ExecStart={sys.executable} -u -m conscio.liaison.reactor --liaison-db {db_path} --self-id {self_id} --interval 1.0\n"
        "Restart=always\n"
        "RestartSec=5\n"
        "Environment=CONSCIO_NOTIFY_CMD=\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    sys.stdout.write(unit_content)
    unit_name = UNIT_MAP.get(slug, f"conscio-relay-reactor-{slug}")
    print(f"# save as ~/.config/systemd/user/{unit_name}.service", file=sys.stderr)
    print(f"# then: systemctl --user daemon-reload && systemctl --user enable --now {unit_name}", file=sys.stderr)

    return 0
