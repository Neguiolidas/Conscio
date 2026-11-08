# conscio/mcp/skills.py
"""MCP Skills extension (SEP-2640): serve the plugin's canonical skills
over MCP as ``skill://`` resources.

Source of truth is the Claude Code plugin assets, NOT
``conscio/agency/skills.py`` (SkillLibrary: procedural memory, a different
concept). The catalog is a snapshot built once per process from disk:
``load()`` constructs the whole dict and only then publishes it, and
``read_file`` and ``directory_read`` serve from the snapshot, never
re-reading disk, so a listing entry and a later read can never disagree.

Limits (SEP-2640, Limits): at most 512 files per skill (``SKILL.md``
included) and at most 16 MiB (16,777,216 bytes) total per skill. The
limits are pre-checked with stat() (file count via rglob over names only
plus the sum of st_size) before any content is read, except ``SKILL.md``
itself, which is read first to parse the frontmatter: a skill that
breaks either limit is excluded without any other file's bytes ever
entering RAM, and a ``SKILL.md`` that alone exceeds the 16 MiB cap is
excluded by stat() without being read at all. A skill that breaks either
limit is excluded at load with a line on stderr.

Frontmatter is the ``SKILL.md`` YAML frontmatter parsed with
``yaml.safe_load`` and returned verbatim as a dict (SEP-2640, Frontmatter:
"identical in content"; key order, quoting and line folding are NOT
preserved, which the SEP accepts). The parsed value must belong to the
JSON subset (dict with string keys, values only str/int/float/bool/None/
list/dict of those): anything else (a YAML date, bytes, a set, ...) is
not JSON-serializable and would kill the process when the dispatcher
serializes the response, so the skill is excluded with a log line, like
invalid frontmatter. Note the YAML coercions this validation accepts
as-is: ``flag: yes`` becomes True, ``1.10`` becomes 1.1, duplicate keys
take the parser's behaviour (last wins) — the subset check judges the
parsed value, it does not recover the original YAML spelling.

A skill directory that is a symlink escaping SKILLS_ROOT is excluded,
and so is any file inside a skill whose resolved path escapes the skill
directory (the file is dropped from the manifest with a log line, except
SKILL.md, whose escape excludes the whole skill). Files that cannot be
read are not silently dropped: an unreadable manifest file excludes the
whole skill with a log line.

The declared protocol version is at most 2025-06-18, so list results carry
no ``ttlMs``/``cacheScope`` (those arrive with 2026-07-28, SEP-2549).

A second, user-supplied root (``--skills-root``) unions with the bundled
root: bundled skills load first, user skills load second, and a user skill
with the same ``name`` as a bundled one WINS (explicit user override),
logged on stderr with the overridden path and the winner's path. Within
one root no collision is possible: a skill's ``name`` must equal its
directory name, and directory names are unique within a root, so two
entries from the same root can never share a name; only collisions
between the two roots exist, and those are logged. Every per-skill
validation in ``_load_skill`` (frontmatter, limits, traversal, symlinks,
unreadable files) is reused unchanged for the user root, so a bad user
skill is excluded with a log line and never takes the server down. A
user root that is not an existing directory, or that cannot be listed,
is skipped at load with a log line and the other root still loads; the
boot-time fail-fast for ``--skills-root`` lives in
``conscio.mcp.server.main``, only runs when the skills gate is on (with
the gate off the lazy catalog is never loaded, so the root is inert),
and runs before any capability is persisted, so a refused boot leaves
no ``skills`` capability behind.

A skill that cannot be read at load (OSError on access: an inaccessible
directory, SKILL.md, subdirectory or manifest file) is not left in place
and not deleted: it is moved to ``<root>/.quarantine/<name>-<timestamp>/``,
logged, and the other skills still load. A skill that was read and is
invalid (bad frontmatter, wrong name, over the limits, ...) is excluded
with a log line instead. On the quarantined skill's next invocation
(skills/get, resources/read, resources/directory/read) the catalog
attempts a lazy auto-repair: move it back, restore readable permissions
(directories 0o755, files 0o644), re-validate it, and serve it when the
repair succeeds. A repair that fails leaves the skill quarantined and the
call answers -32602; a repair never overwrites a skill that now exists in
the root and never deletes data.
"""
from __future__ import annotations

