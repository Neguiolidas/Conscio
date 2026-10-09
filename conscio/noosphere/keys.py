"""Ed25519 signing for the noosphere (v4.9, Jade catalog item 9).

One key per instance, chmod 0600, stored next to the instance identity.
Publish signs the artifact body; import verifies. Trust is TOFU (trust on
first use): the first time an origin is seen, its key is remembered; a
later publish under the same origin with a DIFFERENT key is rejected —
that is a compromised or spoofed identity. Artifacts that predate
signing import with a warning, never a hard failure (legacy compat).

Zero new runtime dependencies: Python 3.10+ ships Ed25519 verification
is NOT in stdlib, so the implementation uses ``cryptography`` — which the
project already transitively requires via the optional keyring extra —
and hard-imports it only when signing/verifying, so a headless install
without the extra never pays for it unless the noosphere is used.
"""
from __future__ import annotations

import hashlib
import logging
import os
import stat
import time
from pathlib import Path

log = logging.getLogger("conscio.noosphere.keys")

# ── key store ─────────────────────────────────────────────────────────


def key_path(storage: str | os.PathLike[str]) -> Path:
    """The instance's Ed25519 key file, 0600, one per instance."""
    p = Path(storage) / "noosphere_ed25519.key"
    return p


def load_or_create_key(storage: str | os.PathLike[str]) -> bytes:
    """Load the raw private key (32 bytes), creating one if absent.

    The file is created with 0600 and never world-readable: a private key
    that other processes can read is a compromised key. Returns raw bytes
    so the caller does not need to know the serialization.
    """
    path = key_path(storage)
    if path.exists():
        data = path.read_bytes()
        if len(data) != 32:
            raise ValueError(
                f"noosphere key {path} is {len(data)} bytes, expected 32")
        _enforce_0600(path)
        return data

    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    raw = Ed25519PrivateKey.generate().private_bytes_raw()
    # Write with 0600 from the start — no window where it's readable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, raw)
    finally:
        os.close(fd)
    log.info("new noosphere ed25519 key at %s (0600)", path)
    return raw


def public_key_bytes(raw_private: bytes) -> bytes:
    """Derive the 32-byte public key from the raw private key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    return Ed25519PrivateKey.from_private_bytes(raw_private).public_key().public_bytes_raw()


def _enforce_0600(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        path.chmod(0o600)
        log.warning("noosphere key %s had mode %o, tightened to 0600",
                    path, mode)


# ── sign / verify ────────────────────────────────────────────────────


def sign_body(raw_private: bytes, body: bytes) -> bytes:
    """Sign the canonical artifact body (64-byte Ed25519 signature)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    return Ed25519PrivateKey.from_private_bytes(raw_private).sign(body)


def verify_signature(public_b: bytes, signature: bytes, body: bytes) -> bool:
    """Verify a signature; False on any mismatch or malformed key."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
        Ed25519PublicKey.from_public_bytes(public_b).verify(signature, body)
        return True
    except Exception:  # InvalidSignature, malformed key, bad length
        return False


def key_id(public_b: bytes) -> str:
    """Short, stable identifier for a public key (for logs and TOFU rows)."""
    return hashlib.sha256(public_b).hexdigest()[:16]


# ── TOFU trust table ────────────────────────────────────────────────

_TRUST_SCHEMA = """
CREATE TABLE IF NOT EXISTS key_trust (
    origin_instance_id TEXT PRIMARY KEY,
    key_id            TEXT NOT NULL,
    public_key        BLOB NOT NULL,
    first_seen_ts     REAL NOT NULL
);
"""


def _trust_conn(noosphere_path: Path):
    import sqlite3

    from ..sqlite_tuning import tune
    conn = sqlite3.connect(str(noosphere_path))
    tune(conn, foreign_keys=True)
    conn.row_factory = sqlite3.Row
    conn.executescript(_TRUST_SCHEMA)
    conn.commit()
    return conn


def tofu_check_or_remember(noosphere: str | os.PathLike[str],
                           origin: str, public_b: bytes) -> bool:
    """TOFU: remember the first key per origin; reject a changed one.

    Returns True when the origin's key is trusted (first sight counts as
    trust). False only when the origin was seen with a DIFFERENT key —
    that is identity theft, and the import is refused.
    """
    conn = _trust_conn(Path(noosphere))
    try:
        row = conn.execute(
            "SELECT public_key, key_id FROM key_trust WHERE origin_instance_id=?",
            (origin,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO key_trust (origin_instance_id, key_id, public_key,"
                " first_seen_ts) VALUES (?, ?, ?, ?)",
                (origin, key_id(public_b), public_b, time.time()))
            conn.commit()
            log.info("TOFU: first key for %s remembered (%s)",
                     origin[:8], key_id(public_b))
            return True
        if bytes(row["public_key"]) == public_b:
            return True
        log.warning(
            "TOFU REJECT: origin %s published with key %s, remembered %s",
            origin[:8], key_id(public_b), row["key_id"])
        return False
    finally:
        conn.close()
