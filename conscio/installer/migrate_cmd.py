from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import sys
import time
from collections.abc import Mapping
from pathlib import Path

from conscio.installer.durable import (
    _write_json_atomic,
    known_plugin_data_dirs,
    migration_lock_path,
    plugin_pointer_path,
    remove_migration_lock,
    remove_refused_marker,
    resolve_space,
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


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _are_files_identical(src: Path, dst: Path) -> bool:
    if not (src.is_file() and dst.is_file()):
        return False
    if src.stat().st_size != dst.stat().st_size:
        return False
    return _sha256(src) == _sha256(dst)


def _are_trees_identical(src: Path, dst: Path) -> bool:
    if not (src.is_dir() and dst.is_dir()):
        return False
    src_files = {}
    for root, _, files in os.walk(src):
        rel = Path(root).relative_to(src)
        for f in files:
            p = Path(root) / f
            src_files[rel / f] = (p.stat().st_size, _sha256(p))
    dst_files = {}
    for root, _, files in os.walk(dst):
        rel = Path(root).relative_to(dst)
        for f in files:
            p = Path(root) / f
            dst_files[rel / f] = (p.stat().st_size, _sha256(p))
    return src_files == dst_files


def _verify_trees_identical(src: Path, dst: Path) -> None:
    if not _are_trees_identical(src, dst):
        raise RuntimeError(f"cross-fs verification failed between {src} and {dst}")


def _is_pid_alive(pid: int | None, proc_root: Path = Path("/proc")) -> bool:
    if pid is None or pid <= 0:
        return False
    if proc_root != Path("/proc"):
        return (proc_root / str(pid)).is_dir()
    if (proc_root / str(pid)).is_dir():
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError as err:
        return err.errno == errno.EPERM


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


def _plugin_dir_matches_slug(p: Path, slug: str, env: Mapping[str, str]) -> bool:
    slug_norm = slugify(slug)
    p_str = str(p)
    claude_env = env.get("CLAUDE_PLUGIN_DATA")
    if claude_env:
        try:
            if Path(claude_env).expanduser().resolve() == p.resolve():
                return slug_norm in ("claude", "claude-code")
        except Exception:
            pass
    zcode_env = env.get("ZCODE_PLUGIN_DATA")
    if zcode_env:
        try:
            if Path(zcode_env).expanduser().resolve() == p.resolve():
                return slug_norm == "zcode"
        except Exception:
            pass
    if slug_norm in ("claude", "claude-code") and ("claude" in p_str or "conscio-conscio" in p_str):
        return True
    return bool(slug_norm == "zcode" and "zcode" in p_str)


def migrate_space_cmd(
    slug: str | None = None,
    quiet_minutes: int = 10,
    proc_root: Path = Path("/proc"),
    env: Mapping[str, str] | None = None,
) -> int:
    """Execute conscio space migrate following Spec Section 3 strict 8 atomic steps."""
    if env is None:
        env = os.environ

    # Step 0: Discovery of plugin folders & slug derivation
    known_dirs = known_plugin_data_dirs(env=env, only_existing=True)

    candidates_with_data: list[tuple[Path, Path]] = []
    for p in known_dirs:
        cand_space = p / "space"
        if cand_space.is_dir() and any(cand_space.iterdir()):
            candidates_with_data.append((p, cand_space))

    plugin_dir: Path | None = None
    legacy_path: Path | None = None

    if slug:
        slug_raw = slugify(slug)
        if slug_raw == "codex":
            print("conscio space migrate: codex has no recognized plugin folder yet.", file=sys.stderr)
            return 3

        matched = []
        for p, s in candidates_with_data:
            if _plugin_dir_matches_slug(p, slug_raw, env):
                matched.append((p, s))

        if matched:
            plugin_dir, legacy_path = matched[0]
        else:
            for p in known_dirs:
                if _plugin_dir_matches_slug(p, slug_raw, env):
                    plugin_dir = p
                    legacy_path = p / "space"
                    break
            else:
                print(f"conscio space migrate: no legacy space found for slug {slug}.", file=sys.stderr)
                return 3
        slug = slug_raw
    else:
        # No --slug passed: derive from found folder
        if len(candidates_with_data) > 1:
            print("conscio space migrate: multiple legacy spaces found. Pass --slug to specify which one to migrate:", file=sys.stderr)
            for p, _ in candidates_with_data:
                print(f"  {p}", file=sys.stderr)
            return 3
        elif len(candidates_with_data) == 1:
            plugin_dir, legacy_path = candidates_with_data[0]
            if _plugin_dir_matches_slug(plugin_dir, "claude-code", env):
                slug = "claude-code"
            elif _plugin_dir_matches_slug(plugin_dir, "zcode", env):
                slug = "zcode"
            else:
                inst_file = legacy_path / "instance.json"
                slug_found = None
                if inst_file.is_file():
                    try:
                        data = json.loads(inst_file.read_text(encoding="utf-8"))
                        slug_found = data.get("label") or data.get("runtime")
                    except Exception:
                        pass
                if not slug_found:
                    host_ident = derive_host_identity(env)
                    slug_found = host_ident.runtime
                slug = slug_found or "default"
            slug = slugify(slug)
        else:
            # 0 candidates with space data
            # Check if an in-flight migration lock exists for resumption
            inst_root = space_dir("dummy").parent
            stale_locks = []
            if inst_root.is_dir():
                for lock_cand in inst_root.iterdir():
                    if lock_cand.name.startswith(".migrating-"):
                        stale_locks.append(lock_cand)

            if len(stale_locks) == 1:
                lock_slug = stale_locks[0].name.removeprefix(".migrating-")
                slug = slugify(lock_slug)
                for p in known_dirs:
                    if _plugin_dir_matches_slug(p, slug, env) or len(known_dirs) == 1:
                        plugin_dir = p
                        legacy_path = p / "space"
                        break
            else:
                # Check Rule 6: instance.json directly in root
                for p in known_dirs:
                    if (p / "instance.json").is_file():
                        print("conscio space migrate: legacy instance.json found directly in plugin root; moving entire plugin dir is prohibited.", file=sys.stderr)
                        return 3
                host_ident = derive_host_identity(env)
                if host_ident.runtime == "codex":
                    print("conscio space migrate: codex has no recognized plugin folder yet.", file=sys.stderr)
                    return 3
                print("conscio space migrate: cannot determine space slug. Pass --slug <name> explicitly.", file=sys.stderr)
                return 3

    durable_target = space_dir(slug)

    # Step 0 check: resolve_space and B3 collision check
    if legacy_path and legacy_path.exists():
        res = resolve_space(legacy_path, env=env)
        if res.kind == "B3":
            print(f"migration refused: {res.reason}", file=sys.stderr)
            return 2

    durable_inst = durable_target / "instance.json"
    legacy_inst = legacy_path / "instance.json" if legacy_path else None
    if durable_inst.is_file() and legacy_inst and legacy_inst.is_file():
        try:
            d_id = json.loads(durable_inst.read_text(encoding="utf-8")).get("instance_id")
            l_id = json.loads(legacy_inst.read_text(encoding="utf-8")).get("instance_id")
            if d_id and l_id and d_id != l_id:
                print(
                    f"migration refused: durable space {durable_target} (id {d_id}) "
                    f"conflicts with legacy space (id {l_id})",
                    file=sys.stderr,
                )
                return 2
        except Exception:
            pass

    # Check lock and crash resumption
    lock_path = migration_lock_path(slug)
    is_resumption = False

    if lock_path.is_file():
        pid = None
        try:
            lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
            pid = lock_data.get("pid")
        except Exception:
            pass

        if _is_pid_alive(pid, proc_root=proc_root):
            print(f"migration deferred: migration already in progress for {slug} (PID {pid} active)", file=sys.stderr)
            return 2
        else:
            is_resumption = True
            write_migration_lock(slug)
    else:
        # Check if already migrated (only when lock is absent)
        pointer_file = plugin_pointer_path(legacy_path, env) if legacy_path else None
        if pointer_file and pointer_file.is_file():
            try:
                data = json.loads(pointer_file.read_text(encoding="utf-8"))
                if data.get("target") == str(durable_target) and durable_target.exists():
                    if legacy_path and legacy_path.exists() and not any(legacy_path.iterdir()):
                        print(f"nothing to migrate: space for {slug} is already at {durable_target}")
                        return 0
            except Exception:
                pass

        if legacy_path and legacy_path.exists() and not any(legacy_path.iterdir()) and durable_target.exists():
            print(f"nothing to migrate: space for {slug} is already at {durable_target}")
            return 0

        # Step 1: Create lock BEFORE gates
        write_migration_lock(slug)

    # Gate 1: Process-zero check
    if legacy_path:
        active_procs = _find_active_legacy_procs(legacy_path, proc_root)
        if active_procs:
            if not is_resumption:
                remove_migration_lock(slug)
            unit = UNIT_MAP.get(slug, f"conscio-relay-reactor-{slug}")
            print(f"migration deferred: {len(active_procs)} active process(es) on the legacy path:", file=sys.stderr)
            for proc in active_procs:
                cmdline = proc["cmdline"]
                cmd_trunc = cmdline[:60] + "..." if len(cmdline) > 60 else cmdline
                print(f"  PID {proc['pid']}: {cmd_trunc} (systemctl --user stop {unit})", file=sys.stderr)
            return 2

    # Gate 2: Mtime-quieto check
    if quiet_minutes > 0 and legacy_path and legacy_path.exists():
        print(f"gate: checking quiet minutes (threshold: {quiet_minutes} min)...", file=sys.stderr)
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
            if not is_resumption:
                remove_migration_lock(slug)
            print(
                f"migration deferred: files in legacy path were modified in the last {quiet_minutes} minute(s)",
                file=sys.stderr,
            )
            return 2

    # Check collisions before moving anything (Ponto 4 & Adendo 1)
    if legacy_path and legacy_path.exists():
        collisions = []
        cleaned_up = []
        for item in list(legacy_path.iterdir()):
            dest = durable_target / item.name
            if dest.exists():
                if not is_resumption:
                    collisions.append(item.name)
                else:
                    if item.is_file():
                        if _are_files_identical(item, dest):
                            item.unlink()
                            cleaned_up.append(item.name)
                        else:
                            collisions.append(item.name)
                    elif item.is_dir():
                        if _are_trees_identical(item, dest):
                            shutil.rmtree(item)
                            cleaned_up.append(item.name)
                        else:
                            collisions.append(item.name)
                    else:
                        collisions.append(item.name)
        if collisions:
            if not is_resumption:
                remove_migration_lock(slug)
            print(
                f"migration refused: items already exist in destination {durable_target}:\n"
                f"  {', '.join(collisions)}",
                file=sys.stderr,
            )
            return 1
        if is_resumption and cleaned_up:
            print(
                f"resumption: cleaned up duplicate items already migrated to durable: {', '.join(cleaned_up)}",
                file=sys.stderr,
            )

    # Step 2: Backup rotativo de 2 gerações
    base_dir = durable_target.parent.parent
    backups_dir = base_dir / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    backup_dest = backups_dir / f"pre-migrate-{ts}"
    backup_dest.mkdir(parents=True, exist_ok=True)

    if legacy_path and legacy_path.exists():
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

    # Step 3 onwards: lock MUST remain if any failure occurs
    durable_target.mkdir(parents=True, exist_ok=True)
    try:
        # Step 3: Move content
        moved_items = []
        if legacy_path and legacy_path.exists():
            for item in list(legacy_path.iterdir()):
                dest = durable_target / item.name
                try:
                    os.rename(str(item), str(dest))
                except OSError as exc:
                    if exc.errno == errno.EXDEV:
                        if item.is_dir():
                            shutil.copytree(item, dest, dirs_exist_ok=True)
                            _verify_trees_identical(item, dest)
                            shutil.rmtree(item)
                        else:
                            shutil.copy2(item, dest)
                            if _sha256(dest) != _sha256(item):
                                raise RuntimeError(f"cross-fs verification failed for {item}")
                            item.unlink()
                    else:
                        raise
                moved_items.append(item.name)
            legacy_path.mkdir(parents=True, exist_ok=True)
        if is_resumption:
            print(
                f"resumption: completed migration of remaining items: {', '.join(moved_items)}",
                file=sys.stderr,
            )

        # Step 4: Gravar lápide migrated-from.json na raiz durável
        origin_str = str(legacy_path.resolve()) if legacy_path else str(durable_target)
        tombstone_payload = {
            "schema": 1,
            "origin": origin_str,
            "runtime": slug,
            "slug": slug,
            "migrated_ts": time.time(),
        }
        _write_json_atomic(durable_target / "migrated-from.json", tombstone_payload)

        # Step 5: Gravar ponteiro space-pointer.json na pasta do plugin
        if legacy_path:
            pointer_path = plugin_pointer_path(legacy_path, env)
            write_pointer_atomic(pointer_path, target=durable_target, runtime=slug, slug=slug)

        # Step 6: Apagar space-refused.json se existir
        if legacy_path:
            remove_refused_marker(legacy_path, env)

        # Step 7: Remover lock .migrating-<slug>
        remove_migration_lock(slug)

    except Exception as exc:
        print(f"migration failed at step 3+: lock {lock_path} preserved for boot protection: {exc}", file=sys.stderr)
        print(f"to resume migration: conscio space migrate --slug {slug}", file=sys.stderr)
        raise

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
