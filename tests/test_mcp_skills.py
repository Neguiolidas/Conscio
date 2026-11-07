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
import re
import sys
from pathlib import Path

import pytest
import yaml

from conscio.agency import MockAdapter
from conscio.engine import ConsciousnessEngine
from conscio.mcp import jsonrpc as j
from conscio.mcp.protocol import Dispatcher
from conscio.mcp.seen import SeenStore
from conscio.mcp.server import Bindings, serve
from conscio.mcp.skills import SkillsCatalog
from test_mcp_protocol import FakeBindings

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
    text = path.read_text("utf-8").lstrip("\ufeff")
    lines = text.splitlines()
    assert lines[0].strip() == "---"
    end = lines.index("---", 1)
    return yaml.safe_load("\n".join(lines[1:end]))


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
    entry = res[3]["result"]["skills"][0]
    assert len(entry["resources"]) == 3
    assert {r["uri"] for r in entry["resources"]} == {
        "skill://multi/SKILL.md", "skill://multi/refs/guide.md",
        "skill://multi/scripts/run.py"}


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
    tools_off = res[5]["result"]


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
    # non-dict params must not raise AttributeError (coerced to {})
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
    assert c["mimeType"] == "application/octet-stream"
    assert base64.b64decode(c["blob"]) == png
    assert "text" not in c


def test_catalog_empty_root_lists_nothing(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    cat = SkillsCatalog(root=root)
    assert cat.list_skills() == {"resultType": "complete", "skills": []}
    with pytest.raises(j.InvalidParams):
        cat.get_skill("skill://x/SKILL.md")


def test_digest_is_sensitive_to_one_byte(tmp_path):
    raw = (CANON / "conscio" / "SKILL.md").read_bytes()
    good = "sha256:" + hashlib.sha256(raw).hexdigest()
    bad = bytearray(raw)
    bad[10] ^= 0xFF
    assert "sha256:" + hashlib.sha256(bytes(bad)).hexdigest() != good


def test_catalog_snapshot_does_not_reread_disk(tmp_path):
    root = tmp_path / "skills"
    d = _skill_files(root, "snap")
    cat = SkillsCatalog(root=root)
    assert len(cat.list_skills()["skills"]) == 1
    (d / "SKILL.md").write_text("---\nname: snap\ndescription: changed\n"
                                "---\n\n# changed\n", "utf-8")
    data, _ = cat.read_file("skill://snap/SKILL.md")
    assert b"changed" not in data


def test_wire_temp_catalog_mixed_valid_invalid(tmp_path):
    root = tmp_path / "skills"
    _skill_files(root, "good")
    _skill_files(root, "bad", frontmatter={"name": "bad"})
    b, _, _ = _bindings(tmp_path, skills_root=root)
    res = _run(b, [INIT, _req(1, "skills/list")])
    assert [s["uri"] for s in res[1]["result"]["skills"]] == \
        ["skill://good/SKILL.md"]


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
