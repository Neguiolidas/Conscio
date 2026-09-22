# conscio/liaison/relay_cli.py
"""`conscio relay` — the operator surface of the relay (v4.5.4).

Everything here answers a question that used to require reading a journal,
a systemd unit and three sqlite files: who can I reach, what is stuck, and
why is nothing arriving. The subcommands:

  pair        register a peer that lives on another machine (url + token)
  peers       who is in the directory, local or remote, and how fresh
  quarantine  list / purge what the mailbox refused to parse
  doctor      am I published, is anything parked in my spool, is it moving
  service     print a user systemd unit: the bridge, or the reactor

Engine-free and side-effect honest: every command prints what it did and
returns a non-zero code when the answer is "no".
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

from . import directory, mailbox, relay_transport


def _cmd_pair(args: argparse.Namespace) -> int:
    """Teach this machine how to reach an agent on another one."""
    if not directory.valid_id(args.id):
        print(f"invalid id: {args.id}", file=sys.stderr)
        return 2
    url = str(args.url).strip()
    if not url.startswith(("http://", "https://")):
        print(f"invalid url: {url}", file=sys.stderr)
        return 2
    remotes = relay_transport.load_remotes()
    remotes[args.id] = {"url": url, "token": args.token,
                        "paired_at": time.time()}
    relay_transport.save_remotes(remotes)
    # A remote peer has a url and NO spool: `spool` is the marker of "same
    # filesystem", and claiming it here would make delivery try a local write.
    directory.publish({"instance_id": args.id, "spool": "", "url": url,
                       "capabilities": ["relay"], "updated_at": time.time()})
    print(f"paired: {args.id} -> {url}")
    return 0


def _cmd_peers(args: argparse.Namespace) -> int:
    cards = directory.peers(exclude=args.id or "")
    if not cards:
        print("directory is empty (no peer published a card yet)")
        return 0
    now = time.time()
    print(f"{'id':<26} {'where':<8} {'role':<12} seen")
    for c in sorted(cards, key=lambda x: str(x.get("instance_id", ""))):
        where = "remote" if str(c.get("url", "")).strip() else "local"
        age = now - float(c.get("updated_at", 0) or 0)
        print(f"{c.get('instance_id', '')!s:<26} {where:<8} "
              f"{c.get('papel', '') or '-'!s:<12} {age:.0f}s ago")
    print(f"total: {len(cards)}")
    return 0


def _db_for(args: argparse.Namespace) -> Path:
    """v4.6.7: this agent's mailbox, not the neutral default.

    `service` embeds the result in a systemd unit, so resolving wrong here does
    not produce one wrong answer that scrolls away — it writes a persistent
    service file pointed at a database nobody writes.
    """
    from ..space import resolve_live_space
    # `service` already knows which agent it is generating a unit for: that id
    # is what disambiguates the space, so the generated unit does not die on a
    # machine where more than one agent published one.
    self_id = str(getattr(args, "id", "") or "").strip()
    return mailbox.resolve_db(
        resolve_live_space(args.storage, self_id).path, args.liaison_db)


def _cmd_quarantine(args: argparse.Namespace) -> int:
    db = _db_for(args)
    if args.purge_days is not None:
        n = mailbox.purge_quarantine(db, older_than_days=args.purge_days)
        print(f"purged: {n}")
        return 0
    rows = mailbox.list_quarantine(db)
    for r in rows:
        print(f"{r.get('ts', '')}  {r.get('motivo', '')}")
    print(f"total: {len(rows)}")
    return 0


def _cmd_forget(args: argparse.Namespace) -> int:
    """Drop a peer's card from this machine's directory.

    The card is an address, not the agent: forgetting one removes a name from
    the square, and any agent still running simply republishes on its next
    heartbeat. That asymmetry is the whole safety story — this cannot silence a
    live peer, only retire a dead one.

    It exists because a card can outlive what published it. An agent whose space
    was minted by an older version and never ran again leaves a card that no
    process will ever refresh or remove, and every peer on the machine carries
    it as a name that never answers.
    """
    target = (args.id or "").strip()
    if not directory.valid_id(target):
        print(f"not an instance id: {target!r}", file=sys.stderr)
        return 2

    card = directory.get(target)
    if card is None:
        print(f"no card for {target} in {directory.peers_dir()}")
        return 1

    if target == (args.self_id or os.environ.get("CONSCIO_SELF_ID", "")).strip():
        print("note: that is your own card — a running agent republishes it "
              "on its next heartbeat", file=sys.stderr)

    age_days = (time.time() - float(card.get("updated_at", 0) or 0)) / 86400
    if not directory.forget(target):
        print(f"could not remove the card for {target}", file=sys.stderr)
        return 1
    print(f"forgot {target} (card was {age_days:.0f} day(s) old, "
          f"runtime {card.get('runtime') or '-'})")
    print("the space and its identity are untouched; only the address is gone")
    return 0


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse version string into a numeric tuple padded to at least 3 elements."""
    nums = [int(x) for x in re.findall(r"\d+", str(v))]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def _resolve_installed_version() -> str:
    """Resolve the currently installed version of conscio."""
    try:
        import conscio

        v = getattr(conscio, "__version__", None)
        if v:
            return str(v)
    except Exception:
        pass
    try:
        import importlib.metadata

        return str(importlib.metadata.version("conscio"))
    except Exception:
        pass
    return "0.0.0"


