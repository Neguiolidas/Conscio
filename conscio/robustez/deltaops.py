"""Refresh docs via addressed operations (v4.9 item 13; hindsight port, MIT).

pydantic -> dicts + manual strict validation, stdlib only. 8 ops:
append_block, insert_block, replace_block, remove_block, add_section,
remove_section, replace_section_blocks, rename_section.

Two layers: **shape** — 1 op outside the schema refuses the whole batch
(DeltaOperationsInvalidError, with ``rejected`` to feed the model back);
**reference** — an unknown section_id/block_id only skips that one op
(with ``reason``), the rest apply. Blocks are addressed by id, never
index; new ids are minted here; the doc itself is never mutated.
"""
from __future__ import annotations

import hashlib
from typing import Any

_OPS = frozenset({
    "append_block", "insert_block", "replace_block", "remove_block",
    "add_section", "remove_section", "replace_section_blocks",
    "rename_section",
})


class DeltaOperationsInvalidError(ValueError):
    """One op outside the schema: the whole batch is refused (shape layer)."""

    def __init__(self, rejected: list[str]) -> None:
        super().__init__(
            f"unknown operation(s): {', '.join(sorted(rejected))}")
        self.rejected = rejected


def make_block_id(text: str, taken: set[str]) -> str:
    """sha1[:8] + disambiguation; the model's id is discarded."""
    base = hashlib.sha1(text.encode("utf-8", "replace"), usedforsecurity=False).hexdigest()[:8]
    block_id = base
    n = 1
    while block_id in taken:
        block_id = f"{base}-{n}"
        n += 1
    return block_id


def _section(doc: dict[str, Any], section_id: str) -> dict[str, Any] | None:
    return next((s for s in doc.get("sections", [])
                 if s.get("section_id") == section_id), None)


def apply_ops(doc: dict[str, Any], ops: list[dict[str, Any]]
              ) -> dict[str, Any]:
    """Apply the batch to a COPY of the doc; returns {document, applied, skipped}."""
    bad = [str(o.get("op")) for o in ops if o.get("op") not in _OPS]
    if bad:
        raise DeltaOperationsInvalidError(bad)
    import copy
    document = copy.deepcopy(doc)
    applied: list[str] = []
    skipped: list[dict[str, Any]] = []
    for op in ops:
        kind = op["op"]
        try:
            applied.append(_apply_one(document, kind, op))
        except (KeyError, ValueError, LookupError) as exc:
            skipped.append({"op": kind, "reason": str(exc)})
    return {"document": document, "applied": applied, "skipped": skipped}


def _apply_one(document: dict, kind: str, op: dict) -> str:
    sections = document.setdefault("sections", [])
    sid = str(op.get("section_id") or "")
    section = _section(document, sid)

    if kind == "add_section":
        name = str(op.get("name") or "").strip()
        if not name:
            raise ValueError("add_section needs a name")
        new = {"section_id": make_block_id(name, {s.get("section_id") for s in sections}),
               "name": name, "blocks": []}
        sections.append(new)
        return f"section {new['section_id']} added"

    if kind == "rename_section":
        if section is None:
            raise LookupError(f"section {sid!r} not found")
        section["name"] = str(op.get("name") or section["name"])
        return f"section {sid} renamed"

    if kind == "remove_section":
        if section is None:
            raise LookupError(f"section {sid!r} not found")
        sections.remove(section)
        return f"section {sid} removed"

    if section is None:
        raise LookupError(f"section {sid!r} not found")
    blocks = section.setdefault("blocks", [])
    block_ids = {b.get("block_id") for b in blocks}
    text = str(op.get("text") or "")
    if kind != "remove_block" and not text:
        raise ValueError(f"{kind} needs text")

    if kind == "append_block":
        block_id = make_block_id(text, block_ids)
        blocks.append({"block_id": block_id, "text": text})
        return f"block {block_id} appended"

    if kind == "insert_block":
        anchor = str(op.get("after_block_id") or "")
        idx = next((i for i, b in enumerate(blocks)
                    if b.get("block_id") == anchor), len(blocks))
        block_id = make_block_id(text, block_ids)
        blocks.insert(idx + 1, {"block_id": block_id, "text": text})
        return f"block {block_id} inserted"

    if kind == "replace_block":
        block_id = str(op.get("block_id") or "")
        target = next((b for b in blocks if b.get("block_id") == block_id), None)
        if target is None:
            raise LookupError(f"block {block_id!r} not found")
        target["text"] = text
        return f"block {block_id} replaced"

    if kind == "remove_block":
        block_id = str(op.get("block_id") or "")
        target = next((b for b in blocks if b.get("block_id") == block_id), None)
        if target is None:
            raise LookupError(f"block {block_id!r} not found")
        blocks.remove(target)
        return f"block {block_id} removed"

    # replace_section_blocks
    blocks.clear()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        block_id = make_block_id(line, {b.get("block_id") for b in blocks})
        blocks.append({"block_id": block_id, "text": line})
    return f"section {sid} blocks replaced"
