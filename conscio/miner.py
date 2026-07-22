"""Miner — file + conversation ingestion for Conscio.

Simplified version (not porting 2742L of MemPalace miner/convo_miner):
- ingest_file: parse .md/.txt/.jsonl → chunks → WingManager.index
- ingest_conversation: parse JSONL chat → each turn = drawer
- ingest_directory: walk dir, skip DEFAULT_SKIP_DIRS
- Dedup via Deduplicator (compute_hash → is_duplicate → register)
- Skip binary files (.bin, .png, .pdf, etc)

DEFAULT_SKIP_DIRS: .git, __pycache__, node_modules, .venv, venv, env
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Optional

from .dedup import Deduplicator
from .wings import WingManager

logger = logging.getLogger(__name__)

DEFAULT_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "env", ".idea", ".vscode"}

SUPPORTED_TEXT_EXT = {".md", ".txt", ".jsonl", ".json"}

BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp",
              ".pdf", ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
              ".bin", ".dat", ".so", ".o", ".a", ".lib", ".dll", ".exe",
              ".mp3", ".mp4", ".avi", ".mov", ".wav", ".ogg", ".flac",
              ".woff", ".woff2", ".ttf", ".otf", ".eot"}


class Miner:
    """File + conversation ingestor."""

    def __init__(self, wing_manager: WingManager, dedup: Optional[Deduplicator] = None):
        self.wm = wing_manager
        # Use shared dedup db if provided; else standalone next to content_store
        if dedup is None:
            # try sibling of content_store db
            cs_db = getattr(self.wm.cs, "db_path", None)
            dd_path = str(cs_db).rsplit(".", 1)[0] + "_dedup.db" if cs_db else None
            self.dd = Deduplicator(db_path=dd_path)
        else:
            self.dd = dedup

    # ── File ingest ─────────────────────────────────────────────────

    def ingest_file(self, path: Path | str, wing: str = "default", room: str = "default") -> int:
        """Ingest a single file. Returns count of drawers indexed (0 if skipped)."""
        p = Path(path)
        if not p.is_file():
            return 0
        ext = p.suffix.lower()
        if ext in BINARY_EXT:
            return 0
        if ext not in SUPPORTED_TEXT_EXT:
            return 0
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return 0
        if not content.strip():
            return 0

        # Dedup
        content_hash = self.dd.compute_hash(content)
        if self.dd.is_duplicate(content_hash):
            return 0

        # Index each non-empty para/chunk as separate drawer
        # Simple strategy: para-split at blank lines
        paras = [p.strip() for p in content.split("\n\n") if p.strip()]
        if not paras:
            paras = [content]

        count = 0
        for i, chunk in enumerate(paras):
            chunk_hash = self.dd.compute_hash(chunk)
            if self.dd.is_duplicate(chunk_hash):
                continue
            try:
                self.wm.index(
                    label=f"{p.stem}__{i}", content=chunk, category="external",
                    content_type="prose", wing=wing, room=room
                )
                self.dd.register(chunk_hash, chunk)
                count += 1
            except Exception as e:
                logger.warning(f"Index failed for {p} chunk {i}: {e}")

        return count

    # ── Conversation ingest ─────────────────────────────────────────

    def ingest_conversation(self, path: Path | str, wing: str = "default", room: str = "default") -> int:
        """Ingest JSONL conversation file. Each turn = one drawer."""
        p = Path(path)
        if not p.is_file() or p.suffix.lower() != ".jsonl":
            return 0
        count = 0
        with p.open("r", encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                role = msg.get("role") or msg.get("speaker") or ""
                content = msg.get("content") or msg.get("text")
                if not content:
                    continue
                chunk = f"[{role}] {content}" if role else content
                chunk_hash = self.dd.compute_hash(chunk)
                if self.dd.is_duplicate(chunk_hash):
                    continue
                try:
                    self.wm.index(
                        label=f"{p.stem}__turn{i}", content=chunk,
                        category="external", content_type="prose",
                        wing=wing, room=room
                    )
                    self.dd.register(chunk_hash, chunk)
                    count += 1
                except Exception as e:
                    logger.warning(f"Index failed for {p} turn {i}: {e}")
        return count

    # ── Directory ingest ─────────────────────────────────────────────

    def ingest_directory(self, path: Path | str, wing: str = "default", room: str = "default") -> int:
        """Walk directory, ingest all supported text files. Skip DEFAULT_SKIP_DIRS."""
        p = Path(path)
        if not p.is_dir():
            return 0
        count = 0
        for root, dirs, files in p.walk():
            # Filter out skip dirs in-place
            dirs[:] = [d for d in dirs if d not in DEFAULT_SKIP_DIRS]
            for f in files:
                if f == ".DS_Store":
                    continue
                fpath = root / f
                c = self.ingest_file(fpath, wing=wing, room=room)
                count += c
        return count
