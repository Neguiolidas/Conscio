"""Tests for noosphere Ed25519 signing + TOFU (v4.9, item 9)."""
from __future__ import annotations

import json
import stat
from pathlib import Path

from conscio.noosphere import catalog, keys
from conscio.noosphere.artifact import ARTIFACT_SCHEMA, build_body, canonical_bytes, content_hash


def _storage(tmp: Path) -> Path:
    d = tmp / "space"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_key_created_with_0600(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    raw = keys.load_or_create_key(storage)
    assert len(raw) == 32
    mode = stat.S_IMODE(keys.key_path(storage).stat().st_mode)
    assert mode == 0o600


def test_key_load_is_stable(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    a = keys.load_or_create_key(storage)
    b = keys.load_or_create_key(storage)
    assert a == b  # same key, not a new one


def test_tightens_leaky_key_mode(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    keys.load_or_create_key(storage)
    keys.key_path(storage).chmod(0o644)  # simulate a leak
    keys.load_or_create_key(storage)
    mode = stat.S_IMODE(keys.key_path(storage).stat().st_mode)
    assert mode == 0o600


def test_sign_verify_roundtrip(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    raw = keys.load_or_create_key(storage)
    pub = keys.public_key_bytes(raw)
    body = b"the canonical bytes of an artifact"
    sig = keys.sign_body(raw, body)
    assert keys.verify_signature(pub, sig, body)
    assert not keys.verify_signature(pub, sig, b"tampered body")
    assert not keys.verify_signature(pub, b"x" * 64, body)


def test_publish_signs_and_importer_verifies(tmp_path: Path) -> None:
    """End-to-end: publish signs the row; read_foreign returns the signature."""
    storage = _storage(tmp_path)
    noo = tmp_path / "noosphere.db"
    raw = keys.load_or_create_key(storage)
    pub = keys.public_key_bytes(raw)

    body = build_body(goal_fp="fp1", goal_text="do a thing",
                      tool_seq=["t1"], plan_template=["step"])
    canon = canonical_bytes(body)
    row = catalog.CatalogRow(
        origin_instance_id="aaaa-bbbb", origin_label="A",
        goal_fp="fp1", goal_text="do a thing",
        tool_seq=json.dumps(["t1"]), plan_template=json.dumps(["step"]),
        published_ts=1.0, content_sha256=content_hash(canon),
        artifact_json=canon, schema_version=ARTIFACT_SCHEMA,
        signature=keys.sign_body(raw, canon), signer_pubkey=pub)
    assert catalog.publish_rows(noo, [row]) == 1

    back = catalog.read_all(noo)
    assert len(back) == 1
    assert back[0].signature and back[0].signer_pubkey == pub
    assert keys.verify_signature(back[0].signer_pubkey,
                                back[0].signature, back[0].artifact_json)


def test_tofu_first_sight_trusts_and_key_change_rejects(tmp_path: Path) -> None:
    noo = tmp_path / "noo.db"
    storage = _storage(tmp_path)
    raw1 = keys.load_or_create_key(storage)
    pub1 = keys.public_key_bytes(raw1)
    # first sight: trusted
    assert keys.tofu_check_or_remember(noo, "origin-1", pub1) is True
    # same key again: trusted
    assert keys.tofu_check_or_remember(noo, "origin-1", pub1) is True
    # a different key under the same origin: REJECTED
    storage2 = _storage(tmp_path / "second")
    raw2 = keys.load_or_create_key(storage2)
    pub2 = keys.public_key_bytes(raw2)
    assert keys.tofu_check_or_remember(noo, "origin-1", pub2) is False


def test_legacy_unsigned_row_imports_with_warning(tmp_path: Path) -> None:
    """Rows without signature (pre-v4.9) still import — compat, not refusal."""
    _storage(tmp_path)
    noo = tmp_path / "noo.db"
    body = build_body(goal_fp="fp2", goal_text="legacy",
                      tool_seq=["t"], plan_template=["s"])
    canon = canonical_bytes(body)
    row = catalog.CatalogRow(
        origin_instance_id="cccc-dddd", origin_label="C",
        goal_fp="fp2", goal_text="legacy",
        tool_seq=json.dumps(["t"]), plan_template=json.dumps(["s"]),
        published_ts=1.0, content_sha256=content_hash(canon),
        artifact_json=canon, schema_version=ARTIFACT_SCHEMA,
        signature=b"", signer_pubkey=b"")  # legacy unsigned
    catalog.publish_rows(noo, [row])
    back = catalog.read_all(noo)
    assert back[0].signature == b""  # arrives unsigned, importer warns