import hashlib
import math
import mimetypes
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import yaml

from .jsonrpc import InvalidParams

SKILLS_ROOT: Path = (Path(__file__).resolve().parent.parent / "integrations"
                     / "claude_code" / "assets" / "skills")

# Agent Skills naming rules: 1-64 chars, lowercase/digits, single hyphens,
# never leading/trailing, never doubled.
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){0,63}$")
MAX_FILES_PER_SKILL = 512
MAX_BYTES_PER_SKILL = 16_777_216
# The frontmatter block itself is capped before parsing (cycle-2 item 12).
MAX_FRONTMATTER_BYTES = 65_536
# Quarantine for inaccessible skill directories: a direct child of a
# skills root that cannot be accessed is moved here, never deleted, and
# repaired lazily on its next invocation. ".quarantine" cannot be a
# skill name (NAME_RE has no leading dot), so the scanner skips it.
QUARANTINE_DIRNAME = ".quarantine"


class _SkillInaccessible(Exception):
    """A skill that cannot be read (OSError on access), as opposed to a
    skill that was read and is invalid. _load_skill raises it; _load_root
    quarantines the directory and _maybe_repair re-quarantines on a
    failed repair. Content failures (bad frontmatter, wrong name, over
    limits, ...) still return None with a log line."""

# Fallback map: these suffixes keep their SEP-2640 MIME types even where
# the stdlib would guess differently (notably .yaml/.yml -> text/plain).
_MIME_BY_SUFFIX = {
    ".md": "text/markdown",
    ".json": "application/json",
    ".txt": "text/plain",
    ".yaml": "text/plain",
    ".yml": "text/plain",
}


def mime_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _MIME_BY_SUFFIX:
        return _MIME_BY_SUFFIX[suffix]
    guessed, _ = mimetypes.guess_type("file" + suffix)
    return guessed or "application/octet-stream"


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _inside_root(path: Path, resolved_dir: Path) -> bool:
    """Same escape check as the load phase: the resolved path must stay
    inside the skill directory. Unresolvable paths (e.g. a symlink loop)
    count as escaping."""
    try:
        return path.resolve().is_relative_to(resolved_dir)
    except (OSError, RuntimeError):
        return False


def _is_json_value(value: object) -> bool:
    """True when value belongs to the JSON subset: str/int/float/bool/None,
    or a list/dict (with string keys) of those, recursively. Anything
    yaml.safe_load can produce outside this (datetime.date, bytes, set,
    ...) is not json.dumps-able and must never reach the dispatcher.
    Non-finite floats (nan/inf, e.g. ``.nan`` / ``.inf`` / ``1.0e+999``)
    are out too: json.dumps would emit them as NaN/Infinity, which is
    not valid JSON on the wire."""
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_value(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json_value(v)
                   for k, v in value.items())
    return False


def _list_skill_tree(directory: Path) -> list[Path]:
    """Every path under a skill directory, sorted like ``rglob("*")``.

    ``Path.rglob`` swallows ``scandir`` errors (probed: an unreadable
    subdirectory is silently skipped, its files vanishing from the
    manifest with no log), so the walk is explicit and an ``OSError``
    propagates to the caller, which excludes the skill with a log line.
    Symlinked directories are listed but never descended, as with
    ``rglob`` (``recurse_symlinks=False``)."""
    found: list[Path] = []
    stack = [directory]
    while stack:
        current = stack.pop()
        with os.scandir(current) as it:
            entries = list(it)
        for entry in entries:
            path = Path(entry.path)
            found.append(path)
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                is_dir = False
            if is_dir:
                stack.append(path)
    return sorted(found, key=lambda p: str(p))


