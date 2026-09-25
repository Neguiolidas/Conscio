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
from typing import NamedTuple

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
                for di in dist_infos:
                    if not di.exists() or _is_editable_dist_info(di):
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


def _is_editable_dist_info(di: Path) -> bool:
    """An editable install's Version is the one at INSTALL time, not the code
    it loads (it points at a source tree). Measured 2026-09-24: ~/.local said
    4.7.1 and the repo .venv said 3.8.2 while both loaded the repo's 4.7.2."""
    import json
    try:
        data = json.loads((di / "direct_url.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(isinstance(data, dict)
                and isinstance(data.get("dir_info"), dict)
                and data["dir_info"].get("editable"))


def _sort_dist_infos(dist_infos: list[Path]) -> list[Path]:
    """Deeper (venv-own) dist-infos first: the process's own site-packages
    is the truth about what IT loaded; random parents are guesses.

    Measured false positive on a multi-install machine: the walk returned a
    stale ~/.local dist-info for a process running the repo-editable copy.
    """
    return sorted(dist_infos, key=lambda p: len(p.parts), reverse=True)


_PYTHON_EXE_RE = re.compile(r"^python(\d+(\.\d+)*)?$")


def _is_python_exe(exe: str) -> bool:
    """Only a Python interpreter may be probed with `-c "import conscio"`:
    for any other binary `-c` means something else, and running it has side
    effects (v4.7.2). A " (deleted)" suffix from /proc means the file is gone."""
    if not exe or exe.endswith(" (deleted)"):
        return False
    return bool(_PYTHON_EXE_RE.match(Path(exe).name))


class _Probe(NamedTuple):
    """What the target's own interpreter resolves for `import conscio`.

    importable: True  -> resolved; version and module_file are set.
                False -> the interpreter ran and conscio is NOT importable
                         there: the process is a wrapper (a watchdog, a
                         launcher), not Conscio itself.
                None  -> inconclusive (missing binary, crash, timeout).
    """

    version: str | None = None
    module_file: str | None = None
    importable: bool | None = None


# Runs inside the TARGET's interpreter. argv[1] is the sys.path[0] the target
# had (or "" for none): `-c` would otherwise put the probe's cwd there, and
# the answer would depend on where doctor was launched from — measured
# 2026-09-24: from the repo root every interpreter "had" the repo's conscio.
_PROBE_SCRIPT = (
    "import sys\n"
    "p0 = sys.argv[1]\n"
    "if sys.path and sys.path[0] == '':\n"
    "    del sys.path[0]\n"
    "if p0:\n"
    "    sys.path.insert(0, p0)\n"
    "try:\n"
    "    import conscio\n"
    "except ImportError:\n"
    "    print('conscio-probe\\t-')\n"
    "    raise SystemExit(0)\n"
    "print('conscio-probe\\t%s\\t%s' % (getattr(conscio, '__version__', ''),"
    " getattr(conscio, '__file__', '') or ''))\n"
)

# Environment that changes what an interpreter imports. The probe carries the
# TARGET's values, never doctor's own.
_PROBE_ENV_KEYS = ("PYTHONPATH", "PYTHONHOME", "PYTHONNOUSERSITE",
                   "PYTHONSAFEPATH", "PYTHONUSERBASE", "PYTHONPLATLIBDIR", "HOME")


def _probe_interpreter(
    exe: Path | str,
    path0: str | None = None,
    env: dict[str, str] | None = None,
    flags: str = "",
) -> _Probe:
    """(b+) Ask the target's own interpreter what conscio it resolves.

    `exe` must be the interpreter AS THE PROCESS INVOKED IT (see
    `_process_interpreter`), `path0` its sys.path[0], `env` its environment,
    `flags` its isolation flags (-s/-E/-I/-P). Best effort, 5s timeout.
    """
    import subprocess
    cmd = [str(exe)]
    if flags:
        cmd.append("-" + flags)
    cmd += ["-c", _PROBE_SCRIPT, path0 or ""]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=5, cwd="/", env=env)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return _Probe()
    if result.returncode != 0:
        return _Probe()
    for line in reversed(result.stdout.splitlines()):
        if not line.startswith("conscio-probe\t"):
            continue
        parts = line.split("\t")
        if parts[1:] == ["-"]:
            return _Probe(importable=False)
        ver = parts[1].strip() if len(parts) > 1 else ""
        if not re.fullmatch(r"[0-9]+(\.[0-9]+)*", ver):
            return _Probe()
        mod = parts[2].strip() if len(parts) > 2 else ""
        return _Probe(ver, mod or None, True)
    return _Probe()


