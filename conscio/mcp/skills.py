# conscio/mcp/skills.py
"""MCP Skills extension (SEP-2640): serve the plugin's canonical skills
over MCP as ``skill://`` resources.

Source of truth is the Claude Code plugin assets, NOT
``conscio/agency/skills.py`` (SkillLibrary: procedural memory, a different
concept). The catalog is a snapshot built once per process from disk:
``load()`` constructs the whole dict and only then publishes it, and
``read_file`` serves bytes from the snapshot, never re-reading disk, so a
listing entry and a later read can never disagree.

Limits (SEP-2640, Limits): at most 512 files per skill (``SKILL.md``
included) and at most 16 MiB (16,777,216 bytes) total per skill. A skill
that breaks either is excluded at load with a line on stderr.

Frontmatter is the ``SKILL.md`` YAML frontmatter parsed with
``yaml.safe_load`` and returned verbatim as a dict (SEP-2640, Frontmatter:
"identical in content"; key order, quoting and line folding are NOT
preserved, which the SEP accepts). Duplicate YAML keys take the parser's
behaviour (last wins).

The declared protocol version is at most 2025-06-18, so list results carry
no ``ttlMs``/``cacheScope`` (those arrive with 2026-07-28, SEP-2549).
"""
from __future__ import annotations

import hashlib
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

_MIME_BY_SUFFIX = {
    ".md": "text/markdown",
    ".json": "application/json",
    ".txt": "text/plain",
    ".yaml": "text/plain",
    ".yml": "text/plain",
}


