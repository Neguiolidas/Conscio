# tests/test_mcp_skills.py
"""MCP Skills extension (SEP-2640): skills/list, skills/get,
resources/read of skill:// URIs and resources/directory/read,
behind the --enable-skills / capability gate.

TDD: written before conscio/mcp/skills.py existed (RED).
"""
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from test_mcp_protocol import FakeBindings

from conscio.agency import MockAdapter
from conscio.engine import ConsciousnessEngine
from conscio.mcp import jsonrpc as j
from conscio.mcp.protocol import Dispatcher
from conscio.mcp.seen import SeenStore
from conscio.mcp.server import Bindings, serve
from conscio.mcp.skills import SkillsCatalog

INIT = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18"}}
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

CANON = Path(__file__).resolve().parent.parent / "conscio" / "integrations" \
    / "claude_code" / "assets" / "skills"


def _bindings(tmp_path, *, skills=True, skills_root=None, script=None):
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    if script is not None:
        eng.attach_adapter(MockAdapter(script=script))
    seen = SeenStore(tmp_path / "mcp_seen.db")
    return (Bindings(eng, seen, adapter_name="mock" if script else None,
                     workspace_id="ws123", mode="ultra",
                     skills=skills, skills_root=skills_root),
            eng, seen)


def _run(bindings, requests):
    out = io.StringIO()
    serve(bindings, io.StringIO("".join(json.dumps(r) + "\n" for r in requests)),
          out)
    return [json.loads(x) for x in out.getvalue().splitlines() if x]


def _req(mid, method, params=None):
    r = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        r["params"] = params
    return r