def _read_proc_environ(entry: Path) -> dict[str, str] | None:
    try:
        raw = (entry / "environ").read_bytes()
    except OSError:
        return None
    env: dict[str, str] = {}
    for item in raw.split(b"\0"):
        key, sep, val = item.partition(b"=")
        if sep:
            env[key.decode("utf-8", "replace")] = val.decode("utf-8", "replace")
    return env


def _read_proc_cwd(entry: Path) -> str | None:
    try:
        cwd = os.readlink(entry / "cwd")
    except OSError:
        return None
    return cwd if os.path.isabs(cwd) and not cwd.endswith(" (deleted)") else None


def _absolute_arg(arg: str, cwd: str | None) -> str | None:
    """A path argument as the PROCESS saw it: relative paths are relative to
    ITS cwd, not doctor's. Unknown cwd -> None (never guess)."""
    if os.path.isabs(arg):
        return arg
    return os.path.normpath(os.path.join(cwd, arg)) if cwd else None


def _process_interpreter(
    args: list[str], exe_str: str, cwd: str | None, env: dict[str, str] | None,
) -> str | None:
    """The interpreter AS THE PROCESS INVOKED IT: cmdline[0], not /proc/<pid>/exe.

    exe is the symlink-RESOLVED binary. For a venv (uv tool, uvx archive,
    .venv) that is the BASE interpreter, which does not see the venv's
    site-packages — the v4.6.9 probe asked the wrong Python. Measured
    2026-09-24 after the 4.7.2 ship: 4 of 7 doctor warnings were false, among
    them the reporting MCP itself (uvx archive with 4.7.2, reported 4.7.1).
    Invoking the venv's own bin/python (unresolved) activates pyvenv.cfg.
    """
    if args and _is_python_exe(args[0]):
        a0 = args[0]
        cand: str | None = None
        if "/" in a0:
            cand = _absolute_arg(a0, cwd)
        elif env and env.get("PATH"):
            import shutil
            path = os.pathsep.join(
                p for p in env["PATH"].split(os.pathsep) if os.path.isabs(p))
            cand = shutil.which(a0, path=path) if path else None
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return exe_str if _is_python_exe(exe_str) else None


def _interpreter_invocation(
    args: list[str], cwd: str | None, env: dict[str, str] | None,
) -> tuple[str | None, str]:
    """(sys.path[0], isolation flags) the target interpreter started with.

    Python's rule ("Interface options"): `-m`, `-c`, `-` or interactive put
    the cwd first; a script puts its symlink-resolved directory; `-I`, `-P`
    and PYTHONSAFEPATH put nothing. Flags -s/-E/-I/-P are replayed so the
    probe sees the same site-packages the process sees.
    """
    safe = bool(env and env.get("PYTHONSAFEPATH"))
    flags = ""
    i = 1
    while i < len(args):
        a = args[i]
        if a == "-":
            return (None if safe else cwd), flags
        if a.startswith("--"):
            if a == "--check-hash-based-pycs":
                i += 1
            i += 1
            continue
        if a.startswith("-"):
            for j, ch in enumerate(a[1:], start=1):
                if ch in "sEIP":
                    flags += ch
                    safe = safe or ch in "IP"
                elif ch in "mc":
                    return (None if safe else cwd), flags
                elif ch in "WX":
                    if j == len(a) - 1:
                        i += 1  # the option's value is the next arg
                    break
            i += 1
            continue
        if safe:
            return None, flags
        script = _absolute_arg(a, cwd)
        return (os.path.dirname(os.path.realpath(script)) if script else None), flags
    return (None if safe else cwd), flags


