# conscio/noosphere/identity.py
"""Instance identity — the root of provenance. instance.json carries
{schema, instance_id, label, created_ts}. Lazy-created only when ABSENT;
written 0600 with atomic os.replace. A corrupt file is a HARD FAIL: we never
regenerate a UUID over corruption (that would erase audit history)."""
from __future__ import annotations

import json
import os
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .paths import instance_path

SCHEMA = 1
MAX_LABEL = 120


class NoosphereIdentityError(RuntimeError):
    pass


@dataclass(frozen=True)
class Identity:
    instance_id: str
    label: str
    created_ts: float
    schema: int = SCHEMA


def _validate_label(label: str) -> str:
    label = label.strip()
    if not label:
        raise ValueError("label must not be empty")
    if len(label) > MAX_LABEL:
        raise ValueError(f"label too long (max {MAX_LABEL})")
    if any((not c.isprintable()) or c in "\n\r\t" for c in label):
        raise ValueError("label must not contain control characters")
    return label


def _default_label(instance_id: str) -> str:
    return _validate_label(f"{socket.gethostname()}-{instance_id[:8]}")


def _write_atomic(path: Path, ident: Identity) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(
        {"schema": ident.schema, "instance_id": ident.instance_id,
         "label": ident.label, "created_ts": ident.created_ts},
        indent=2, sort_keys=True).encode("utf-8")
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        written = 0
        while written < len(blob):          # os.write may write partially
            written += os.write(fd, blob[written:])
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(path))


def _read(path: Path) -> Identity:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Identity(
            instance_id=str(data["instance_id"]),
            label=_validate_label(str(data["label"])),
            created_ts=float(data["created_ts"]),
            schema=int(data.get("schema", SCHEMA)))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise NoosphereIdentityError(
            f"instance identity at {path} is corrupt/unreadable: {exc}. "
            f"Back it up and remove it manually to re-create.") from exc


def load_or_create(storage: str | os.PathLike[str]) -> Identity:
    path = instance_path(storage)
    if path.exists():
        return _read(path)
    iid = str(uuid.uuid4())
    ident = Identity(instance_id=iid, label=_default_label(iid),
                     created_ts=time.time())
    _write_atomic(path, ident)
    return ident


def set_label(storage: str | os.PathLike[str], label: str) -> Identity:
    current = load_or_create(storage)
    ident = Identity(instance_id=current.instance_id,
                     label=_validate_label(label),
                     created_ts=current.created_ts, schema=current.schema)
    _write_atomic(instance_path(storage), ident)
    return ident
