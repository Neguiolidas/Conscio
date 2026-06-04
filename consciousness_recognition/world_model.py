"""
World Model — Knowledge graph of entities, relations, and states.

The agent's "model of the world": what exists, how things relate,
and what state they're in. Updated by perception and reflection.
Queried on-demand for context injection (only relevant subgraph).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional


class WorldModel:
    """
    Simple knowledge graph for agent world awareness.
    
    Structure:
    {
        "entities": {
            "entity_name": {
                "type": "person|system|project|asset|...",
                "attributes": {...},
                "state": "current state description",
                "last_updated": "ISO timestamp"
            }
        },
        "relations": [
            {"from": "entity_a", "relation": "owns", "to": "entity_b"},
            ...
        ],
        "predictions": [
            {"if": "condition", "then": "predicted outcome", "confidence": 0.0-1.0},
            ...
        ]
    }
    """

    def __init__(self, storage_path: Path):
        self.path = storage_path / "world_model.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict:
        """Load world model from disk."""
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except json.JSONDecodeError:
                pass
        return {"entities": {}, "relations": [], "predictions": []}

    def _save(self) -> None:
        """Save world model to disk."""
        self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False))

    # --- Entities ---

    def add_entity(self, name: str, entity_type: str,
                   attributes: Optional[dict] = None,
                   state: str = "") -> None:
        """Add or update an entity in the world model."""
        self._data["entities"][name] = {
            "type": entity_type,
            "attributes": attributes or {},
            "state": state,
            "last_updated": datetime.now().isoformat(),
        }
        self._save()

    def remove_entity(self, name: str) -> None:
        """Remove an entity and its relations."""
        self._data["entities"].pop(name, None)
        self._data["relations"] = [
            r for r in self._data["relations"]
            if r["from"] != name and r["to"] != name
        ]
        self._save()

    def update_state(self, name: str, state: str) -> None:
        """Update an entity's state."""
        if name in self._data["entities"]:
            self._data["entities"][name]["state"] = state
            self._data["entities"][name]["last_updated"] = datetime.now().isoformat()
            self._save()

    def get_entity(self, name: str) -> Optional[dict]:
        """Get an entity by name."""
        return self._data["entities"].get(name)

    # --- Relations ---

    def add_relation(self, from_entity: str, relation: str, to_entity: str) -> None:
        """Add a relation between two entities."""
        # Avoid duplicates
        for r in self._data["relations"]:
            if r["from"] == from_entity and r["relation"] == relation and r["to"] == to_entity:
                return
        self._data["relations"].append({
            "from": from_entity,
            "relation": relation,
            "to": to_entity,
        })
        self._save()

    def get_relations(self, entity: str) -> list[dict]:
        """Get all relations involving an entity."""
        return [
            r for r in self._data["relations"]
            if r["from"] == entity or r["to"] == entity
        ]

    # --- Predictions ---

    def add_prediction(self, condition: str, outcome: str, confidence: float) -> None:
        """Add a prediction about the world."""
        self._data["predictions"].append({
            "if": condition,
            "then": outcome,
            "confidence": confidence,
            "created": datetime.now().isoformat(),
        })
        self._save()

    def get_predictions(self, keyword: str = "") -> list[dict]:
        """Get predictions, optionally filtered by keyword."""
        if not keyword:
            return self._data["predictions"]
        return [
            p for p in self._data["predictions"]
            if keyword.lower() in p["if"].lower() or keyword.lower() in p["then"].lower()
        ]

    def validate_prediction(self, index: int, was_correct: bool) -> None:
        """Mark a prediction as validated (correct or incorrect)."""
        if 0 <= index < len(self._data["predictions"]):
            self._data["predictions"][index]["validated"] = was_correct
            self._data["predictions"][index]["validated_at"] = datetime.now().isoformat()
            self._save()

    # --- Queries ---

    def query(self, question: str) -> str:
        """
        Natural language query against the world model.
        
        Returns a compact text summary of relevant entities, relations, and predictions.
        Designed to be called by the inner monologue and injected into context.
        """
        keyword = question.lower()
        relevant_entities = {}
        relevant_relations = []

        # Find matching entities
        for name, info in self._data["entities"].items():
            if (keyword in name.lower() or
                keyword in info.get("state", "").lower() or
                keyword in info.get("type", "").lower() or
                any(keyword in str(v).lower() for v in info.get("attributes", {}).values())):
                relevant_entities[name] = info

        # Find matching relations
        for r in self._data["relations"]:
            if (keyword in r["from"].lower() or
                keyword in r["relation"].lower() or
                keyword in r["to"].lower()):
                relevant_relations.append(r)

        if not relevant_entities and not relevant_relations:
            return "No relevant information found."

        # Build compact response
        parts = []
        for name, info in relevant_entities.items():
            parts.append(f"{name} ({info['type']}): {info.get('state', 'unknown')}")

        for r in relevant_relations:
            parts.append(f"{r['from']} → {r['relation']} → {r['to']}")

        return "; ".join(parts)

    def subgraph(self, entity: str, depth: int = 1) -> str:
        """Get a subgraph around an entity, for context injection."""
        visited = set()
        result_parts = []

        def _traverse(name: str, d: int) -> None:
            if name in visited or d < 0:
                return
            visited.add(name)
            info = self._data["entities"].get(name)
            if info:
                result_parts.append(f"{name}({info['type']}): {info.get('state', '?')}")
            for r in self.get_relations(name):
                other = r["to"] if r["from"] == name else r["from"]
                result_parts.append(f"  {r['relation']} → {other}")
                _traverse(other, d - 1)

        _traverse(entity, depth)
        return "\n".join(result_parts) if result_parts else "Entity not found."

    def stale_entities(self, max_age_hours: int = 24) -> list[str]:
        """Find entities whose state hasn't been updated in a while."""
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        stale = []
        for name, info in self._data["entities"].items():
            try:
                updated = datetime.fromisoformat(info.get("last_updated", "2000-01-01"))
                if updated < cutoff:
                    stale.append(name)
            except (ValueError, TypeError):
                stale.append(name)
        return stale

    def to_dict(self) -> dict:
        """Return the raw world model data."""
        return dict(self._data)

    def status(self) -> dict:
        """Return status for monitoring."""
        return {
            "entities": len(self._data["entities"]),
            "relations": len(self._data["relations"]),
            "predictions": len(self._data["predictions"]),
            "stale": len(self.stale_entities()),
            "path": str(self.path),
        }