class SkillsCatalog:
    """Snapshot catalog of the skills under ``root`` plus, when given,
    ``user_root``.

    ``root`` is the primary root (default: the bundled plugin skills).
    ``user_root`` is a second root whose skills union with the primary
    ones; a user skill with the same name as a primary one WINS (explicit
    user override), logged on stderr. Within one root no collision is
    possible (a skill's name must equal its directory name, and directory
    names are unique within a root); only collisions between the two
    roots exist, and those are logged. Without ``user_root`` the behaviour
    is identical to a single-root catalog.
    """

    def __init__(self, root: Path | None = None, user_root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else SKILLS_ROOT
        self.user_root = Path(user_root) if user_root is not None else None
        if self.user_root is not None and self.user_root.resolve() == self.root.resolve():
            # Same directory twice: load once, no spurious override logs.
            self.user_root = None
        self._roots = [self.root] + ([self.user_root] if self.user_root is not None else [])
        self._loaded = False
        # name -> {"entry": dict, "dir": Path, "files": {rel_posix: bytes}}
        self._skills: dict[str, dict] = {}
        # name -> {"root": Path, "path": Path, "reason": str}: skill
        # directories moved to <root>/.quarantine at load because they
        # could not be accessed; repaired lazily on next invocation.
        self._quarantined: dict[str, dict] = {}

    # ------------------------------------------------------------------ load

    def load(self) -> None:
        skills: dict[str, dict] = {}
        quarantined: dict[str, dict] = {}
        for root in self._roots:
            if not root.is_dir():
                if root is self.user_root:
                    why = "is not a directory" if root.exists() else "does not exist"
                    print(
                        f"[conscio-mcp] skills: skipping user root {root}: {why}", file=sys.stderr
                    )
                continue
            per_root, per_quarantined = self._load_root(root)
            for name, entry in per_root.items():
                if name in skills:
                    print(
                        f"[conscio-mcp] skills: overriding skill "
                        f"{name!r} from {skills[name]['dir']} with "
                        f"{entry['dir']} (user root wins)",
                        file=sys.stderr,
                    )
                skills[name] = entry
            quarantined.update(per_quarantined)
        # A quarantined name that loaded from the other root is served
        # from there; its broken copy stays preserved in quarantine, but
        # there is nothing to repair on invocation.
        for name in list(quarantined):
            if name in skills:
                print(
                    f"[conscio-mcp] skills: {name!r} is quarantined at "
                    f"{quarantined[name]['path']} but loads from "
                    f"{skills[name]['dir']}",
                    file=sys.stderr,
                )
                del quarantined[name]
        # Publish atomically: readers never see a half-built catalog.
        self._skills = skills
        self._quarantined = quarantined
        self._loaded = True

    def _load_root(self, root: Path) -> tuple[dict[str, dict], dict[str, dict]]:
        """Load one root into (name -> entry, name -> quarantine record).
        Within a root there is no collision to resolve (a skill's name
        must equal its directory name, and directory names are unique
        within a root); every per-skill validation lives in _load_skill(),
        reused unchanged for the user root. A root that cannot be listed
        (e.g. unreadable) is skipped with a log line and empty dicts, so
        the other root still loads: a bad root must never poison the whole
        catalog. A direct child that cannot be accessed is moved to
        <root>/.quarantine/<name>-<timestamp>/ (never deleted) and
        recorded for lazy repair on its next invocation; a move that
        fails falls back to skip-with-log. The .quarantine directory
        itself is never treated as a skill."""
        skills: dict[str, dict] = {}
        quarantined: dict[str, dict] = {}
        try:
            root_resolved = root.resolve()
            children = sorted(root.iterdir(), key=lambda p: p.name)
        except OSError as exc:
            print(
                f"[conscio-mcp] skills: skipping skills root {root}: cannot list directory ({exc})",
                file=sys.stderr,
            )
            return {}, {}
        for child in children:
            if child.name == QUARANTINE_DIRNAME:
                continue
            try:
                if not child.is_dir():
                    continue
                if not child.resolve().is_relative_to(root_resolved):
                    print(
                        f"[conscio-mcp] skills: skipping {child}: skill directory escapes skills root",
                        file=sys.stderr,
                    )
                    continue
            except OSError as exc:
                self._quarantine_or_skip(root, child, exc, quarantined)
                continue
            try:
                entry = self._load_skill(child)
            except _SkillInaccessible as exc:
                self._quarantine_or_skip(root, child, exc, quarantined)
                continue
            if entry is not None:
                skills[entry["entry"]["frontmatter"]["name"]] = entry
        return skills, quarantined

    def _quarantine_or_skip(self, root: Path, child: Path, exc: Exception,
                            quarantined: dict[str, dict]) -> None:
        """Quarantine an inaccessible child (move + record + log), falling
        back to skip-with-log when the move fails. A failed quarantine
        must never poison the catalog."""
        record = self._quarantine_child(root, child, exc)
        if record is not None:
            quarantined[child.name] = record
            print(
                f"[conscio-mcp] skills: quarantined {child.name!r} "
                f"-> {record['path']} ({exc})",
                file=sys.stderr,
            )
        else:
            print(
                f"[conscio-mcp] skills: skipping {child}: {exc}",
                file=sys.stderr,
            )

    def _quarantine_child(self, root: Path, child: Path, exc: Exception) -> dict | None:
        """Move an inaccessible direct child of a skills root to
        ``<root>/.quarantine/<name>-<timestamp>/`` and return its record
        (``{"root", "path", "reason"}``). Returns None when the move
        fails, and the caller falls back to skip-with-log: a failed
        quarantine must never poison the catalog. The data is never
        deleted; repair on next invocation moves it back. Renaming a
        directory to a different parent updates its ".." entry, which
        needs write permission on the directory itself, so a chmod 000
        directory is chmodded to 0o755 first (a symlink needs no chmod:
        renaming a link never touches its target)."""
        qdir = root / QUARANTINE_DIRNAME
        try:
            qdir.mkdir(exist_ok=True)
        except OSError:
            return None
        if not child.is_symlink():
            try:
                os.chmod(child, 0o755)
            except OSError:
                return None
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        target = qdir / f"{child.name}-{stamp}"
        n = 2
        while target.exists():
            target = qdir / f"{child.name}-{stamp}-{n}"
            n += 1
        try:
            os.rename(child, target)
        except OSError:
            return None
        return {"root": root, "path": target, "reason": str(exc)}

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def _load_skill(self, directory: Path) -> dict | None:
        name = directory.name
        skill_md = directory / "SKILL.md"
        try:
            has_skill_md = skill_md.is_file()
        except OSError as exc:
            raise _SkillInaccessible(f"cannot access SKILL.md ({exc})") from exc
        if not has_skill_md:
            print(
                f"[conscio-mcp] skills: skipping {name} ({directory}): no SKILL.md", file=sys.stderr
            )
            return None
        resolved_dir = directory.resolve()
        if skill_md.is_symlink() and not skill_md.resolve().is_relative_to(resolved_dir):
            print(
                f"[conscio-mcp] skills: skipping {name} ({directory}): SKILL.md "
                f"escapes the skill directory",
                file=sys.stderr,
            )
            return None
        try:
            skill_md_size = skill_md.stat().st_size
        except OSError as exc:
            raise _SkillInaccessible(f"cannot stat SKILL.md ({exc})") from exc
        if skill_md_size > MAX_BYTES_PER_SKILL:
            print(
                f"[conscio-mcp] skills: skipping {name} ({directory}): SKILL.md alone "
                f"is {skill_md_size} bytes > {MAX_BYTES_PER_SKILL}",
                file=sys.stderr,
            )
            return None
        try:
            raw = skill_md.read_bytes()
        except OSError as exc:
            raise _SkillInaccessible(f"cannot read SKILL.md ({exc})") from exc
        try:
            frontmatter, ok, reason = _parse_frontmatter(raw)
        except Exception as exc:
            # Any non-YAML exception from the parse (e.g. RecursionError on
            # deeply nested frontmatter) must poison only this skill, never
            # the whole catalog: same treatment as OSError in the SKILL.md.
            print(
                f"[conscio-mcp] skills: skipping {name} ({directory}): frontmatter "
                f"unreadable ({type(exc).__name__}: {exc})",
                file=sys.stderr,
            )
            return None
        if not ok or frontmatter is None:
            print(f"[conscio-mcp] skills: skipping {name} ({directory}): {reason}", file=sys.stderr)
            return None
        if frontmatter["name"] != name:
            print(
                f"[conscio-mcp] skills: skipping {name} ({directory}): frontmatter "
                f"name {frontmatter['name']!r} != directory name",
                file=sys.stderr,
            )
            return None
        # Pre-check the limits by stat() before reading any content:
        # file count via rglob over names only, plus the sum of st_size.
        # Symlinks escaping the skill directory are dropped from the
        # manifest by the read phase below, so they are not counted here
        # either (same resolve() + is_relative_to check). Directories
        # (including empty ones) are recorded in the snapshot too, so
        # directory_read can list them.
        try:
            all_paths = _list_skill_tree(directory)
        except OSError as exc:
            raise _SkillInaccessible(f"cannot list skill directory ({exc})") from exc
        candidates = [p for p in all_paths
                      if p.is_file() and _inside_root(p, resolved_dir)]
        dirs = sorted({p.relative_to(directory).as_posix()
                       for p in all_paths
                       if p.is_dir() and _inside_root(p, resolved_dir)})
        if len(candidates) > MAX_FILES_PER_SKILL:
            print(
                f"[conscio-mcp] skills: skipping {name} ({directory}): "
                f"{len(candidates)} files > {MAX_FILES_PER_SKILL}",
                file=sys.stderr,
            )
            return None
        total_stat = 0
        for path in candidates:
            try:
                total_stat += path.stat().st_size
            except OSError:
                continue  # the read phase below logs and excludes the skill
        if total_stat > MAX_BYTES_PER_SKILL:
            print(
                f"[conscio-mcp] skills: skipping {name} ({directory}): "
                f"{total_stat} bytes > {MAX_BYTES_PER_SKILL}",
                file=sys.stderr,
            )
            return None
        files: dict[str, bytes] = {}
        for path in candidates:
            rel = path.relative_to(directory).as_posix()
            if not path.resolve().is_relative_to(resolved_dir):
                if rel == "SKILL.md":
                    print(
                        f"[conscio-mcp] skills: skipping {name} ({directory}): "
                        f"SKILL.md escapes the skill directory",
                        file=sys.stderr,
                    )
                    return None
                print(f"[conscio-mcp] skills: dropping {name}/{rel}: "
                      f"symlink escapes the skill directory", file=sys.stderr)
                continue
            try:
                data = path.read_bytes()
            except OSError as exc:
                # An unreadable manifest file makes the whole skill
                # inaccessible (it is quarantined by the caller):
                # silently dropping it would serve a listing the disk
                # cannot back.
                raise _SkillInaccessible(f"cannot read {rel} ({exc})") from exc
            files[rel] = data
        uri = f"skill://{name}/SKILL.md"
        resources = [{"uri": f"skill://{name}/{rel}",
                      "digest": _digest(data), "size": len(data)}
                     for rel, data in sorted(files.items())]
        entry = {"uri": uri, "frontmatter": frontmatter,
                 "resources": resources}
        return {"entry": entry, "dir": directory, "files": files,
                "dirs": dirs}

    def _maybe_repair(self, name: str) -> bool:
        """Lazy auto-repair for a quarantined skill, attempted on its next
        invocation (skills/get, resources/read, resources/directory/read).
        Restores readable permissions on the quarantined copy
        (directories 0o755, files 0o644, symlinks untouched), moves it
        back into its root, and re-validates it with _load_skill. On
        success the skill joins the catalog and the call proceeds. On
        any failure the skill stays quarantined and the call answers
        -32602. Never overwrites a skill that now exists in the root,
        never deletes data."""
        if name in self._skills:
            return True
        q = self._quarantined.get(name)
        if q is None:
            return False
        root = q["root"]
        target = root / name
        if target.exists():
            print(
                f"[conscio-mcp] skills: cannot repair {name!r}: {target} "
                f"already exists, keeping quarantined at {q['path']}",
                file=sys.stderr,
            )
            return False
        # Never repair (and never chmod) a quarantined path that escapes
        # its root: the quarantine move happens before the escape check
        # when resolve() itself raised, so re-check here.
        try:
            resolved = q["path"].resolve()
            root_resolved = root.resolve()
        except (OSError, RuntimeError):
            print(
                f"[conscio-mcp] skills: cannot repair {name!r}: cannot "
                f"resolve quarantined path {q['path']}",
                file=sys.stderr,
            )
            return False
        if not resolved.is_relative_to(root_resolved):
            print(
                f"[conscio-mcp] skills: cannot repair {name!r}: "
                f"quarantined path {q['path']} escapes its root",
                file=sys.stderr,
            )
            return False
        # Restore readable permissions while the copy is still in
        # quarantine, so a failure here leaves it quarantined instead of
        # landing a locked directory in the root.
        try:
            os.chmod(q["path"], 0o755)
        except OSError as exc:
            print(
                f"[conscio-mcp] skills: cannot repair {name!r}: cannot "
                f"restore permissions in quarantine ({exc})",
                file=sys.stderr,
            )
            return False
        for dirpath, dirnames, filenames in os.walk(q["path"]):
            try:
                os.chmod(dirpath, 0o755)
            except OSError:
                pass
            for sub in dirnames:
                p = Path(dirpath) / sub
                if p.is_symlink():
                    continue
                try:
                    os.chmod(p, 0o755)
                except OSError:
                    pass
            for fn in filenames:
                p = Path(dirpath) / fn
                if p.is_symlink():
                    continue
                try:
                    os.chmod(p, 0o644)
                except OSError:
                    pass
        try:
            os.rename(q["path"], target)
        except OSError as exc:
            print(
                f"[conscio-mcp] skills: cannot repair {name!r}: cannot move "
                f"back from quarantine ({exc})",
                file=sys.stderr,
            )
            return False
        try:
            entry = self._load_skill(target)
        except _SkillInaccessible as exc:
            # Back in the root but still unreadable: re-quarantine it
            # instead of leaving a locked directory in the root.
            record = self._quarantine_child(root, target, exc)
            if record is not None:
                self._quarantined[name] = record
                print(
                    f"[conscio-mcp] skills: re-quarantined {name!r} "
                    f"-> {record['path']} ({exc})",
                    file=sys.stderr,
                )
            else:
                del self._quarantined[name]
                print(
                    f"[conscio-mcp] skills: cannot repair {name!r}: "
                    f"re-quarantine failed ({exc})",
                    file=sys.stderr,
                )
            return False
        if entry is None:
            # Back in the root but still invalid: it is now an ordinary
            # excluded skill (logged by _load_skill), not a quarantined one.
            del self._quarantined[name]
            return False
        self._skills[name] = entry
        del self._quarantined[name]
        print(
            f"[conscio-mcp] skills: repaired {name!r} from quarantine "
            f"({q['path']})",
            file=sys.stderr,
        )
        return True

    # ------------------------------------------------------------- public API

    def list_skills(self, cursor: str | None = None) -> dict:
        """SEP-2640 §4.1. Single page; the catalog is tiny (D1)."""
        self._ensure_loaded()
        _check_cursor(cursor)
        return {"resultType": "complete",
                "skills": [s["entry"] for s in
                           sorted(self._skills.values(),
                                  key=lambda s: s["entry"]["uri"])]}

    def get_skill(self, uri: str) -> dict:
        """SEP-2640 §4.2. Lookup is by URI in the single-page catalog, so a
        skill answers by direct URI lookup whether or not a listing was
        ever requested. A quarantined skill is repaired lazily on this
        call (see _maybe_repair) and answers when the repair succeeds."""
        self._ensure_loaded()
        try:
            name, _rest = self._parse_uri(uri)
        except InvalidParams:
            name = None
        if name is not None:
            self._maybe_repair(name)
        for skill in self._skills.values():
            if skill["entry"]["uri"] == uri:
                return {"resultType": "complete", "skill": skill["entry"]}
        raise InvalidParams(f"unknown skill '{uri}'")

    def directory_read(self, uri: str, cursor: str | None = None) -> dict:
        """SEP-2640 §Directory Listing: direct children only, served from
        the snapshot, never from disk, so the listing can never disagree
        with skills/list or a later resources/read."""
        self._ensure_loaded()
        _check_cursor(cursor)
        name, rest = self._parse_uri(uri)
        self._maybe_repair(name)
        skill = self._skills.get(name)
        if skill is None:
            raise InvalidParams(f"unknown resource '{uri}'")
        prefix = "/".join(rest)
        dirs = set(skill["dirs"])
        children: dict[str, tuple[str, str | None]] = {}
        for rel in skill["files"]:
            if prefix:
                if rel == prefix:
                    raise InvalidParams(f"unknown resource '{uri}'")
                if not rel.startswith(prefix + "/"):
                    continue
                remainder = rel[len(prefix) + 1:]
            else:
                remainder = rel
            first, _, _rest = remainder.partition("/")
            if _rest:
                children.setdefault(first, ("dir", None))
            else:
                children[first] = ("file", rel)
        for rel in dirs:
            if prefix:
                if rel == prefix:
                    continue  # the directory itself, not one of its children
                if not rel.startswith(prefix + "/"):
                    continue
                remainder = rel[len(prefix) + 1:]
            else:
                remainder = rel
            first, _, _rest = remainder.partition("/")
            children.setdefault(first, ("dir", None))
        if prefix and not children and prefix not in dirs:
            raise InvalidParams(f"unknown resource '{uri}'")
        base = f"skill://{name}/" + (f"{prefix}/" if prefix else "")
        out = []
        for child_name in sorted(children):
            kind, rel = children[child_name]
            if kind == "dir":
                out.append({"uri": base + child_name, "name": child_name,
                            "mimeType": "inode/directory"})
            else:
                assert rel is not None
                out.append({"uri": base + child_name, "name": child_name,
                            "mimeType": mime_for(Path(rel))})
        return {"resultType": "complete", "resources": out}

    def read_file(self, uri: str) -> tuple[bytes, str]:
        """Bytes + mimeType from the snapshot. Raises InvalidParams for
        anything outside a skill directory (traversal, symlink escape),
        for directories and for unknown URIs."""
        self._ensure_loaded()
        skill_dir, rel = self._resolve_file(uri)
        data = self._skills[skill_dir.name]["files"].get(rel)
        if data is None:
            raise InvalidParams(f"unknown resource '{uri}'")
        return data, mime_for(Path(rel))

    # -------------------------------------------------------------- internals

    def _parse_uri(self, uri: str) -> tuple[str, tuple[str, ...]]:
        """Strict URI split: scheme ``skill``, first segment is the skill
        name, the rest is the path inside the skill. Any ``..``, ``.``,
        empty segment (double slash / trailing slash), query, fragment or
        NUL byte (literal or %00-encoded) is rejected before touching the
        filesystem."""
        if not isinstance(uri, str) or not uri or "\x00" in uri:
            raise InvalidParams(f"unknown resource '{uri}'")
        try:
            parsed = urlparse(unquote(uri))
        except ValueError:
            # Malformed URI (e.g. an unterminated IPv6 literal in the
            # authority): InvalidParams, never a leaked -32603.
            raise InvalidParams(f"unknown resource '{uri}'")
        if parsed.scheme != "skill" or not parsed.netloc \
                or parsed.query or parsed.fragment:
            raise InvalidParams(f"unknown resource '{uri}'")
        raw = f"{parsed.netloc}{parsed.path}"
        if "\x00" in raw:
            raise InvalidParams(f"unknown resource '{uri}'")
        if raw.endswith("/"):
            raise InvalidParams(f"unknown resource '{uri}'")
        segments = raw.split("/")
        if not segments or any(s in ("", ".", "..") for s in segments):
            raise InvalidParams(f"unknown resource '{uri}'")
        return segments[0], tuple(segments[1:])

    def _resolve_file(self, uri: str) -> tuple[Path, str]:
        # Snapshot-only: the files dict is the authority, never the disk.
        # _parse_uri already rejects ".." / "." / empty segments, and only
        # files that passed the load-time escape check are in the snapshot,
        # so membership here is the whole traversal/symlink defence.
        name, rest = self._parse_uri(uri)
        if not rest:
            raise InvalidParams(f"unknown resource '{uri}'")
        self._maybe_repair(name)
        skill = self._skills.get(name)
        if skill is None:
            raise InvalidParams(f"unknown resource '{uri}'")
        rel = Path(*rest).as_posix()
        if rel not in skill["files"]:
            raise InvalidParams(f"unknown resource '{uri}'")
        return skill["dir"], rel


def _check_cursor(cursor: str | None) -> None:
    if cursor is None or cursor == "":
        return
    if not isinstance(cursor, str):
        raise InvalidParams("cursor must be a string")
    # Single-page catalog: any non-empty cursor is unknown (D1).
    raise InvalidParams(f"unknown cursor {cursor!r}")


def _parse_frontmatter(raw: bytes) -> tuple[dict | None, bool, str]:
    """Returns (frontmatter, ok, reason). BOM is stripped, CRLF is
    normalized to LF explicitly (``\\r\\n`` -> ``\\n``; splitlines() is
    NOT used: it also splits on \\x0b, \\x0c, \\x85, \\u2028 and \\u2029).
    The block must be a YAML dict in the JSON subset (see _is_json_value)
    with a valid ``name`` equal to the directory name and a non-empty
    ``description`` of at most 1024 chars. Extra fields pass through
    verbatim. The block itself is capped at 64 KiB before parsing. YAML lossy coercions (``flag: yes`` -> True, ``1.10`` ->
    1.1, duplicate keys last-wins) are judged on the parsed value, not
    recovered."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, False, "SKILL.md is not valid UTF-8"
    text = text.removeprefix("\ufeff")
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return None, False, "missing opening '---'"
    try:
        end = next(i for i, line in enumerate(lines[1:], start=1)
                   if line.strip() == "---")
    except StopIteration:
        return None, False, "missing closing '---'"
    block = "\n".join(lines[1:end])
    if len(block.encode("utf-8")) > MAX_FRONTMATTER_BYTES:
        return None, False, "frontmatter block exceeds 64 KiB"
    try:
        parsed = yaml.safe_load(block)
    except Exception as exc:
        # yaml.YAMLError is the expected failure; anything else (e.g.
        # RecursionError on deeply nested frontmatter) is still a bad
        # frontmatter, not a reason to poison the catalog.
        return None, False, (f"invalid YAML frontmatter "
                             f"({type(exc).__name__}: {exc})")
    if not isinstance(parsed, dict):
        return None, False, "frontmatter is not a mapping"
    if not _is_json_value(parsed):
        return None, False, ("frontmatter value outside the JSON subset "
                             "(e.g. date, bytes, set)")
    name = parsed.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        return None, False, f"invalid name {name!r}"
    desc = parsed.get("description")
    if not isinstance(desc, str) or not 1 <= len(desc) <= 1024 \
            or not desc.strip():
        return None, False, "missing or invalid description"
    return parsed, True, ""