def _detect_version_from_cmdline_flags(args: list[str]) -> str | None:
    """(a) --report-version <ver> or --report-version=<ver> in cmdline."""
    for i, arg in enumerate(args):
        if arg == "--report-version" and i + 1 < len(args):
            v = args[i + 1].strip().strip("'\"")
            if v and not v.startswith("-"):
                return v
        elif arg.startswith("--report-version="):
            v = arg.split("=", 1)[1].strip().strip("'\"")
            if v:
                return v
    for arg in args:
        m = re.search(r"--report-version(?:=|\s+)([^\s]+)", arg)
        if m:
            return m.group(1).strip().strip("'\"")
    return None


def _detect_version_from_uvx(args: list[str]) -> str | None:
    """(b) uvx --from conscio==<ver> (or @<ver>) in cmdline."""
    pattern = re.compile(
        r"(?:^|[\s=/])conscio(?:==|@)([0-9]+(?:\.[0-9]+)*(?:[a-zA-Z0-9_\.-]*))"
    )
    for arg in args:
        m = pattern.search(arg)
        if m:
            return m.group(1).strip().strip("'\"")
    return None


def _detect_version_from_dist_info(candidate_paths: list[Path]) -> str | None:
    """(c) Python virtualenv / package dist-info resolved by walking up parent directories.

    Looks for `lib/python*/site-packages/conscio-*.dist-info`.
    """
    version_pattern = re.compile(
        r"^conscio-([0-9]+(?:\.[0-9]+)*[a-zA-Z0-9_\.-]*)\.dist-info$"
    )
    for cp in candidate_paths:
        try:
            dirs_to_check: list[Path] = []
            if cp.is_dir():
                dirs_to_check.append(cp)
            dirs_to_check.extend(cp.parents)
            for d in dirs_to_check:
                dist_infos: list[Path] = []
                lib_dir = d / "lib"
                if lib_dir.is_dir():
                    dist_infos.extend(
                        lib_dir.glob("python*/site-packages/conscio-*.dist-info")
                    )
                    dist_infos.extend(
                        lib_dir.glob("site-packages/conscio-*.dist-info")
                    )
                sp_dir = d / "site-packages"
                if sp_dir.is_dir():
                    dist_infos.extend(sp_dir.glob("conscio-*.dist-info"))
                if "packages" in d.name:
                    dist_infos.extend(d.glob("conscio-*.dist-info"))

                dist_infos = _sort_dist_infos(dist_infos)
                dist_infos = _sort_dist_infos(dist_infos)
                for di in dist_infos:
                    if not di.exists():
                        continue
                    meta_file = di / "METADATA"
                    if meta_file.is_file():
                        try:
                            for line in meta_file.read_text(
                                encoding="utf-8", errors="replace"
                            ).splitlines():
                                if line.startswith("Version:"):
                                    ver = line.split(":", 1)[1].strip()
                                    if ver:
                                        return ver
                        except OSError:
                            pass
                    m = version_pattern.match(di.name)
                    if m:
                        return m.group(1)
        except OSError:
            continue
    return None


