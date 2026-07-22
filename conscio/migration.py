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
from typing import Optional

from .content_store import ContentStore
from .kg import KnowledgeGraph
from .hallways import Hallways


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def export_archive(
    path: str | Path,
    content_store: Optional[ContentStore] = None,
    kg: Optional[KnowledgeGraph] = None,
    hallways: Optional[Hallways] = None,
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
            stats = getattr(content_store, "stats", lambda: {})() or {}
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
) -> tuple[Optional[ContentStore], Optional[KnowledgeGraph], Optional[Hallways]]:
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
                    out.write(tar.extractfile(member).read())
            elif member.name == "kg.db":
                kg_path = target / "kg.db"
                with open(kg_path, "wb") as out:
                    out.write(tar.extractfile(member).read())
            elif member.name == "hallways.db":
                hw_path = target / "hallways.db"
                with open(hw_path, "wb") as out:
                    out.write(tar.extractfile(member).read())

    cs = ContentStore(db_path=cs_path) if cs_path else None
    kg = KnowledgeGraph(db_path=kg_path) if kg_path else None
    hw = Hallways(db_path=hw_path) if hw_path else None
    return cs, kg, hw


def import_format_mempalace(
    chroma_db_dir: str | Path,
    wing_manager: Optional["WingManager"] = None,  # noqa: F821
) -> int:
    """Import drawers de ChromaDB MemPalace (chroma.sqlite3).

    Reads chroma:document + wing + room from embedding_metadata.
    The `id` in embedding_metadata is FK to embeddings.id.
    Indexes each document as a drawer via WingManager.

    Returns count of drawers imported.
    """
    import sqlite3 as _sql

    d = Path(chroma_db_dir)
    if not d.is_dir():
        return 0
    chroma_path = d / "chroma.sqlite3"
    if not chroma_path.exists():
        return 0
    if wing_manager is None:
        return 0

    conn = _sql.connect(str(chroma_path), timeout=10)
    conn.row_factory = _sql.Row

    count = 0
    # Get distinct embedding ids that have chroma:document
    rows = conn.execute(
        """SELECT DISTINCT em.id
           FROM embedding_metadata em
           WHERE em.key = 'chroma:document'"""
    ).fetchall()

    for row in rows:
        eid = row["id"]
        # Get document content
        doc_row = conn.execute(
            "SELECT string_value FROM embedding_metadata WHERE id = ? AND key = 'chroma:document'",
            (eid,),
        ).fetchone()
        if doc_row is None:
            continue
        content = doc_row["string_value"]
        if not content:
            continue

        # Get wing + room (optional)
        wing_row = conn.execute(
            "SELECT string_value FROM embedding_metadata WHERE id = ? AND key = 'wing'",
            (eid,),
        ).fetchone()
        room_row = conn.execute(
            "SELECT string_value FROM embedding_metadata WHERE id = ? AND key = 'room'",
            (eid,),
        ).fetchone()
        wing = wing_row["string_value"] if wing_row else "mempalace"
        room = room_row["string_value"] if room_row else "imported"

        try:
            wing_manager.index(
                label=f"mempalace_{eid}",
                content=content,
                category="external",
                content_type="prose",
                wing=wing,
                room=room,
                session_id="mempalace_migration",
            )
            count += 1
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"MemPalace import failed for id {eid}: {e}")

    conn.close()
    return count
