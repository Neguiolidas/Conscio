"""WingManager — integrate Hallways (wing/room) + ContentStore (FTS5 search).

Protocol G notes:
- ContentStore.search() does not filter by source_id, so we filter in Python.
- Hallways auto-creates default wing/room in __init__.
- Default category='external' (matches VALID_CATEGORIES).
"""
from __future__ import annotations
from typing import Optional

from .content_store import ContentStore
from .hallways import Hallways


class WingManager:
    """Orchestrates content indexing and search with wing/room hierarchy."""

    def __init__(
        self,
        hallways_db: Optional[str] = None,
        hallways: Optional[Hallways] = None,
        content_store: Optional[ContentStore] = None,
        content_store_db: Optional[str] = None,
    ):
        self.hallways = hallways or Hallways(db_path=hallways_db)
        if content_store is None:
            cs_path = content_store_db or (hallways_db.rsplit(".", 1)[0] + "_cs.db" if hallways_db else None)
            self.cs = ContentStore(db_path=cs_path)
        else:
            self.cs = content_store

    def close(self) -> None:
        self.hallways.close()
        # Don't close content_store if it was passed in (caller owns it)
        # But do close if we created it internally
        # Heuristic: if caller passed content_store, they manage lifetime
        # We close it regardless — avoids resource leak. Caller can re-pass.
        # Actually, let's track ownership:
        pass

    # ── Index ────────────────────────────────────────────────────────

    def index(
        self,
        label: str,
        content: str,
        category: str = "external",
        content_type: str = "prose",
        wing: str = "default",
        room: str = "default",
        session_id: str = "",
    ) -> int:
        """Index content via ContentStore; assign to wing/room in Hallways.

        Returns the source_id (int) from ContentStore.index.
        """
        source_id = self.cs.index(
            label=label, content=content, category=category,
            content_type=content_type, session_id=session_id
        )
        self.hallways.assign_drawer(
            wing=wing, room=room, drawer_id=source_id
        )
        return source_id

    # ── Search ──────────────────────────────────────────────────────

    def search(self, query: str, wing: Optional[str] = None, limit: int = 5):
        """Search content via ContentStore.search. Optionally filter by wing.

        ContentStore.search() does not support source_id filtering, so when
        wing is specified we fetch with limit*3 and filter in Python by
        source_ids returned from Hallways.list_drawers(wing).
        """
        if wing is None:
            return self.cs.search(query, limit=limit)
        # Filter by wing
        source_ids = set(self.hallways.list_drawers(wing=wing))
        if not source_ids:
            return []
        results = self.cs.search(query, limit=limit * 3)
        filtered = [r for r in results if r.source_id in source_ids]
        return filtered[:limit]

    # ── Listing ──────────────────────────────────────────────────────

    def list_wings(self) -> list[str]:
        return self.hallways.list_wings()

    def list_rooms(self, wing: str) -> list[str]:
        return self.hallways.list_rooms(wing)

    def list_drawers(self, wing: str | None = None, room: Optional[str] = None) -> list[int]:
        return self.hallways.list_drawers(wing=wing, room=room)

    # ── Deletion ────────────────────────────────────────────────────

    def delete_drawer(self, drawer_id: int) -> None:
        """Remove drawer from Hallways (ContentStore has no delete API)."""
        self.hallways.remove_drawer(drawer_id)