def mime_for(path: Path) -> str:
    return _MIME_BY_SUFFIX.get(path.suffix.lower(), "application/octet-stream")


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


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
            for child in sorted(self.root.iterdir(), key=lambda p: p.name):
                if not child.is_dir():
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
        try:
            raw = skill_md.read_bytes()
        except OSError as exc:
            print(f"[conscio-mcp] skills: skipping {name}: cannot read "
                  f"SKILL.md ({exc})", file=sys.stderr)
            return None
        frontmatter, ok, reason = _parse_frontmatter(raw)
        if not ok:
            print(f"[conscio-mcp] skills: skipping {name}: {reason}",
                  file=sys.stderr)
            return None
        if frontmatter["name"] != name:
            print(f"[conscio-mcp] skills: skipping {name}: frontmatter "
                  f"name {frontmatter['name']!r} != directory name",
                  file=sys.stderr)
            return None
        files: dict[str, bytes] = {}
        total = 0
        for path in sorted(directory.rglob("*"), key=lambda p: str(p)):
            if not path.is_file():
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            rel = path.relative_to(directory).as_posix()
            files[rel] = data
            total += len(data)
        if len(files) > MAX_FILES_PER_SKILL:
            print(f"[conscio-mcp] skills: skipping {name}: "
                  f"{len(files)} files > {MAX_FILES_PER_SKILL}",
                  file=sys.stderr)
            return None
        if total > MAX_BYTES_PER_SKILL:
            print(f"[conscio-mcp] skills: skipping {name}: "
                  f"{total} bytes > {MAX_BYTES_PER_SKILL}",
                  file=sys.stderr)
            return None
        uri = f"skill://{name}/SKILL.md"
        resources = [{"uri": f"skill://{name}/{rel}",
                      "digest": _digest(data), "size": len(data)}
                     for rel, data in sorted(files.items())]
        entry = {"uri": uri, "frontmatter": frontmatter,
                 "resources": resources}
        return {"entry": entry, "dir": directory, "files": files}

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
        """SEP-2640 §4.2. Lookup is by URI in the catalog, so a skill
        answers even if it never appeared in a listing."""
        self._ensure_loaded()
        for skill in self._skills.values():
            if skill["entry"]["uri"] == uri:
                return {"resultType": "complete", "skill": skill["entry"]}
        raise InvalidParams(f"unknown skill '{uri}'")

    def directory_read(self, uri: str, cursor: str | None = None) -> dict:
        """SEP-2640 §Directory Listing: direct children only."""
        self._ensure_loaded()
        _check_cursor(cursor)
        name, directory, root = self._resolve_dir(uri)
        children = []
        for child in sorted(directory.iterdir(), key=lambda p: p.name):
            # Paths are lexical under the resolved root; no resolve() here
            # so a symlink child keeps its own name in the listing.
            rel = child.relative_to(root).as_posix()
            child_uri = f"skill://{name}/{rel}"
            if child.is_dir():
                children.append({"uri": child_uri, "name": child.name,
                                 "mimeType": "inode/directory"})
            elif child.is_file() or child.is_symlink():
                children.append({"uri": child_uri, "name": child.name,
                                 "mimeType": mime_for(child)})
        return {"resultType": "complete", "resources": children}

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

    def _resolve_dir(self, uri: str) -> tuple[str, Path, Path]:
        name, rest = self._parse_uri(uri)
        skill = self._skills.get(name)
        if skill is None:
            raise InvalidParams(f"unknown resource '{uri}'")
        root = skill["dir"].resolve()
        target = (root / Path(*rest)).resolve() if rest else root
        if not target.is_relative_to(root) or not target.is_dir():
            raise InvalidParams(f"unknown resource '{uri}'")
        return name, target, root

    def _parse_uri(self, uri: str) -> tuple[str, tuple[str, ...]]:
        """Strict URI split: scheme ``skill``, first segment is the skill
        name, the rest is the path inside the skill. Any ``..``, ``.``,
        empty segment (double slash / trailing slash), query or fragment
        is rejected before touching the filesystem."""
        if not isinstance(uri, str) or not uri:
            raise InvalidParams(f"unknown resource '{uri}'")
        parsed = urlparse(unquote(uri))
        if parsed.scheme != "skill" or not parsed.netloc \
                or parsed.query or parsed.fragment:
            raise InvalidParams(f"unknown resource '{uri}'")
        raw = f"{parsed.netloc}{parsed.path}"
        if raw.endswith("/"):
            raise InvalidParams(f"unknown resource '{uri}'")
        segments = raw.split("/")
        if not segments or any(s in ("", ".", "..") for s in segments):
            raise InvalidParams(f"unknown resource '{uri}'")
        return segments[0], tuple(segments[1:])

    def _resolve_file(self, uri: str) -> tuple[Path, str]:
        name, rest = self._parse_uri(uri)
        if not rest:
            raise InvalidParams(f"unknown resource '{uri}'")
        skill = self._skills.get(name)
        if skill is None:
            raise InvalidParams(f"unknown resource '{uri}'")
        root = skill["dir"].resolve()
        target = (skill["dir"] / Path(*rest)).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise InvalidParams(f"unknown resource '{uri}'")
        return skill["dir"], Path(*rest).as_posix()


def _check_cursor(cursor: str | None) -> None:
    if cursor is None or cursor == "":
        return
    if not isinstance(cursor, str):
        raise InvalidParams("cursor must be a string")
    # Single-page catalog: any non-empty cursor is unknown (D1).
    raise InvalidParams(f"unknown cursor {cursor!r}")


def _parse_frontmatter(raw: bytes) -> tuple[dict | None, bool, str]:
    """Returns (frontmatter, ok, reason). BOM is stripped, CRLF handled
    by splitlines; the block must be a YAML dict with a valid ``name``
    equal to the directory name and a non-empty ``description`` of at
    most 1024 chars. Extra fields pass through verbatim."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, False, "SKILL.md is not valid UTF-8"
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, False, "missing opening '---'"
    try:
        end = next(i for i, line in enumerate(lines[1:], start=1)
                   if line.strip() == "---")
    except StopIteration:
        return None, False, "missing closing '---'"
    try:
        parsed = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        return None, False, f"invalid YAML frontmatter ({exc})"
    if not isinstance(parsed, dict):
        return None, False, "frontmatter is not a mapping"
    name = parsed.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        return None, False, f"invalid name {name!r}"
    desc = parsed.get("description")
    if not isinstance(desc, str) or not 1 <= len(desc) <= 1024 \
            or not desc.strip():
        return None, False, "missing or invalid description"
    return parsed, True, ""
