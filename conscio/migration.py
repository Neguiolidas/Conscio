"""Migration — export/import Conscio memory em tar.gz.

Formato tar.gz:
  metadata.json    — { version, exported_at, components, drawer_count, entity_count, schema_version }
  content_store.db  — SQLite backup (sqlite3 backup API)
  kg.db             — optional, if KG provided
  hallways.db       — optional, if Hallways provided

Round-trip: export→import produces DBs equivalent (ContentStore dedup via content_hash).
"""
from __future__ import annotations

import io
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from .content_store import ContentStore
from .hallways import Hallways
from .kg import KnowledgeGraph


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def export_archive(
    path: str | Path,
    content_store: ContentStore | None = None,
    kg: KnowledgeGraph | None = None,
    hallways: Hallways | None = None,
) -> dict:
    """Serialize Conscio memory em tar.gz. Components opcional (None = skip).

    Returns the metadata dict.
    """
    metadata = {
        "version": "3.2.0",
        "exported_at": _utcnow(),
        "schema_version": 1,
        "components": {},
    }

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmpdir = path.parent

    with tarfile.open(path, "w:gz") as tar:
        if content_store is not None:
            p = tmpdir / "_conscio_export_cs.db"
            content_store.dump(p)
            tar.add(p, arcname="content_store.db")
            p.unlink()
            stats = getattr(content_store, "stats", dict)() or {}
            metadata["components"]["content_store"] = {"stats": stats}
        if kg is not None:
            p = tmpdir / "_conscio_export_kg.db"
            kg.dump(p)
            tar.add(p, arcname="kg.db")
            p.unlink()
            stats = kg.stats()
            metadata["components"]["kg"] = {"stats": stats}
        if hallways is not None:
            p = tmpdir / "_conscio_export_hw.db"
            hallways.dump(p)
            tar.add(p, arcname="hallways.db")
            p.unlink()
            stats = hallways.stats()
            metadata["components"]["hallways"] = {"stats": stats}

        # metadata.json
        meta_bytes = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
        info = tarfile.TarInfo("metadata.json")
        info.size = len(meta_bytes)
        tar.addfile(info, io.BytesIO(meta_bytes))

    return metadata


def import_archive(
    path: str | Path, target_dir: str | Path
) -> tuple[ContentStore | None, KnowledgeGraph | None, Hallways | None]:
    """Restaurar tar.gz em DBs abertos. Retorna instancias (cs, kg, hw) — None se ausente."""
    path = Path(path)
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)

    cs_path = None
    kg_path = None
    hw_path = None
    with tarfile.open(path, "r:gz") as tar:
        meta_member = tar.extractfile("metadata.json")
        if meta_member:
            json.loads(meta_member.read().decode("utf-8"))
        for member in tar.getmembers():
            if member.name == "content_store.db":
                # Use unique filename in target
                cs_path = target / "content_store.db"
                with open(cs_path, "wb") as out:
                    out.write(tar.extractfile(member).read())  # type: ignore[union-attr]
            elif member.name == "kg.db":
                kg_path = target / "kg.db"
                with open(kg_path, "wb") as out:
                    out.write(tar.extractfile(member).read())  # type: ignore[union-attr]
            elif member.name == "hallways.db":
                hw_path = target / "hallways.db"
                with open(hw_path, "wb") as out:
                    out.write(tar.extractfile(member).read())  # type: ignore[union-attr]

    cs = ContentStore(db_path=cs_path) if cs_path else None
    kg = KnowledgeGraph(db_path=kg_path) if kg_path else None
    hw = Hallways(db_path=hw_path) if hw_path else None
    return cs, kg, hw