def _sort_dist_infos(dist_infos: list[Path]) -> list[Path]:
    """Deeper (venv-own) dist-infos first: the process's own site-packages
    is the truth about what IT loaded; random parents are guesses.

    Measured false positive on a multi-install machine: the walk returned a
    stale ~/.local dist-info for a process running the repo-editable copy.
    """
    return sorted(dist_infos, key=lambda p: len(p.parts), reverse=True)


def _detect_version_from_interpreter(exe: Path) -> str | None:
    """(b+) Ask the target's own interpreter what conscio it resolves.

    Runs a subprocess of the process's interpreter (/proc/<pid>/exe) that
    imports conscio and prints __version__. This is the process's REAL
    resolution order — its venv's sys.path — instead of guessing from a
    dist-info walk that can find any of several coinstalled copies.

    Best effort: dead interpreter, no conscio importable, or timeout all
    return None (the caller falls through to the walk).
    """
    import subprocess
    try:
        result = subprocess.run(
            [str(exe), "-c", "import conscio; print(conscio.__version__)"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            ver = result.stdout.strip()
            if ver and re.fullmatch(r"[0-9]+(\.[0-9]+)*", ver):
                return ver
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def find_stale_processes(
    installed_version: str | None = None,
    proc_root: Path | str = Path("/proc"),
) -> list[dict]:
    """Scan proc_root for live Conscio processes running an older version.

    Excludes self. Detects running version via:
      (a) --report-version <ver> in cmdline
      (b) uvx --from conscio==<ver> (or @<ver>) in cmdline
      (c) Python virtualenv / package dist-info resolved by walking up parent directories.

    Returns a list of dicts with:
      - pid: int
      - name: str
      - running_version: str
      - installed_version: str
    """
    proc_path = Path(proc_root)
    if not proc_path.exists() or not proc_path.is_dir():
        return []

    target_version = (
        installed_version
        if installed_version is not None
        else _resolve_installed_version()
    )
    parsed_target = _parse_version(target_version)
    self_pid = os.getpid()

    stale: list[dict] = []

    try:
        entries = list(proc_path.iterdir())
    except OSError:
        return []

    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            pid = int(entry.name)
        except ValueError:
            continue

        if pid == self_pid:
            continue

        try:
            comm_path = entry / "comm"
            comm = ""
            if comm_path.is_file():
                comm = comm_path.read_text(
                    encoding="utf-8", errors="replace"
                ).strip()

            cmdline_path = entry / "cmdline"
            cmdline_args: list[str] = []
            if cmdline_path.is_file():
                raw = cmdline_path.read_bytes()
                cmdline_args = [
                    a
                    for a in raw.decode("utf-8", errors="replace").split("\x00")
                    if a
                ]

            if not cmdline_args and not comm:
                continue

            exe_path = entry / "exe"
            exe_str = ""
            candidate_paths: list[Path] = []
            if exe_path.is_symlink():
                try:
                    exe_str = os.readlink(exe_path)
                    candidate_paths.append(Path(exe_str))
                except OSError:
                    pass
                try:
                    candidate_paths.append(exe_path.resolve())
                except OSError:
                    pass
            elif exe_path.exists():
                try:
                    if exe_path.is_file():
                        content = exe_path.read_text(
                            encoding="utf-8", errors="replace"
                        ).strip()
                        if content.startswith("/"):
                            candidate_paths.append(Path(content))
                    candidate_paths.append(exe_path.resolve())
                except OSError:
                    pass

            for arg in cmdline_args:
                if "/" in arg:
                    candidate_paths.append(Path(arg))

            is_conscio = (
                "conscio" in comm.lower()
                or any("conscio" in a.lower() for a in cmdline_args)
                or (exe_str and "conscio" in exe_str.lower())
                or any(
                    a == "--report-version" or a.startswith("--report-version=")
                    for a in cmdline_args
                )
            )

            running_ver = _detect_version_from_cmdline_flags(cmdline_args)
            if not running_ver:
                running_ver = _detect_version_from_uvx(cmdline_args)
            if not running_ver:
                running_ver = _detect_version_from_interpreter(Path(exe_str)) if exe_str else None
            if not running_ver:
                running_ver = _detect_version_from_dist_info(candidate_paths)

            if not is_conscio or not running_ver:
                continue

            parsed_running = _parse_version(running_ver)
            if parsed_running < parsed_target:
                proc_name = (
                    comm
                    if comm
                    else (
                        Path(cmdline_args[0]).name
                        if cmdline_args
                        else "conscio"
                    )
                )
                stale.append(
                    {
                        "pid": pid,
                        "name": proc_name,
                        "running_version": running_ver,
                        "installed_version": target_version,
                    }
                )
        except (OSError, ValueError):
            continue

    return sorted(stale, key=lambda x: x["pid"])


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Three questions, no journal: am I published, is anything parked in my
    spool, and does the directory know anybody at all."""
    self_id = (args.id or "").strip()
    problems: list[str] = []

    cards = directory.peers(exclude="")
    print(f"directory: {len(cards)} card(s) in {directory.peers_dir()}")

    if not self_id:
        print("my card: unknown (pass --id to check yourself)")
    else:
        card = directory.get(self_id)
        print(f"my card: {'ok' if card else 'MISSING'}")
        if card is None:
            problems.append(
                f"the card for {self_id} is not published — peers cannot "
                f"reach an agent they cannot see")
        try:
            parked = list(directory.spool_dir(self_id).glob("*.json"))
        except (OSError, ValueError) as exc:
            parked = []
            problems.append(f"spool unreachable: {exc}")
        print(f"parked in my spool: {len(parked)} message(s) awaiting ingest")
        if parked:
            print("  (they arrive on the next tool call; a session that never "
                  "runs one never ingests)")

    remotes = relay_transport.load_remotes()
    print(f"paired remotes: {len(remotes)}")

    proc_root = getattr(args, "proc_root", Path("/proc"))
    installed_ver = getattr(args, "installed_version", None)
    stale = find_stale_processes(
        installed_version=installed_ver, proc_root=proc_root
    )
    for sp in stale:
        print(
            f"AVISO: processo {sp['pid']} ({sp['name']}) roda versao "
            f"{sp['running_version']} < instalada {sp['installed_version']}, "
            f"reinicie apos upgrade para nao apagar campos novos"
        )

    for p in problems:
        print(f"PROBLEM: {p}", file=sys.stderr)
    return 1 if problems else 0


_UNIT = """[Unit]
Description=Conscio relay bridge (cross-machine delivery)
After=network-online.target

[Service]
ExecStart=%h/.local/bin/conscio-relay-bridge --bind {bind} --port {port}
Restart=always
RestartSec=5
Environment=CONSCIO_RELAY_ROOT={root}

[Install]
WantedBy=default.target
"""


_REACTOR_UNIT = """[Unit]
Description=Conscio relay reactor (reactive delivery; every message notifies)
After=network-online.target

[Service]
ExecStart={python} -u -m conscio.liaison.reactor --liaison-db {db} \
--self-id {self_id} --interval {interval}
Restart=always
RestartSec=5
Environment=CONSCIO_NOTIFY_CMD={notify_cmd}

[Install]
WantedBy=default.target
"""


def _cmd_service(args: argparse.Namespace) -> int:
    """Print a user unit: `--kind bridge` (transport) or `reactor` (wake-ups).

    Deliberately without `RestartPreventExitStatus`/`SuccessExitStatus`: that
    pair is what kept a dead watcher reported as a success for 21 hours. An
    error exit is never declared a success here.

    The reactor unit carries no `--relay-peer`: an empty allowlist means the
    whole directory, so a peer that re-registers under a new id keeps being
    heard. A hand-maintained allowlist is the per-agent wiring this release
    exists to delete.
    """
    if args.kind == "reactor":
        if not args.notify_cmd:
            print("config error: --notify-cmd required (the reactor has no "
                  "way to wake an agent without one)", file=sys.stderr)
            return 2
        self_id = args.id or os.environ.get("CONSCIO_SELF_ID", "").strip()
        if not self_id:
            print("config error: --id required (no instance id resolved)",
                  file=sys.stderr)
            return 2
        db = _db_for(args)
        print(_REACTOR_UNIT.format(python=sys.executable, db=db,
                                   self_id=self_id, interval=args.interval,
                                   notify_cmd=args.notify_cmd), end="")
        print("# save as ~/.config/systemd/user/conscio-relay-reactor.service",
              file=sys.stderr)
        print("# then: systemctl --user daemon-reload && systemctl --user "
              "enable --now conscio-relay-reactor", file=sys.stderr)
        return 0

    print(_UNIT.format(bind=args.bind, port=args.port,
                       root=directory.relay_root()), end="")
    print("# save as ~/.config/systemd/user/conscio-relay-bridge.service",
          file=sys.stderr)
    print("# then: systemctl --user daemon-reload && systemctl --user enable "
          "--now conscio-relay-bridge", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="conscio relay",
        description="Operate the relay: pair machines, inspect peers, "
                    "diagnose delivery.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("pair", help="register a peer on another machine")
    p.add_argument("--id", required=True, help="peer instance id")
    p.add_argument("--url", required=True, help="peer bridge url")
    p.add_argument("--token", required=True, help="shared bridge token")
    p.set_defaults(fn=_cmd_pair)

    p = sub.add_parser("peers", help="list the directory")
    p.add_argument("--id", default="", help="exclude myself from the list")
    p.set_defaults(fn=_cmd_peers)

    p = sub.add_parser("quarantine", help="list/purge unparseable messages")
    p.add_argument("--liaison-db", default="")
    p.add_argument("--storage", default="",
                   help="space to act on (default: the live space,"
                        " resolved from the directory card)")
    p.add_argument("--purge-days", type=float, default=None,
                   help="purge entries older than N days (0 = all)")
    p.set_defaults(fn=_cmd_quarantine)

    p = sub.add_parser("forget", help="drop a peer's card from the directory")
    p.add_argument("id", help="the instance id to forget")
    p.add_argument("--self-id", default="",
                   help="my instance id, only so the command can warn when you "
                        "are forgetting yourself (default $CONSCIO_SELF_ID)")
    p.set_defaults(fn=_cmd_forget)

    p = sub.add_parser("doctor", help="why is nothing arriving?")
    p.add_argument("--id", default="", help="my instance id")
    p.add_argument("--installed-version", default=None, help=argparse.SUPPRESS)
    p.add_argument(
        "--proc-root", type=Path, default=Path("/proc"), help=argparse.SUPPRESS
    )
    p.set_defaults(fn=_cmd_doctor)

    p = sub.add_parser("service", help="print a systemd user unit")
    p.add_argument("--kind", choices=("bridge", "reactor"), default="bridge",
                   help="bridge = cross-machine transport; reactor = the "
                        "reactive loop that wakes this agent (default bridge)")
    p.add_argument("--notify-cmd", default="",
                   help="[reactor] command run per message, JSON on stdin")
    p.add_argument("--id", default="",
                   help="[reactor] my instance id (default $CONSCIO_SELF_ID)")
    p.add_argument("--liaison-db", default="",
                   help="[reactor] path to liaison.db (default: resolved)")
    p.add_argument("--storage", default="",
                   help="space to act on (default: the live space,"
                        " resolved from the directory card)")
    p.add_argument("--interval", type=float, default=5.0,
                   help="[reactor] poll every N seconds (default 5)")
    # Loopback by default, like relay_net's own --bind. A generated unit that
    # silently listens on every interface is not the doc's "bind to the
    # tailnet address": pass the tailscale IP to accept remote peers.
    p.add_argument("--bind", default="127.0.0.1",
                   help="address the unit listens on (default 127.0.0.1; "
                        "pass the tailscale IP to accept remote peers)")
    p.add_argument("--port", type=int, default=8789)
    p.set_defaults(fn=_cmd_service)

    args = ap.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