def _frontmatter(path: Path) -> dict:
    # Independent of the implementation's line splitting: regex-extract
    # the --- block (BOM + CRLF tolerated) and parse it. Sharing the
    # implementation's split primitive would let a split bug pass here.
    text = path.read_text("utf-8")
    m = re.match(r"\ufeff?---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.DOTALL)
    assert m, f"no frontmatter in {path}"
    return yaml.safe_load(m.group(1))


def _skill_files(root: Path, name: str, *, frontmatter=None, extra=None):
    """Build one valid skill dir under root/<name>/."""
    d = root / name
    d.mkdir(parents=True)
    fm = frontmatter if frontmatter is not None else {
        "name": name, "description": f"Skill {name} for tests."}
    body = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n# Title\n"
    (d / "SKILL.md").write_text(body, "utf-8")
    for rel, data in (extra or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            p.write_text(data, "utf-8")
        else:
            p.write_bytes(data)
    return d


def _restore_locked(root: Path, *paths: Path) -> None:
    """Restore readable perms on a locked skill dir wherever it ended up
    (still in place, or moved to <root>/.quarantine), so tmp_path
    cleanup can remove it. Recurses: a chmod-000 subdirectory is
    restored before descending into it, or its contents survive and
    pytest cleanup warns (rm_rf "Directory not empty").
    Best-effort: a missing path is ignored."""
    qdir = root / ".quarantine"
    candidates = list(paths)
    if qdir.is_dir():
        candidates += list(qdir.iterdir())
    for p in candidates:
        _chmod_tree(p)


def _chmod_tree(path: Path) -> None:
    """Recursively chmod a tree to 0o755 (dirs) / 0o644 (files),
    restoring a chmod-000 directory before listing it. Symlinks are
    left alone. Best-effort: errors are ignored."""
    if path.is_symlink():
        return
    if path.is_dir():
        try:
            os.chmod(path, 0o755)
        except OSError:
            pass
        try:
            entries = list(path.iterdir())
        except OSError:
            return
        for entry in entries:
            _chmod_tree(entry)
    else:
        try:
            os.chmod(path, 0o644)
        except OSError:
            pass


# ---------------------------------------------------------------- catalog wire

def test_list_returns_both_canonical_skills_complete(tmp_path):
    b, _, _ = _bindings(tmp_path)
    res = _run(b, [INIT, _req(1, "skills/list")])
    assert res[1]["result"]["resultType"] == "complete"
    skills = res[1]["result"]["skills"]
    assert {s["uri"] for s in skills} == {
        "skill://conscio/SKILL.md", "skill://conscio-relay/SKILL.md"}
    assert "nextCursor" not in res[1]["result"]
    assert "ttlMs" not in res[1]["result"]
    assert "cacheScope" not in res[1]["result"]
    for s in skills:
        name = s["uri"].split("/")[2]
        assert s["frontmatter"] == _frontmatter(CANON / name / "SKILL.md")
        assert s["uri"].rsplit("/", 1)[-1] == "SKILL.md"
        assert s["uri"].rsplit("/", 2)[-2] == s["frontmatter"]["name"]
        assert len(s["resources"]) == 1
        r = s["resources"][0]
        raw = (CANON / name / "SKILL.md").read_bytes()
        assert r["uri"] == s["uri"]
        assert DIGEST_RE.match(r["digest"])
        assert r["digest"] == "sha256:" + hashlib.sha256(raw).hexdigest()
        assert r["size"] == len(raw)


def test_get_answers_for_both_uris(tmp_path):
    b, _, _ = _bindings(tmp_path)
    res = _run(b, [INIT,
                   _req(1, "skills/get", {"uri": "skill://conscio/SKILL.md"}),
                   _req(2, "skills/get",
                        {"uri": "skill://conscio-relay/SKILL.md"})])
    for r, name in zip(res[1:], ("conscio", "conscio-relay")):
        assert r["result"]["resultType"] == "complete"
        s = r["result"]["skill"]
        assert s["uri"] == f"skill://{name}/SKILL.md"
        assert s["frontmatter"] == _frontmatter(CANON / name / "SKILL.md")
        assert "ttlMs" not in r["result"]


def test_get_errors(tmp_path):
    b, _, _ = _bindings(tmp_path)
    res = _run(b, [INIT,
                   _req(1, "skills/get", {"uri": "skill://nope/SKILL.md"}),
                   _req(2, "skills/get", {"uri": "skill://conscio"}),
                   _req(3, "skills/get", {}),
                   _req(4, "skills/get", {"uri": 42}),
                   _req(5, "skills/get"),
                   _req(6, "skills/get", {"uri": "conscio://advisory"})])
    for r in res[1:]:
        assert r["error"]["code"] == j.INVALID_PARAMS, r


def test_list_cursor_rules(tmp_path):
    b, _, _ = _bindings(tmp_path)
    res = _run(b, [INIT,
                   _req(1, "skills/list", {"cursor": ""}),
                   _req(2, "skills/list", {"cursor": "bogus"}),
                   _req(3, "skills/list", {"cursor": 7}),
                   _req(4, "skills/list", {"cursor": None})])
    assert res[1]["result"]["skills"]
    assert res[2]["error"]["code"] == j.INVALID_PARAMS
    assert res[3]["error"]["code"] == j.INVALID_PARAMS
    # cursor: null is JSON null -> params.get returns None -> first page
    assert res[4]["result"]["skills"]


def test_resources_read_returns_exact_bytes(tmp_path):
    b, _, _ = _bindings(tmp_path)
    res = _run(b, [INIT, _req(1, "resources/read",
                              {"uri": "skill://conscio/SKILL.md"})])
    c = res[1]["result"]["contents"][0]
    assert c["uri"] == "skill://conscio/SKILL.md"
    assert c["mimeType"] == "text/markdown"
    assert c["text"].encode("utf-8") == \
        (CANON / "conscio" / "SKILL.md").read_bytes()
    assert "sha256:" + hashlib.sha256(c["text"].encode("utf-8")).hexdigest() \
        == "sha256:" + hashlib.sha256(
            (CANON / "conscio" / "SKILL.md").read_bytes()).hexdigest()


def test_resources_read_errors(tmp_path):
    b, _, _ = _bindings(tmp_path)
    res = _run(b, [INIT,
                   _req(1, "resources/read", {"uri": "skill://conscio"}),
                   _req(2, "resources/read",
                        {"uri": "skill://x/SKILL.md"}),
                   _req(3, "resources/read",
                        {"uri": "skill://conscio/../conscio-relay/SKILL.md"}),
                   _req(4, "resources/read",
                        {"uri": "skill://conscio/%2e%2e/conscio-relay/SKILL.md"}),
                   _req(5, "resources/read",
                        {"uri": "skill://conscio//SKILL.md"}),
                   _req(6, "resources/read",
                        {"uri": "skill://conscio/./SKILL.md"})])
    for r in res[1:]:
        assert r["error"]["code"] == j.INVALID_PARAMS, r


def test_resources_read_symlink_escape_rejected(tmp_path):
    root = tmp_path / "skills"
    outside = tmp_path / "secret.txt"
    outside.write_text("do not serve", "utf-8")
    d = _skill_files(root, "linky")
    (d / "escape.txt").symlink_to(outside)
    b, _, _ = _bindings(tmp_path, skills_root=root)
    res = _run(b, [INIT, _req(1, "resources/read",
                              {"uri": "skill://linky/escape.txt"})])
    assert res[1]["error"]["code"] == j.INVALID_PARAMS


def test_resources_read_rejects_nul_and_nonstring_uri(tmp_path):
    # Hostile review #3: %00 / literal NUL and non-string uris must be
    # -32602 (InvalidParams), never -32603 (InternalError).
    b, _, _ = _bindings(tmp_path)
    res = _run(b, [INIT,
                   _req(1, "resources/read",
                        {"uri": "skill://conscio/%00/SKILL.md"}),
                   _req(2, "resources/read",
                        {"uri": "skill://conscio/\x00/SKILL.md"}),
                   _req(3, "resources/read", {"uri": 42}),
                   _req(4, "resources/read",
                        {"uri": ["skill://conscio/SKILL.md"]})])
    for r in res[1:]:
        assert r["error"]["code"] == j.INVALID_PARAMS, r


def test_directory_read_lists_direct_child(tmp_path):
    b, _, _ = _bindings(tmp_path)
    res = _run(b, [INIT,
                   _req(1, "resources/directory/read",
                        {"uri": "skill://conscio"}),
                   _req(2, "resources/directory/read",
                        {"uri": "skill://conscio-relay"}),
                   _req(3, "resources/directory/read",
                        {"uri": "skill://conscio", "cursor": ""})])
    for r in res[1:]:
        assert r["result"]["resultType"] == "complete"
        assert len(r["result"]["resources"]) == 1
        child = r["result"]["resources"][0]
        assert child["name"] == "SKILL.md"
        assert child["mimeType"] == "text/markdown"
    assert res[1]["result"]["resources"][0]["uri"] == \
        "skill://conscio/SKILL.md"


def test_directory_read_errors(tmp_path):
    b, _, _ = _bindings(tmp_path)
    res = _run(b, [INIT,
                   _req(1, "resources/directory/read",
                        {"uri": "skill://nope"}),
                   _req(2, "resources/directory/read",
                        {"uri": "skill://conscio/SKILL.md"}),
                   _req(3, "resources/directory/read",
                        {"uri": "skill://conscio", "cursor": "bogus"}),
                   _req(4, "resources/directory/read", {}),
                   _req(5, "resources/directory/read",
                        {"uri": "skill://conscio/../conscio-relay"})])
    for r in res[1:]:
        assert r["error"]["code"] == j.INVALID_PARAMS, r


def test_directory_read_subdirectory(tmp_path):
    root = tmp_path / "skills"
    _skill_files(root, "multi",
                 extra={"refs/guide.md": "# guide\n",
                        "scripts/run.py": "print(1)\n"})
    b, _, _ = _bindings(tmp_path, skills_root=root)
    res = _run(b, [INIT,
                   _req(1, "resources/directory/read",
                        {"uri": "skill://multi"}),
                   _req(2, "resources/directory/read",
                        {"uri": "skill://multi/refs"}),
                   _req(3, "skills/list")])
    kids = {c["name"]: c for c in res[1]["result"]["resources"]}
    assert set(kids) == {"SKILL.md", "refs", "scripts"}
    assert kids["refs"]["mimeType"] == "inode/directory"
    assert kids["refs"]["uri"] == "skill://multi/refs"
    assert kids["scripts"]["mimeType"] == "inode/directory"
    assert kids["SKILL.md"]["mimeType"] == "text/markdown"
    assert [c["name"] for c in res[2]["result"]["resources"]] == ["guide.md"]
    by_uri = {s["uri"]: s for s in res[3]["result"]["skills"]}
    entry = by_uri["skill://multi/SKILL.md"]
    assert len(entry["resources"]) == 3
    assert {r["uri"] for r in entry["resources"]} == {
        "skill://multi/SKILL.md", "skill://multi/refs/guide.md",
        "skill://multi/scripts/run.py"}


def test_skills_methods_reject_non_dict_params(tmp_path):
    # Hostile review #4: non-dict params must be -32602, not a silent
    # success via coercion to {}. initialize keeps its pre-existing
    # leniency (documented in the CHANGELOG, out of scope here).
    b, _, _ = _bindings(tmp_path)
    res = _run(b, [INIT,
                   _req(1, "skills/list", "hello"),
                   _req(2, "skills/list", [1, 2]),
                   _req(3, "skills/get", "hello"),
                   _req(4, "resources/directory/read", [1, 2])])
    for r in res[1:]:
        assert r["error"]["code"] == j.INVALID_PARAMS, r


# ---------------------------------------------------------------- gate

def test_gate_off(tmp_path):
    b, _, _ = _bindings(tmp_path, skills=False)
    res = _run(b, [INIT,
                   _req(1, "skills/list"),
                   _req(2, "skills/get", {"uri": "skill://conscio/SKILL.md"}),
                   _req(3, "resources/directory/read",
                        {"uri": "skill://conscio"}),
                   _req(4, "resources/read",
                        {"uri": "skill://conscio/SKILL.md"}),
                   _req(5, "tools/list")])
    assert "extensions" not in res[0]["result"]["capabilities"]
    for r in res[1:4]:
        assert r["error"]["code"] == j.METHOD_NOT_FOUND, r
    assert res[4]["error"]["code"] == j.INVALID_PARAMS
    # tools/list still answers with the gate off.
    assert isinstance(res[5]["result"]["tools"], list)
    assert res[5]["result"]["tools"]


def test_gate_on_tools_list_identical(tmp_path):
    b_on, _, _ = _bindings(tmp_path, skills=True)
    on = _run(b_on, [INIT, _req(1, "tools/list")])
    b_off, _, _ = _bindings(tmp_path, skills=False)
    off = _run(b_off, [INIT, _req(1, "tools/list")])
    assert on[1]["result"] == off[1]["result"]
    assert "io.modelcontextprotocol/skills" in \
        on[0]["result"]["capabilities"]["extensions"]
    assert on[0]["result"]["capabilities"]["extensions"][
        "io.modelcontextprotocol/skills"] == {"directoryRead": True}
    assert on[0]["result"]["capabilities"]["resources"] == {}


def test_resources_list_identical_gate_on_off(tmp_path):
    # Hostile review #13e: the gate must not change resources/list.
    b_on, _, _ = _bindings(tmp_path, skills=True)
    on = _run(b_on, [INIT, _req(1, "resources/list")])
    b_off, _, _ = _bindings(tmp_path, skills=False)
    off = _run(b_off, [INIT, _req(1, "resources/list")])
    assert on[1]["result"] == off[1]["result"]


def test_gate_persists_without_flag(tmp_path):
    # Hostile review #13g: a capability persisted in storage turns the
    # gate on at the next boot with no --enable-skills flag.
    from conscio.mcp.capabilities import resolve_capability, write_capabilities
    storage = tmp_path / "storage"
    storage.mkdir()
    write_capabilities(storage, {"skills"})
    assert resolve_capability(storage, "skills", False) is True
    b, _, _ = _bindings(
        tmp_path,
        skills=resolve_capability(storage, "skills", False))
    res = _run(b, [INIT, _req(1, "skills/list")])
    assert "io.modelcontextprotocol/skills" in \
        res[0]["result"]["capabilities"]["extensions"]
    assert res[1]["result"]["skills"]


def test_initialize_future_version_falls_back_and_no_cache_attrs(tmp_path):
    b, _, _ = _bindings(tmp_path)
    res = _run(b, [{"jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"protocolVersion": "2026-07-28"}},
                   _req(1, "skills/list")])
    assert res[0]["result"]["protocolVersion"] == "2025-06-18"
    assert "ttlMs" not in res[1]["result"]
    assert "cacheScope" not in res[1]["result"]


def test_gate_off_via_protocol_fake():
    fb = FakeBindings()
    fb.skills_enabled = False
    d = Dispatcher(fb)
    d.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18"}})
    r = d.handle({"jsonrpc": "2.0", "id": 1, "method": "skills/list"})
    assert r["error"]["code"] == j.METHOD_NOT_FOUND
    r = d.handle({"jsonrpc": "2.0", "id": 2, "method": "skills/get",
                  "params": {"uri": "skill://conscio/SKILL.md"}})
    assert r["error"]["code"] == j.METHOD_NOT_FOUND


def test_protocol_fake_lists_and_gets():
    d = Dispatcher(FakeBindings())
    d.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18"}})
    r = d.handle({"jsonrpc": "2.0", "id": 1, "method": "skills/list"})
    assert {s["uri"] for s in r["result"]["skills"]} == {
        "skill://conscio/SKILL.md", "skill://conscio-relay/SKILL.md"}
    r = d.handle({"jsonrpc": "2.0", "id": 2,
                  "method": "resources/directory/read",
                  "params": {"uri": "skill://conscio"}})
    assert r["result"]["resources"][0]["uri"] == "skill://conscio/SKILL.md"
    # non-dict params are rejected with InvalidParams, not coerced.
    r = d.handle({"jsonrpc": "2.0", "id": 3, "method": "skills/get",
                  "params": "not-a-dict"})
    assert r["error"]["code"] == j.INVALID_PARAMS


# ---------------------------------------------------------------- catalog rules

def _catalog(tmp_path, build):
    root = tmp_path / "skills"
    root.mkdir()
    build(root)
    return SkillsCatalog(root=root)


def test_catalog_excludes_invalid_frontmatter(tmp_path):
    def build(root):
        _skill_files(root, "good")
        _skill_files(root, "noclose", frontmatter={
            "name": "noclose", "description": "x"})
        # strip the closing --- from noclose
        p = root / "noclose" / "SKILL.md"
        p.write_text(p.read_text().replace("---\n\n# Title\n", "\n# Title\n"))
        _skill_files(root, "wrongname", frontmatter={
            "name": "other", "description": "x"})
        _skill_files(root, "nodesc", frontmatter={"name": "nodesc"})
        _skill_files(root, "bad--name", frontmatter={
            "name": "bad--name", "description": "x"})
        _skill_files(root, "x" * 65, frontmatter={
            "name": "x" * 65, "description": "x"})
        _skill_files(root, "emptydesc", frontmatter={
            "name": "emptydesc", "description": "   "})
        _skill_files(root, "longdesc", frontmatter={
            "name": "longdesc", "description": "y" * 1025})
    cat = _catalog(tmp_path, build)
    assert cat.list_skills()["skills"][0]["uri"] == "skill://good/SKILL.md"
    assert len(cat.list_skills()["skills"]) == 1


def test_catalog_excludes_non_json_frontmatter(tmp_path):
    # Hostile review #1: yaml.safe_load produces datetime.date / bytes,
    # which json.dumps cannot serialize; the skill must be excluded.
    def build(root):
        _skill_files(root, "good")
        d = root / "dated"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            "---\nname: dated\ndescription: x\nreleased: 2026-11-07\n"
            "---\n\n# T\n", "utf-8")
        d2 = root / "bindata"
        d2.mkdir(parents=True)
        (d2 / "SKILL.md").write_text(
            "---\nname: bindata\ndescription: x\ndata: !!binary aGk=\n"
            "---\n\n# T\n", "utf-8")
    cat = _catalog(tmp_path, build)
    assert [s["uri"] for s in cat.list_skills()["skills"]] == \
        ["skill://good/SKILL.md"]


def test_catalog_excludes_deeply_nested_frontmatter(tmp_path):
    # Hostile review #2: 2000 levels of nesting raise RecursionError in
    # yaml.safe_load; only that skill may die, never the catalog.
    def build(root):
        _skill_files(root, "good")
        d = root / "deep"
        d.mkdir(parents=True)
        nested = "[" * 2000 + "]" * 2000
        (d / "SKILL.md").write_text(
            f"---\nname: deep\ndescription: x\nnested: {nested}\n"
            "---\n\n# T\n", "utf-8")
    cat = _catalog(tmp_path, build)
    assert [s["uri"] for s in cat.list_skills()["skills"]] == \
        ["skill://good/SKILL.md"]


def test_frontmatter_yaml_coercions_are_json_values(tmp_path):
    # Hostile review #1, documented side: YAML's lossy coercions are
    # judged on the parsed value (flag: yes -> True, 1.10 -> 1.1).
    root = tmp_path / "skills"
    d = root / "coerced"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: coerced\ndescription: x\nflag: yes\nversion: 1.10\n"
        "---\n\n# T\n", "utf-8")
    cat = SkillsCatalog(root=root)
    skills = cat.list_skills()["skills"]
    assert len(skills) == 1
    assert skills[0]["frontmatter"]["flag"] is True
    assert skills[0]["frontmatter"]["version"] == 1.1


def test_catalog_excludes_oversize_skills(tmp_path):
    def build(root):
        _skill_files(root, "good")
        d = _skill_files(root, "many")
        for i in range(513):
            (d / f"f{i:03}.txt").write_text("", "utf-8")
        d2 = _skill_files(root, "huge")
        (d2 / "big.bin").write_bytes(b"\0" * (17 * 1024 * 1024))
    cat = _catalog(tmp_path, build)
    assert [s["uri"] for s in cat.list_skills()["skills"]] == \
        ["skill://good/SKILL.md"]


def test_boundary_512_files_in_513_out(tmp_path):
    # Hostile review #13c: exact file-count boundary (SKILL.md included).
    root = tmp_path / "skills"
    d = _skill_files(root, "edge512")
    for i in range(511):
        (d / f"f{i:03}.txt").write_text("", "utf-8")
    d2 = _skill_files(root, "over513")
    for i in range(512):
        (d2 / f"f{i:03}.txt").write_text("", "utf-8")
    cat = SkillsCatalog(root=root)
    assert [s["uri"] for s in cat.list_skills()["skills"]] == \
        ["skill://edge512/SKILL.md"]


def test_boundary_16mib_in_plus_one_out(tmp_path):
    # Hostile review #13c: exact total-bytes boundary (SKILL.md included).
    from conscio.mcp.skills import MAX_BYTES_PER_SKILL
    root = tmp_path / "skills"
    d = _skill_files(root, "exact")
    skill_size = (d / "SKILL.md").stat().st_size
    (d / "big.bin").write_bytes(b"\0" * (MAX_BYTES_PER_SKILL - skill_size))
    d2 = _skill_files(root, "over")
    skill_size2 = (d2 / "SKILL.md").stat().st_size
    (d2 / "big.bin").write_bytes(
        b"\0" * (MAX_BYTES_PER_SKILL - skill_size2 + 1))
    cat = SkillsCatalog(root=root)
    assert [s["uri"] for s in cat.list_skills()["skills"]] == \
        ["skill://exact/SKILL.md"]


def test_catalog_accepts_bom_and_crlf_and_extra_fields(tmp_path):
    def build(root):
        d = root / "bom"
        d.mkdir(parents=True)
        fm = {"name": "bom", "description": "BOM + CRLF skill.",
              "license": "MIT"}
        text = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n# T\n"
        (d / "SKILL.md").write_bytes(b"\xef\xbb\xbf" +
                                     text.replace("\n", "\r\n").encode())
    cat = _catalog(tmp_path, build)
    skills = cat.list_skills()["skills"]
    assert len(skills) == 1
    assert skills[0]["frontmatter"]["license"] == "MIT"


def test_catalog_binary_file_reads_as_blob(tmp_path):
    root = tmp_path / "skills"
    png = b"\x89PNG\r\n\x1a\n\x00\xff\xfe binary"
    _skill_files(root, "bin", extra={"img.png": png})
    b, _, _ = _bindings(tmp_path, skills_root=root)
    res = _run(b, [INIT, _req(1, "resources/read",
                              {"uri": "skill://bin/img.png"})])
    c = res[1]["result"]["contents"][0]
    assert c["mimeType"] == "image/png"
    assert base64.b64decode(c["blob"]) == png
    assert "text" not in c


def test_mime_types_map_suffixes(tmp_path):
    # Cycle-2 item 10: only the 5-suffix map is pinned here. Anything
    # else comes from the platform stdlib mimetypes (see CHANGELOG) and
    # is deliberately not asserted: its answers vary by platform.
    from conscio.mcp.skills import mime_for
    assert mime_for(Path("x.md")) == "text/markdown"
    assert mime_for(Path("x.json")) == "application/json"
    assert mime_for(Path("x.txt")) == "text/plain"
    assert mime_for(Path("x.yaml")) == "text/plain"
    assert mime_for(Path("x.yml")) == "text/plain"


def test_catalog_empty_root_lists_nothing(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    cat = SkillsCatalog(root=root)
    assert cat.list_skills() == {"resultType": "complete", "skills": []}
    with pytest.raises(j.InvalidParams):
        cat.get_skill("skill://x/SKILL.md")


def test_digest_is_sensitive_to_one_byte(tmp_path):
    # Hostile review #13a: flip one byte of a catalogued SKILL.md and
    # prove the digest in skills/list changes with it.
    root = tmp_path / "skills"
    d = _skill_files(root, "onebyte")
    cat = SkillsCatalog(root=root)
    before = cat.list_skills()["skills"][0]["resources"][0]
    assert before["uri"] == "skill://onebyte/SKILL.md"
    raw = (d / "SKILL.md").read_bytes()
    bad = bytearray(raw)
    # bad[-7] is the space in "# Title": "# Title" -> "#UTitle",
    # one ASCII byte, still valid.
    bad[-7] = ord("U")
    (d / "SKILL.md").write_bytes(bytes(bad))
    cat2 = SkillsCatalog(root=root)
    after = cat2.list_skills()["skills"][0]["resources"][0]
    assert after["digest"] != before["digest"]
    assert after["digest"] == "sha256:" + hashlib.sha256(bytes(bad)).hexdigest()
    assert after["size"] == len(bad)


def test_catalog_snapshot_does_not_reread_disk(tmp_path):
    root = tmp_path / "skills"
    d = _skill_files(root, "snap")
    cat = SkillsCatalog(root=root)
    assert len(cat.list_skills()["skills"]) == 1
    (d / "SKILL.md").write_text("---\nname: snap\ndescription: changed\n"
                                "---\n\n# changed\n", "utf-8")
    data, _ = cat.read_file("skill://snap/SKILL.md")
    assert b"changed" not in data


def test_directory_read_serves_snapshot_not_disk(tmp_path):
    # Hostile review #6: directory_read reads the snapshot, never disk.
    root = tmp_path / "skills"
    d = _skill_files(root, "snap", extra={"old.txt": "old\n"})
    cat = SkillsCatalog(root=root)
    assert [c["name"] for c in
            cat.directory_read("skill://snap")["resources"]] == \
        ["SKILL.md", "old.txt"]
    (d / "new.txt").write_text("new\n", "utf-8")
    (d / "old.txt").unlink()
    after = cat.directory_read("skill://snap")["resources"]
    assert [c["name"] for c in after] == ["SKILL.md", "old.txt"]
    data, _ = cat.read_file("skill://snap/old.txt")
    assert data == b"old\n"
    with pytest.raises(j.InvalidParams):
        cat.read_file("skill://snap/new.txt")


def test_symlink_file_dropped_from_manifest(tmp_path):
    # Hostile review #5: an escaping symlink file must not enter the
    # manifest (no digest/size of external content) nor directory_read,
    # while the skill itself survives.
    root = tmp_path / "skills"
    outside = tmp_path / "secret.txt"
    outside.write_text("do not serve", "utf-8")
    d = _skill_files(root, "linky", extra={"refs/real.md": "# real\n"})
    (d / "escape.txt").symlink_to(outside)
    b, _, _ = _bindings(tmp_path, skills_root=root)
    res = _run(b, [INIT, _req(1, "skills/list"),
                   _req(2, "resources/directory/read",
                        {"uri": "skill://linky"}),
                   _req(3, "resources/read",
                        {"uri": "skill://linky/escape.txt"})])
    by_uri = {s["uri"]: s for s in res[1]["result"]["skills"]}
    entry = by_uri["skill://linky/SKILL.md"]
    assert {r["uri"] for r in entry["resources"]} == {
        "skill://linky/SKILL.md", "skill://linky/refs/real.md"}
    assert {c["name"] for c in res[2]["result"]["resources"]} == {
        "SKILL.md", "refs"}
    assert res[3]["error"]["code"] == j.INVALID_PARAMS


def test_symlink_skill_md_and_skill_dir_escapes_excluded(tmp_path):
    # Hostile review #5: SKILL.md escaping excludes the skill; a skill
    # directory symlink escaping SKILLS_ROOT excludes the skill too.
    root = tmp_path / "skills"
    _skill_files(root, "good")
    outside_md = tmp_path / "outside.md"
    outside_md.write_text("---\nname: linkmd\ndescription: x\n---\n\n# T\n",
                          "utf-8")
    d = root / "linkmd"
    d.mkdir()
    (d / "SKILL.md").symlink_to(outside_md)
    _skill_files(tmp_path, "ext")
    (root / "ext").symlink_to(tmp_path / "ext", target_is_directory=True)
    cat = SkillsCatalog(root=root)
    assert [s["uri"] for s in cat.list_skills()["skills"]] == \
        ["skill://good/SKILL.md"]


def test_unreadable_file_quarantines_skill(tmp_path, capsys):
    # Hostile review #8: a chmod-000 manifest file excludes the whole
    # skill from the listing, it does not vanish from the manifest
    # silently. It is an access failure, so the skill is quarantined
    # (and repairs lazily on its next invocation).
    if os.geteuid() == 0:
        pytest.skip("root can read chmod-000 files")
    root = tmp_path / "skills"
    d = _skill_files(root, "locked", extra={"notes.txt": "secret\n"})
    _skill_files(root, "good")
    locked = d / "notes.txt"
    locked.chmod(0o000)
    try:
        cat = SkillsCatalog(root=root)
        assert [s["uri"] for s in cat.list_skills()["skills"]] == \
            ["skill://good/SKILL.md"]
    finally:
        _restore_locked(root, d)
    assert "quarantined 'locked'" in capsys.readouterr().err
    assert not (root / "locked").exists()
    assert len(list((root / ".quarantine").iterdir())) == 1


def test_wire_temp_catalog_mixed_valid_invalid(tmp_path):
    root = tmp_path / "skills"
    _skill_files(root, "good")
    _skill_files(root, "bad", frontmatter={"name": "bad"})
    b, _, _ = _bindings(tmp_path, skills_root=root)
    res = _run(b, [INIT, _req(1, "skills/list")])
    assert [s["uri"] for s in res[1]["result"]["skills"]] == [
        "skill://conscio-relay/SKILL.md",
        "skill://conscio/SKILL.md",
        "skill://good/SKILL.md",
    ]


def test_conscio_meta_reports_skills_enabled(tmp_path):
    b, _, _ = _bindings(tmp_path, skills=True)
    assert b.conscio_meta()["skills_enabled"] is True
    b2, _, _ = _bindings(tmp_path, skills=False)
    assert b2.conscio_meta()["skills_enabled"] is False


def test_non_utf8_skill_file_excluded(tmp_path):
    root = tmp_path / "skills"
    d = root / "badbytes"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_bytes(b"\xff\xfe invalid utf-8")
    cat = SkillsCatalog(root=root)
    assert cat.list_skills()["skills"] == []


def test_main_accepts_enable_skills_flag(tmp_path):
    from conscio.mcp.server import _arg_parser
    args = _arg_parser().parse_args(["--enable-skills"])
    assert args.enable_skills is True
    assert _arg_parser().parse_args([]).enable_skills is False


# ------------------------------------------------------- cycle-2 hostile review

def test_catalog_excludes_non_finite_frontmatter(tmp_path):
    # Cycle-2 item 1: .nan / .inf / 1.0e+999 parse to non-finite floats,
    # which json.dumps would emit as NaN/Infinity (invalid JSON); the
    # skill must be excluded. (Bare 1e999 parses as a string in PyYAML
    # 6.0.3, so the overflow spelling is 1.0e+999.)
    def build(root):
        _skill_files(root, "good")
        _skill_files(root, "nan", frontmatter={
            "name": "nan", "description": "x", "v": float("nan")})
        _skill_files(root, "inf", frontmatter={
            "name": "inf", "description": "x", "v": float("inf")})
        d = root / "overflow"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            "---\nname: overflow\ndescription: x\nv: 1.0e+999\n---\n\n# T\n",
            "utf-8")
    cat = _catalog(tmp_path, build)
    assert [s["uri"] for s in cat.list_skills()["skills"]] == [
        "skill://good/SKILL.md"]


def test_malformed_uri_is_invalid_params(tmp_path):
    # Cycle-2 item 2: urlparse raises ValueError on a malformed URI
    # (e.g. an unterminated IPv6 literal); the wire answer must be
    # -32602, never -32603.
    b, _, _ = _bindings(tmp_path)
    res = _run(b, [INIT,
                   _req(1, "resources/read", {"uri": "skill://[::1/x"}),
                   _req(2, "resources/directory/read",
                        {"uri": "skill://[::1/x"})])
    for r in res[1:]:
        assert r["error"]["code"] == j.INVALID_PARAMS, r


def test_skills_methods_reject_falsy_non_dict_params(tmp_path):
    # Cycle-2 item 3: falsy non-dict params ([], "", 0) must be -32602,
    # not silently coerced to {} the way the old handle() did it.
    b, _, _ = _bindings(tmp_path)
    res = _run(b, [INIT,
                   _req(1, "skills/list", []),
                   _req(2, "skills/list", ""),
                   _req(3, "skills/list", 0)])
    for r in res[1:]:
        assert r["error"]["code"] == j.INVALID_PARAMS, r
    # ...while params-absent requests keep working: initialize and ping
    # without params, and skills/list without params.
    res = _run(b, [{"jsonrpc": "2.0", "id": 0, "method": "initialize"},
                   _req(1, "ping"),
                   _req(2, "skills/list")])
    assert res[1]["result"] == {}
    assert res[2]["result"]["skills"]


def test_oversize_skill_md_not_read(tmp_path, monkeypatch):
    # Cycle-2 item 4: a SKILL.md that alone exceeds the 16 MiB cap is
    # excluded by stat() before any read_bytes on it.
    from conscio.mcp.skills import MAX_BYTES_PER_SKILL
    root = tmp_path / "skills"
    d = _skill_files(root, "giant")
    (d / "SKILL.md").write_bytes(b"x" * (MAX_BYTES_PER_SKILL + 1))
    orig = Path.read_bytes
    reads = []

    def spy(self):
        reads.append(str(self))
        return orig(self)

    monkeypatch.setattr(Path, "read_bytes", spy)
    cat = SkillsCatalog(root=root)
    assert cat.list_skills()["skills"] == []
    assert not any(r.endswith("SKILL.md") for r in reads)


def test_precheck_ignores_escaping_symlinks(tmp_path):
    # Cycle-2 item 5: the limits pre-check must not count symlinks that
    # escape the skill directory (the manifest drops them anyway).
    root = tmp_path / "skills"
    outside = tmp_path / "outside.bin"
    with open(outside, "wb") as f:
        f.truncate(17 * 1024 * 1024)  # sparse: st_size 17 MiB, ~no disk
    d = _skill_files(root, "edge")
    for i in range(511):
        (d / f"f{i:03}.txt").write_text("", "utf-8")
    (d / "escape.bin").symlink_to(outside)
    cat = SkillsCatalog(root=root)
    skills = cat.list_skills()["skills"]
    assert [s["uri"] for s in skills] == ["skill://edge/SKILL.md"]
    # 512 real files (SKILL.md + 511); the escaping symlink is in neither
    # the count nor the byte sum, and not in the manifest.
    assert len(skills[0]["resources"]) == 512


def test_directory_read_lists_empty_subdirectory(tmp_path):
    # Cycle-2 item 6: empty directories are recorded in the snapshot and
    # listed (inode/directory); reading one answers an empty list.
    root = tmp_path / "skills"
    d = _skill_files(root, "multi", extra={"refs/guide.md": "# guide\n"})
    (d / "empty").mkdir()
    b, _, _ = _bindings(tmp_path, skills_root=root)
    res = _run(b, [INIT,
                   _req(1, "resources/directory/read",
                        {"uri": "skill://multi"}),
                   _req(2, "resources/directory/read",
                        {"uri": "skill://multi/empty"})])
    kids = {c["name"]: c for c in res[1]["result"]["resources"]}
    assert set(kids) == {"SKILL.md", "refs", "empty"}
    assert kids["empty"]["mimeType"] == "inode/directory"
    assert kids["empty"]["uri"] == "skill://multi/empty"
    assert res[2]["result"] == {"resultType": "complete", "resources": []}


def test_lone_cr_frontmatter_rejected(tmp_path):
    # Cycle-2 item 7 (documented behaviour): only CRLF and LF split
    # frontmatter lines; a CR-only SKILL.md fails frontmatter parsing
    # and the skill is excluded.
    root = tmp_path / "skills"
    d = root / "cronly"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_bytes(
        b"---\rname: cronly\rdescription: x\r---\r\r# T\r")
    cat = SkillsCatalog(root=root)
    assert cat.list_skills()["skills"] == []


def test_catalog_excludes_cyclic_alias_frontmatter(tmp_path):
    # Cycle-2 item 8: a cyclic alias parses to a self-referential dict,
    # so _is_json_value raises RecursionError OUTSIDE the safe_load try
    # in _parse_frontmatter; only _load_skill's outer wrapper saves the
    # catalog. (The 2000-bracket input never reaches that wrapper.)
    from conscio.mcp.skills import _parse_frontmatter
    root = tmp_path / "skills"
    d = root / "cyclic"
    d.mkdir(parents=True)
    raw = (b"---\nname: cyclic\ndescription: x\na: &x {b: *x}\n"
           b"---\n\n# T\n")
    (d / "SKILL.md").write_bytes(raw)
    with pytest.raises(RecursionError):
        _parse_frontmatter(raw)
    _skill_files(root, "good")
    cat = SkillsCatalog(root=root)
    assert [s["uri"] for s in cat.list_skills()["skills"]] == [
        "skill://good/SKILL.md"]


def test_frontmatter_block_over_64kib_excluded(tmp_path):
    # Cycle-2 item 12 (viable half): the frontmatter block itself is
    # capped at 64 KiB before parsing.
    root = tmp_path / "skills"
    d = root / "hugefm"
    d.mkdir(parents=True)
    big = "x" * 70_000
    (d / "SKILL.md").write_text(
        f"---\nname: hugefm\ndescription: x\nbig: {big}\n---\n\n# T\n",
        "utf-8")
    cat = SkillsCatalog(root=root)
    assert cat.list_skills()["skills"] == []


# ------------------------------------------------------- --skills-root


def test_user_root_unions_with_bundled(tmp_path):
    root = tmp_path / "user-skills"
    _skill_files(root, "mine", extra={"notes.txt": "hello\n"})
    _skill_files(root, "other")
    b, _, _ = _bindings(tmp_path, skills_root=root)
    res = _run(
        b,
        [
            INIT,
            _req(1, "skills/list"),
            _req(2, "skills/get", {"uri": "skill://mine/SKILL.md"}),
            _req(3, "resources/read", {"uri": "skill://mine/notes.txt"}),
        ],
    )
    assert {s["uri"] for s in res[1]["result"]["skills"]} == {
        "skill://conscio/SKILL.md",
        "skill://conscio-relay/SKILL.md",
        "skill://mine/SKILL.md",
        "skill://other/SKILL.md",
    }
    assert res[2]["result"]["skill"]["frontmatter"]["description"] == "Skill mine for tests."
    assert res[3]["result"]["contents"][0]["text"] == "hello\n"


def test_user_root_collision_user_wins_and_logs(tmp_path, capsys):
    root = tmp_path / "user-skills"
    _skill_files(
        root, "conscio", frontmatter={"name": "conscio", "description": "User override of conscio."}
    )
    b, _, _ = _bindings(tmp_path, skills_root=root)
    res = _run(b, [INIT, _req(1, "skills/list")])
    by_uri = {s["uri"]: s for s in res[1]["result"]["skills"]}
    assert (
        by_uri["skill://conscio/SKILL.md"]["frontmatter"]["description"]
        == "User override of conscio."
    )
    assert "skill://conscio-relay/SKILL.md" in by_uri
    err = capsys.readouterr().err
    assert "overriding skill 'conscio'" in err
    assert str(root / "conscio") in err
    assert str(CANON / "conscio") in err


def test_user_root_invalid_frontmatter_excluded_and_logged(tmp_path, capsys):
    root = tmp_path / "user-skills"
    _skill_files(root, "good")
    _skill_files(root, "bad", frontmatter={"name": "bad"})
    _skill_files(root, "longdesc", frontmatter={"name": "longdesc", "description": "y" * 1025})
    b, _, _ = _bindings(tmp_path, skills_root=root)
    res = _run(b, [INIT, _req(1, "skills/list")])
    assert {s["uri"] for s in res[1]["result"]["skills"]} == {
        "skill://conscio/SKILL.md",
        "skill://conscio-relay/SKILL.md",
        "skill://good/SKILL.md",
    }
    err = capsys.readouterr().err
    assert "skipping bad" in err
    assert "skipping longdesc" in err


def test_user_root_escaping_symlinks_excluded(tmp_path, capsys):
    root = tmp_path / "user-skills"
    _skill_files(root, "good")
    outside_md = tmp_path / "outside.md"
    outside_md.write_text("---\nname: linkmd\ndescription: x\n---\n\n# T\n", "utf-8")
    d = root / "linkmd"
    d.mkdir()
    (d / "SKILL.md").symlink_to(outside_md)
    _skill_files(tmp_path, "ext")
    (root / "ext").symlink_to(tmp_path / "ext", target_is_directory=True)
    cat = SkillsCatalog(user_root=root)
    assert [s["uri"] for s in cat.list_skills()["skills"]] == [
        "skill://conscio-relay/SKILL.md",
        "skill://conscio/SKILL.md",
        "skill://good/SKILL.md",
    ]
    assert "escapes" in capsys.readouterr().err


def test_user_root_file_symlink_escape_dropped(tmp_path):
    root = tmp_path / "user-skills"
    outside = tmp_path / "secret.txt"
    outside.write_text("do not serve", "utf-8")
    d = _skill_files(root, "linky", extra={"refs/real.md": "# real\n"})
    (d / "escape.txt").symlink_to(outside)
    b, _, _ = _bindings(tmp_path, skills_root=root)
    res = _run(
        b,
        [
            INIT,
            _req(1, "skills/list"),
            _req(2, "resources/read", {"uri": "skill://linky/escape.txt"}),
        ],
    )
    by_uri = {s["uri"]: s for s in res[1]["result"]["skills"]}
    assert {r["uri"] for r in by_uri["skill://linky/SKILL.md"]["resources"]} == {
        "skill://linky/SKILL.md",
        "skill://linky/refs/real.md",
    }
    assert res[2]["error"]["code"] == j.INVALID_PARAMS


def test_user_root_traversal_rejected(tmp_path):
    root = tmp_path / "user-skills"
    _skill_files(root, "mine")
    b, _, _ = _bindings(tmp_path, skills_root=root)
    res = _run(
        b,
        [
            INIT,
            _req(1, "resources/read", {"uri": "skill://mine/../conscio/SKILL.md"}),
            _req(2, "resources/directory/read", {"uri": "skill://mine/.."}),
            _req(3, "resources/read", {"uri": "skill://mine/%2e%2e/conscio/SKILL.md"}),
        ],
    )
    for r in res[1:]:
        assert r["error"]["code"] == j.INVALID_PARAMS, r


def test_user_root_oversize_skill_excluded(tmp_path, capsys):
    root = tmp_path / "user-skills"
    _skill_files(root, "good")
    d = _skill_files(root, "huge")
    big = d / "big.bin"
    with open(big, "wb") as f:
        f.truncate(17 * 1024 * 1024)  # sparse: 17 MiB on stat, ~no disk
    b, _, _ = _bindings(tmp_path, skills_root=root)
    res = _run(b, [INIT, _req(1, "skills/list")])
    assert {s["uri"] for s in res[1]["result"]["skills"]} == {
        "skill://conscio/SKILL.md",
        "skill://conscio-relay/SKILL.md",
        "skill://good/SKILL.md",
    }
    assert "skipping huge" in capsys.readouterr().err


def test_user_root_too_many_files_excluded(tmp_path, capsys):
    root = tmp_path / "user-skills"
    _skill_files(root, "good")
    d = _skill_files(root, "many")
    for i in range(512):
        (d / f"f{i:03}.txt").write_text("", "utf-8")
    cat = SkillsCatalog(user_root=root)
    assert {s["uri"] for s in cat.list_skills()["skills"]} == {
        "skill://conscio/SKILL.md",
        "skill://conscio-relay/SKILL.md",
        "skill://good/SKILL.md",
    }
    assert "skipping many" in capsys.readouterr().err


def test_user_root_dir_without_skill_md_skipped_and_logged(tmp_path, capsys):
    root = tmp_path / "user-skills"
    _skill_files(root, "good")
    (root / "empty").mkdir(parents=True)
    b, _, _ = _bindings(tmp_path, skills_root=root)
    res = _run(b, [INIT, _req(1, "skills/list")])
    assert {s["uri"] for s in res[1]["result"]["skills"]} == {
        "skill://conscio/SKILL.md",
        "skill://conscio-relay/SKILL.md",
        "skill://good/SKILL.md",
    }
    err = capsys.readouterr().err
    assert "skipping empty" in err
    assert "no SKILL.md" in err
    assert str(root / "empty") in err


def test_user_root_unreadable_skipped_bundled_survive(tmp_path, capsys):
    if os.geteuid() == 0:
        pytest.skip("chmod 000 does not block uid 0")
    root = tmp_path / "user-skills"
    _skill_files(root, "mine")
    os.chmod(root, 0)
    try:
        b, _, _ = _bindings(tmp_path, skills_root=root)
        res = _run(b, [INIT, _req(1, "skills/list")])
    finally:
        os.chmod(root, 0o755)
    assert {s["uri"] for s in res[1]["result"]["skills"]} == {
        "skill://conscio/SKILL.md",
        "skill://conscio-relay/SKILL.md",
    }
    err = capsys.readouterr().err
    assert "skipping skills root" in err
    assert str(root) in err


def test_user_root_unreadable_subdir_quarantined(tmp_path, capsys):
    # A skill with an inaccessible subdirectory is an access failure, so
    # it is quarantined (not just excluded): the repair restores the
    # permissions on the whole tree.
    if os.geteuid() == 0:
        pytest.skip("chmod 000 does not block uid 0")
    root = tmp_path / "user-skills"
    _skill_files(root, "good")
    d = _skill_files(root, "locked", extra={"sub/x.txt": "x\n"})
    os.chmod(d / "sub", 0)
    try:
        b, _, _ = _bindings(tmp_path, skills_root=root)
        res = _run(b, [INIT, _req(1, "skills/list")])
    finally:
        _restore_locked(root, d)
    assert {s["uri"] for s in res[1]["result"]["skills"]} == {
        "skill://conscio/SKILL.md",
        "skill://conscio-relay/SKILL.md",
        "skill://good/SKILL.md",
    }
    err = capsys.readouterr().err
    assert "quarantined 'locked'" in err
    assert not (root / "locked").exists()
    assert len(list((root / ".quarantine").iterdir())) == 1


def test_user_root_unreadable_skill_dir_quarantined_bundled_survive(tmp_path, capsys):
    # B1 successor: a direct child directory of the root that is
    # inaccessible (chmod 000) is quarantined, not left in place and not
    # deleted: it moves to <root>/.quarantine/<name>-<timestamp>/, the
    # other skills still list, and the skill repairs lazily on its next
    # invocation. pathlib is_file() re-raises PermissionError (only
    # ENOENT/ENOTDIR are swallowed), so the EACCES from stat() inside
    # the locked directory used to propagate out of _load_skill as -32603.
    if os.geteuid() == 0:
        pytest.skip("chmod 000 does not block uid 0")
    root = tmp_path / "user-skills"
    _skill_files(root, "good")
    d = _skill_files(root, "locked")
    os.chmod(d, 0)
    try:
        b, _, _ = _bindings(tmp_path, skills_root=root)
        res = _run(b, [INIT, _req(1, "skills/list")])
    finally:
        _restore_locked(root, d)
    assert {s["uri"] for s in res[1]["result"]["skills"]} == {
        "skill://conscio/SKILL.md",
        "skill://conscio-relay/SKILL.md",
        "skill://good/SKILL.md",
    }
    err = capsys.readouterr().err
    assert "quarantined 'locked'" in err
    assert not (root / "locked").exists()
    qdir = root / ".quarantine"
    assert qdir.is_dir()
    moved = list(qdir.iterdir())
    assert len(moved) == 1 and moved[0].name.startswith("locked-")
    # A fresh load on the same root never treats .quarantine as a skill.
    b2, _, _ = _bindings(tmp_path, skills_root=root)
    res2 = _run(b2, [INIT, _req(1, "skills/list")])
    assert {s["uri"] for s in res2[1]["result"]["skills"]} == {
        "skill://conscio/SKILL.md",
        "skill://conscio-relay/SKILL.md",
        "skill://good/SKILL.md",
    }


def test_quarantined_skill_repairs_on_get(tmp_path, capsys):
    # Quarantine + lazy auto-repair: a locked skill dir is moved to
    # .quarantine at load, and the next skills/get repairs it (move back,
    # restore permissions, re-validate) and serves it; it then lists, and
    # resources/read serves it too.
    if os.geteuid() == 0:
        pytest.skip("chmod 000 does not block uid 0")
    root = tmp_path / "user-skills"
    _skill_files(root, "good")
    d = _skill_files(root, "locked")
    os.chmod(d, 0)
    try:
        b, _, _ = _bindings(tmp_path, skills_root=root)
        res = _run(b, [INIT,
                       _req(1, "skills/list"),
                       _req(2, "skills/get",
                            {"uri": "skill://locked/SKILL.md"}),
                       _req(3, "skills/list"),
                       _req(4, "resources/read",
                            {"uri": "skill://locked/SKILL.md"})])
    finally:
        _restore_locked(root, d)
    assert "skill://locked/SKILL.md" not in {
        s["uri"] for s in res[1]["result"]["skills"]}
    assert res[2]["result"]["resultType"] == "complete"
    assert res[2]["result"]["skill"]["uri"] == "skill://locked/SKILL.md"
    assert "skill://locked/SKILL.md" in {
        s["uri"] for s in res[3]["result"]["skills"]}
    assert res[4]["result"]["contents"][0]["uri"] == "skill://locked/SKILL.md"
    assert (root / "locked" / "SKILL.md").is_file()
    assert not list((root / ".quarantine").iterdir())
    err = capsys.readouterr().err
    assert "quarantined 'locked'" in err
    assert "repaired 'locked'" in err


def test_quarantined_skill_repairs_on_resources_read(tmp_path, capsys):
    # The lazy repair is triggered by resources/read too, not just
    # skills/get: a fresh quarantine repairs on the first read_file.
    if os.geteuid() == 0:
        pytest.skip("chmod 000 does not block uid 0")

    def build(r):
        _skill_files(r, "locked")

    cat = _catalog(tmp_path, build)
    root = tmp_path / "skills"
    d = root / "locked"
    os.chmod(d, 0)
    try:
        assert cat.list_skills()["skills"] == []
        assert not (root / "locked").exists()
        data, mime = cat.read_file("skill://locked/SKILL.md")
        assert mime == "text/markdown"
        assert b"locked" in data
        assert (root / "locked" / "SKILL.md").is_file()
        assert [s["uri"] for s in cat.list_skills()["skills"]] == [
            "skill://locked/SKILL.md"]
    finally:
        _restore_locked(root, d)
    assert "repaired 'locked'" in capsys.readouterr().err


def test_quarantined_skill_collision_loads_existing_skill(tmp_path, capsys):
    # A repair never overwrites a skill that now exists in the root.
    # Instead the existing skill is loaded into the snapshot, so the
    # call proceeds with the current skill (no longer stranded until
    # the next load): the quarantined copy stays quarantined, never
    # deleted, and the new skill wins.
    if os.geteuid() == 0:
        pytest.skip("chmod 000 does not block uid 0")

    def build(r):
        _skill_files(r, "locked")

    cat = _catalog(tmp_path, build)
    root = tmp_path / "skills"
    d = root / "locked"
    os.chmod(d, 0)
    try:
        assert cat.list_skills()["skills"] == []
        qdir = root / ".quarantine"
        assert len(list(qdir.iterdir())) == 1
        # A new, valid skill with the same name appears in the root.
        _skill_files(root, "locked",
                     frontmatter={"name": "locked", "description": "replacement"})
        got = cat.get_skill("skill://locked/SKILL.md")
        assert got["resultType"] == "complete"
        assert got["skill"]["uri"] == "skill://locked/SKILL.md"
        assert got["skill"]["frontmatter"]["description"] == "replacement"
        # The new skill is intact and now listed; the old one is still
        # quarantined (never deleted).
        assert (root / "locked" / "SKILL.md").is_file()
        assert len(list(qdir.iterdir())) == 1
        assert [s["uri"] for s in cat.list_skills()["skills"]] == [
            "skill://locked/SKILL.md"]
    finally:
        _restore_locked(root, d)


def test_quarantined_skill_collision_escaping_symlink_not_served(tmp_path, capsys):
    # R1 pinning: a skill directory that is a symlink escaping the root
    # is never served, even when a skill of the same name is quarantined
    # (the collision load must not bypass the escape check). Covers
    # skills/get, resources/read and resources/directory/read.
    if os.geteuid() == 0:
        pytest.skip("chmod 000 does not block uid 0")
    root = tmp_path / "user-skills"
    outside = tmp_path / "outside"
    _skill_files(tmp_path, "outside",
                 frontmatter={"name": "locked", "description": "OUTSIDE"},
                 extra={"secret.txt": "OUTSIDE-SECRET\n"})
    _skill_files(root, "good")
    d = _skill_files(root, "locked")
    os.chmod(d, 0)
    try:
        b, _, _ = _bindings(tmp_path, skills_root=root)
        res = _run(b, [INIT, _req(1, "skills/list")])
        assert "skill://locked/SKILL.md" not in {
            s["uri"] for s in res[1]["result"]["skills"]}
        # Symlink in the root pointing OUTSIDE, same name as quarantined.
        os.symlink(outside, root / "locked")
        res = _run(b, [INIT,
                       _req(2, "skills/get",
                            {"uri": "skill://locked/SKILL.md"}),
                       _req(3, "resources/read",
                            {"uri": "skill://locked/secret.txt"}),
                       _req(4, "resources/directory/read",
                            {"uri": "skill://locked"})])
        for r in res[1:]:
            assert r["error"]["code"] == j.INVALID_PARAMS, r
            assert "OUTSIDE" not in json.dumps(r)
            assert "OUTSIDE-SECRET" not in json.dumps(r)
        # Nothing leaked, the quarantined copy is still there, and the
        # skill is still not listed.
        assert len(list((root / ".quarantine").iterdir())) == 1
        res = _run(b, [INIT, _req(5, "skills/list")])
        assert "skill://locked/SKILL.md" not in {
            s["uri"] for s in res[1]["result"]["skills"]}
    finally:
        _restore_locked(root, d)
        link = root / "locked"
        if link.is_symlink():
            link.unlink()


def test_quarantined_skill_repair_failure_stays_quarantined(tmp_path, capsys):
    # When the move back fails (here the root is read-only), the skill
    # stays quarantined and the call answers -32602.
    if os.geteuid() == 0:
        pytest.skip("chmod 000 does not block uid 0")

    def build(r):
        _skill_files(r, "locked")

    cat = _catalog(tmp_path, build)
    root = tmp_path / "skills"
    d = root / "locked"
    os.chmod(d, 0)
    try:
        assert cat.list_skills()["skills"] == []
        qdir = root / ".quarantine"
        assert len(list(qdir.iterdir())) == 1
        os.chmod(root, 0o555)
        try:
            with pytest.raises(j.InvalidParams):
                cat.get_skill("skill://locked/SKILL.md")
        finally:
            os.chmod(root, 0o755)
        assert len(list(qdir.iterdir())) == 1
        assert not (root / "locked").exists()
    finally:
        _restore_locked(root, d)
        try:
            os.chmod(root, 0o755)
        except OSError:
            pass
    assert "cannot repair 'locked'" in capsys.readouterr().err


def test_main_accepts_skills_root_flag(tmp_path):
    from conscio.mcp.server import _arg_parser, _skills_root_or_error

    args = _arg_parser().parse_args(["--enable-skills", "--skills-root", str(tmp_path)])
    assert args.enable_skills is True
    assert args.skills_root == str(tmp_path)
    assert _arg_parser().parse_args([]).skills_root is None
    assert _skills_root_or_error(None) is None
    assert _skills_root_or_error(str(tmp_path)) == tmp_path
    with pytest.raises(ValueError, match="does not exist"):
        _skills_root_or_error(str(tmp_path / "no-such-dir"))
    with pytest.raises(ValueError, match="must not be empty"):
        _skills_root_or_error("")
    with pytest.raises(ValueError, match="must not be empty"):
        _skills_root_or_error("   ")
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("x", "utf-8")
    with pytest.raises(ValueError, match="is not a directory"):
        _skills_root_or_error(str(not_a_dir))


def test_skills_root_gate_off_changes_nothing(tmp_path):
    # Orthogonality: without --enable-skills the user root is inert —
    # the lazy catalog is never loaded, even for a nonexistent root.
    root = tmp_path / "user-skills"
    _skill_files(root, "mine")
    b, _, _ = _bindings(tmp_path, skills=False, skills_root=root)
    assert b.skills_catalog._loaded is False
    res = _run(b, [INIT, _req(1, "skills/list")])
    assert res[1]["error"]["code"] == j.METHOD_NOT_FOUND
    assert b.skills_catalog._loaded is False
    b2, _, _ = _bindings(tmp_path, skills=False, skills_root=tmp_path / "no-such-dir")
    assert b2.skills_catalog._loaded is False


def test_catalog_user_root_param_unions(tmp_path):
    # Direct catalog use: root keeps its single-root meaning, user_root
    # unions with it; a user skill wins a name collision.
    root = tmp_path / "primary"
    _skill_files(root, "shared", frontmatter={"name": "shared", "description": "Primary."})
    user = tmp_path / "user"
    _skill_files(user, "shared", frontmatter={"name": "shared", "description": "User wins."})
    _skill_files(user, "extra")
    cat = SkillsCatalog(root=root, user_root=user)
    skills = {s["uri"]: s for s in cat.list_skills()["skills"]}
    assert set(skills) == {"skill://shared/SKILL.md", "skill://extra/SKILL.md"}
    assert skills["skill://shared/SKILL.md"]["frontmatter"]["description"] == "User wins."


# ------------------------------------------- fail-fast through real main()
def _run_mcp_subprocess(tmp_path, *argv, pre_capabilities=None):
    """Run the real `conscio-mcp` console script in a subprocess with a
    fresh storage dir and closed stdin; return (returncode, stderr)."""
    storage = tmp_path / "storage"
    storage.mkdir(parents=True, exist_ok=True)
    if pre_capabilities is not None:
        (storage / "mcp_capabilities").write_text(pre_capabilities, "utf-8")
    script = Path(sys.executable).parent / "conscio-mcp"
    proc = subprocess.run(
        [str(script), "--storage", str(storage), "--model", "glm-5.1", "--adapter", "mock", *argv],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode, proc.stderr


def test_main_fail_fast_by_flag_persists_nothing(tmp_path):
    rc, err = _run_mcp_subprocess(
        tmp_path, "--enable-skills", "--skills-root", str(tmp_path / "no-such-dir")
    )
    assert rc == 2
    assert "refusing to start" in err
    assert "does not exist" in err
    assert not (tmp_path / "storage" / "mcp_capabilities").exists()


def test_main_fail_fast_by_persisted_capability(tmp_path):
    rc, err = _run_mcp_subprocess(
        tmp_path, "--skills-root", str(tmp_path / "no-such-dir"), pre_capabilities="skills\n"
    )
    assert rc == 2
    assert "refusing to start" in err
    assert "does not exist" in err
    # The refused boot persisted nothing new: the pre-existing file is
    # byte-identical to what the test wrote.
    assert (tmp_path / "storage" / "mcp_capabilities").read_text() == "skills\n"
