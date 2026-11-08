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
"""
from __future__ import annotations

import hashlib
import math
import mimetypes
import re
import sys
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


class SkillsCatalog:
    """Snapshot catalog of the skills under ``root``."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else SKILLS_ROOT
        self._loaded = False
        # name -> {"entry": dict, "dir": Path, "files": {rel_posix: bytes}}
        self._skills: dict[str, dict] = {}

    # ------------------------------------------------------------------ load

    def load(self) -> None:
        skills: dict[str, dict] = {}
        if self.root.is_dir():
            root_resolved = self.root.resolve()
            for child in sorted(self.root.iterdir(), key=lambda p: p.name):
                if not child.is_dir():
                    continue
                if not child.resolve().is_relative_to(root_resolved):
                    print(f"[conscio-mcp] skills: skipping {child.name}: "
                          f"skill directory escapes skills root",
                          file=sys.stderr)
                    continue
                entry = self._load_skill(child)
                if entry is not None:
                    skills[entry["entry"]["frontmatter"]["name"]] = entry
        # Publish atomically: readers never see a half-built catalog.
        self._skills = skills
        self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def _load_skill(self, directory: Path) -> dict | None:
        name = directory.name
        skill_md = directory / "SKILL.md"
        if not skill_md.is_file():
            return None
        resolved_dir = directory.resolve()
        if skill_md.is_symlink() and not skill_md.resolve().is_relative_to(
                resolved_dir):
            print(f"[conscio-mcp] skills: skipping {name}: SKILL.md "
                  f"escapes the skill directory", file=sys.stderr)
            return None
        try:
            skill_md_size = skill_md.stat().st_size
        except OSError as exc:
            print(f"[conscio-mcp] skills: skipping {name}: cannot stat "
                  f"SKILL.md ({exc})", file=sys.stderr)
            return None
        if skill_md_size > MAX_BYTES_PER_SKILL:
            print(f"[conscio-mcp] skills: skipping {name}: SKILL.md alone "
                  f"is {skill_md_size} bytes > {MAX_BYTES_PER_SKILL}",
                  file=sys.stderr)
            return None
        try:
            raw = skill_md.read_bytes()
        except OSError as exc:
            print(f"[conscio-mcp] skills: skipping {name}: cannot read "
                  f"SKILL.md ({exc})", file=sys.stderr)
            return None
        try:
            frontmatter, ok, reason = _parse_frontmatter(raw)
        except Exception as exc:
            # Any non-YAML exception from the parse (e.g. RecursionError on
            # deeply nested frontmatter) must poison only this skill, never
            # the whole catalog: same treatment as OSError in the SKILL.md.
            print(f"[conscio-mcp] skills: skipping {name}: frontmatter "
                  f"unreadable ({type(exc).__name__}: {exc})", file=sys.stderr)
            return None
        if not ok or frontmatter is None:
            print(f"[conscio-mcp] skills: skipping {name}: {reason}",
                  file=sys.stderr)
            return None
        if frontmatter["name"] != name:
            print(f"[conscio-mcp] skills: skipping {name}: frontmatter "
                  f"name {frontmatter['name']!r} != directory name",
                  file=sys.stderr)
            return None
        # Pre-check the limits by stat() before reading any content:
        # file count via rglob over names only, plus the sum of st_size.
        # Symlinks escaping the skill directory are dropped from the
        # manifest by the read phase below, so they are not counted here
        # either (same resolve() + is_relative_to check). Directories
        # (including empty ones) are recorded in the snapshot too, so
        # directory_read can list them.
        all_paths = sorted(directory.rglob("*"), key=lambda p: str(p))
        candidates = [p for p in all_paths
                      if p.is_file() and _inside_root(p, resolved_dir)]
        dirs = sorted({p.relative_to(directory).as_posix()
                       for p in all_paths
                       if p.is_dir() and _inside_root(p, resolved_dir)})
        if len(candidates) > MAX_FILES_PER_SKILL:
            print(f"[conscio-mcp] skills: skipping {name}: "
                  f"{len(candidates)} files > {MAX_FILES_PER_SKILL}",
                  file=sys.stderr)
            return None
        total_stat = 0
        for path in candidates:
            try:
                total_stat += path.stat().st_size
            except OSError:
                continue  # the read phase below logs and excludes the skill
        if total_stat > MAX_BYTES_PER_SKILL:
            print(f"[conscio-mcp] skills: skipping {name}: "
                  f"{total_stat} bytes > {MAX_BYTES_PER_SKILL}",
                  file=sys.stderr)
            return None
        files: dict[str, bytes] = {}
        for path in candidates:
            rel = path.relative_to(directory).as_posix()
            if not path.resolve().is_relative_to(resolved_dir):
                if rel == "SKILL.md":
                    print(f"[conscio-mcp] skills: skipping {name}: "
                          f"SKILL.md escapes the skill directory",
                          file=sys.stderr)
                    return None
                print(f"[conscio-mcp] skills: dropping {name}/{rel}: "
                      f"symlink escapes the skill directory", file=sys.stderr)
                continue
            try:
                data = path.read_bytes()
            except OSError as exc:
                # An unreadable manifest file excludes the whole skill:
                # silently dropping it would serve a listing the disk
                # cannot back.
                print(f"[conscio-mcp] skills: skipping {name}: cannot "
                      f"read {rel} ({exc})", file=sys.stderr)
                return None
            files[rel] = data
        uri = f"skill://{name}/SKILL.md"
        resources = [{"uri": f"skill://{name}/{rel}",
                      "digest": _digest(data), "size": len(data)}
                     for rel, data in sorted(files.items())]
        entry = {"uri": uri, "frontmatter": frontmatter,
                 "resources": resources}
        return {"entry": entry, "dir": directory, "files": files,
                "dirs": dirs}

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
        ever requested."""
        self._ensure_loaded()
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