def _probe_env(target_env: dict[str, str] | None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PYTHON") and k != "HOME"}
    env["HOME"] = os.environ.get("HOME", "")
    if target_env:
        for key in _PROBE_ENV_KEYS:
            if key in target_env:
                env[key] = target_env[key]
    if not env["HOME"]:
        del env["HOME"]
    return env


def _boot_time(proc_root: Path) -> float | None:
    try:
        for line in (proc_root / "stat").read_text(encoding="utf-8").splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def _proc_start_time(entry: Path, btime: float | None) -> float | None:
    """Epoch start of a process: btime + starttime (field 22) / CLK_TCK."""
    if btime is None:
        return None
    try:
        stat = (entry / "stat").read_text(encoding="utf-8")
        fields = stat[stat.rindex(")") + 2:].split()
        return btime + int(fields[19]) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return None


# Tolerance between a file's mtime and a process start: btime has 1s
# resolution, and an installer finishes writing just before it launches.
_START_SLACK_S = 2.0


def find_stale_processes(
    installed_version: str | None = None,
    proc_root: Path | str = Path("/proc"),
) -> list[dict]:
    """Scan proc_root for live Conscio processes running an older version.

    Excludes self. Detects running version via:
      (a) --report-version <ver> in cmdline
      (b) uvx --from conscio==<ver> (or @<ver>) in cmdline
      (b+) the process's own interpreter, invoked as the process invoked it
           (cmdline[0], its sys.path[0], its PYTHON* env), importing conscio
      (c) last resort: a non-editable dist-info found by walking up from the
          process's path arguments.

    Returns a list of dicts with:
      - pid: int
      - name: str
      - running_version: str ("<X" when the process predates X on disk)
      - installed_version: str
      - reason: "older_version" | "code_newer_than_process"
        (the latter adds started, code_mtime, module_file)
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
    btime = _boot_time(proc_path)

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

            cwd = _read_proc_cwd(entry)

            exe_path = entry / "exe"
            exe_str = ""
            exe_paths: list[Path] = []
            if exe_path.is_symlink():
                try:
                    exe_str = os.readlink(exe_path)
                    exe_paths.append(Path(exe_str))
                except OSError:
                    pass
                try:
                    exe_paths.append(exe_path.resolve())
                except OSError:
                    pass
            elif exe_path.exists():
                try:
                    if exe_path.is_file():
                        content = exe_path.read_text(
                            encoding="utf-8", errors="replace"
                        ).strip()
                        if content.startswith("/"):
                            exe_paths.append(Path(content))
                    exe_paths.append(exe_path.resolve())
                except OSError:
                    pass

            # Path arguments as the process saw them (relative to ITS cwd).
            arg_paths: list[Path] = []
            for arg in cmdline_args:
                if "/" in arg:
                    absolute = _absolute_arg(arg, cwd)
                    if absolute:
                        arg_paths.append(Path(absolute))

            is_conscio = (
                "conscio" in comm.lower()
                or any("conscio" in a.lower() for a in cmdline_args)
                or (exe_str and "conscio" in exe_str.lower())
                or any(
                    a == "--report-version" or a.startswith("--report-version=")
                    for a in cmdline_args
                )
            )

            # v4.7.2: filter BEFORE any detection. The interpreter probe
            # executes `<exe> -c ...`, and it used to run for EVERY process in
            # /proc: doctor spawned zcode, antigravity, rustdesk, pipewire,
            # gnome-keyring-daemon... with `-c` (for `claude`, `-c` is
            # --continue). Daemons that fork outlived the 5s kill — stray
            # gnome-keyring-daemons measured 2026-09-24 — and ~5s per
            # non-python process made doctor hang for minutes.
            if not is_conscio:
                continue

            running_ver = _detect_version_from_cmdline_flags(cmdline_args)
            if not running_ver:
                running_ver = _detect_version_from_uvx(cmdline_args)
            probe = _Probe()
            if not running_ver:
                env = _read_proc_environ(entry)
                interp = _process_interpreter(cmdline_args, exe_str, cwd, env)
                if interp:
                    path0, flags = _interpreter_invocation(cmdline_args, cwd, env)
                    probe = _probe_interpreter(
                        interp, path0=path0, env=_probe_env(env), flags=flags)
                    running_ver = probe.version
            if probe.importable is False:
                # v4.7.3: the interpreter answered that conscio is not
                # importable there — the process is a wrapper (a watchdog, a
                # launcher), not Conscio; the disk cannot contradict the
                # process's own answer.
                continue
            if not running_ver:
                # Last resort. v4.7.3: the parents of a PYTHON exe are the
                # base interpreter's install — walking up from uv's
                # ~/.local/share/uv/python/... reached ~/.local and returned
                # an unrelated dist-info (another Python version, editable).
                # They count only when the exe is not a Python at all.
                # A deleted interpreter (brew/uv replaced it under a live
                # process) is still a Python: its parents are the same base
                # install, even though it can no longer be probed.
                exe_is_python = _is_python_exe(exe_str.removesuffix(" (deleted)"))
                walk = arg_paths if exe_is_python else arg_paths + exe_paths
                running_ver = _detect_version_from_dist_info(walk)

            if not running_ver:
                continue

            proc_name = (
                comm
                if comm
                else (
                    Path(cmdline_args[0]).name
                    if cmdline_args
                    else "conscio"
                )
            )
            if _parse_version(running_ver) < parsed_target:
                stale.append(
                    {
                        "pid": pid,
                        "name": proc_name,
                        "running_version": running_ver,
                        "installed_version": target_version,
                        "reason": "older_version",
                    }
                )
                continue

            # v4.7.3: the probe reads the DISK. A process started before the
            # code it would import today was written (pip/uv upgrade in place,
            # editable repo after a bump) still runs the old code in memory:
            # exactly the "restart after upgrade" case, which doctor could
            # never see. Measured 2026-09-24: the freebuff reactor, up since
            # 09-22, was not even listed.
            started = _proc_start_time(entry, btime)
            if probe.module_file and started is not None:
                try:
                    code_mtime = os.stat(probe.module_file).st_mtime
                except OSError:
                    code_mtime = None
                if code_mtime is not None and code_mtime > started + _START_SLACK_S:
                    stale.append(
                        {
                            "pid": pid,
                            "name": proc_name,
                            "running_version": f"<{running_ver}",
                            "installed_version": target_version,
                            "reason": "code_newer_than_process",
                            "started": started,
                            "code_mtime": code_mtime,
                            "module_file": probe.module_file,
                        }
                    )
        except (OSError, ValueError):
            continue

    return sorted(stale, key=lambda x: x["pid"])


def _fmt_age(seconds: float) -> str:
    if seconds >= 86400:
        return f"{seconds / 86400:.0f}d"
    if seconds >= 3600:
        return f"{seconds / 3600:.0f}h"
    return f"{max(seconds, 0) / 60:.0f}min"


def _report_mailboxes(cards: list[dict]) -> None:
    """v4.7.2: per LOCAL agent, what is waiting unconsumed and since when.

    The question every "why doesn't X answer" session started with, answered
    by hand with sqlite one-liners (2026-09-24: Hermes had 3 messages from a
    new peer parked 20h, filtered out by a stale allowlist; 8fb1197f had 10
    broadcasts nobody would ever read). Informational, never a PROBLEM: a
    backlog is state, not a fault of the machine running doctor. Review types
    are left out — their own channel consumes them, not the wake path."""
    from . import relay
    now = time.time()
    print("mailboxes (unconsumed, per local agent):")
    for card in sorted(cards, key=lambda c: str(c.get("instance_id", ""))):
        cid = str(card.get("instance_id", ""))
        if directory.is_remote(card):
            continue
        space = str(card.get("space") or "")
        rows = [r for r in (mailbox.waiting(mailbox.db_in_space(Path(space)), cid)
                            if space else [])
                if r.get("type") not in relay.RESERVED_TYPES]
        try:
            parked = sum(1 for _ in directory.spool_dir(cid).glob("*.json"))
        except (OSError, ValueError):
            parked = 0
        silent = directory.dormant_for(card, now)
        # A card without runtime (freebuff) still says who it is by its space:
        # ~/.conscio/instances/<name>. A generic ".../space" says nothing.
        fallback = Path(space).name if space and Path(space).name != "space" else "?"
        label = str(card.get("runtime") or card.get("familia") or fallback)
        tag = f" DORMANT {_fmt_age(silent)}" if silent is not None else ""
        if not rows and not parked:
            print(f"  {cid[:8]} {label}: empty{tag}")
            continue
        line = f"  {cid[:8]} {label}: {len(rows)} waiting"
        if rows:
            senders: dict[str, int] = {}
            for r in rows:
                s = str(r.get("from_instance", ""))[:8]
                senders[s] = senders.get(s, 0) + 1
            by = ", ".join(f"{s}×{n}" for s, n in sorted(senders.items()))
            line += f", oldest {_fmt_age(now - float(rows[0]['ts']))} ({by})"
        if parked:
            line += f", {parked} parked in spool"
        print(line + tag)
    for oid, n in directory.orphan_spools():
        print(f"AVISO: spool {oid[:8]} holds {n} message(s) and has no card — "
              f"nobody will ingest them (`relay forget` left it, or the agent "
              f"never published)")


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

    _report_mailboxes(cards)

    proc_root = getattr(args, "proc_root", Path("/proc"))
    installed_ver = getattr(args, "installed_version", None)
    stale = find_stale_processes(
        installed_version=installed_ver, proc_root=proc_root
    )
    for sp in stale:
        if sp.get("reason") == "code_newer_than_process":
            print(
                f"AVISO: processo {sp['pid']} ({sp['name']}) iniciou "
                f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(sp['started']))}, "
                f"antes do codigo que carregaria hoje "
                f"({sp['running_version'][1:]}, gravado "
                f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(sp['code_mtime']))}): "
                f"roda codigo anterior, reinicie para nao apagar campos novos"
            )
            continue
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
