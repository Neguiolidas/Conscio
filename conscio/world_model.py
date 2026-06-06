"""
World Model — Knowledge graph of entities, relations, and states.

The agent's "model of the world": what exists, how things relate,
and what state they're in. Updated by perception and reflection.
Queried on-demand for context injection (only relevant subgraph).
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Entropy scoring (v0.4): higher entropy = more disordered = better prune candidate.
HALFLIFE_DAYS = 7        # age normalization half-life
MAX_RELATIONS = 8        # relations at/above which an entity is fully "connected"
W_AGE = 0.4              # entropy weights — must sum to 1.0
W_ISO = 0.3
W_REL = 0.3


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


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
        existing = self._data["entities"].get(name)
        relevance = existing.get("relevance", 1.0) if existing else 1.0
        # Boost relevance on re-add (entity is being referenced)
        relevance = min(relevance + 0.3, 1.0)
        self._data["entities"][name] = {
            "type": entity_type,
            "attributes": attributes or {},
            "state": state,
            "last_updated": datetime.now().isoformat(),
            "relevance": relevance,
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
        # Boost relevance of both entities
        self._boost_relevance(from_entity)
        self._boost_relevance(to_entity)
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
                # Boost relevance when entity is queried
                self._boost_relevance(name)

        # Find matching relations
        for r in self._data["relations"]:
            if (keyword in r["from"].lower() or
                keyword in r["relation"].lower() or
                keyword in r["to"].lower()):
                relevant_relations.append(r)

        if relevant_entities:
            self._save()

        if not relevant_entities and not relevant_relations:
            return "No relevant information found."

        # Build compact response
        parts = []
        for name, info in relevant_entities.items():
            parts.append(f"{name} ({info['type']}): {info.get('state', 'unknown')}")

        for r in relevant_relations:
            parts.append(f"{r['from']} \u2192 {r['relation']} \u2192 {r['to']}")

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
        """Find entities whose state hasn't been updated or whose relevance is low."""
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        stale = []
        for name, info in self._data["entities"].items():
            relevance = info.get("relevance", 1.0)
            # Entity is stale if low relevance OR too old
            if relevance < 0.2:
                stale.append(name)
                continue
            try:
                updated = datetime.fromisoformat(info.get("last_updated", "2000-01-01"))
                if updated < cutoff:
                    stale.append(name)
            except (ValueError, TypeError):
                stale.append(name)
        return stale

    # --- Relevance & Decay ---

    def _boost_relevance(self, name: str, amount: float = 0.3) -> None:
        """Boost an entity's relevance (capped at 1.0)."""
        if name in self._data["entities"]:
            current = self._data["entities"][name].get("relevance", 1.0)
            self._data["entities"][name]["relevance"] = min(current + amount, 1.0)

    def _compute_relevance(self, hours_since_update: float, current_relevance: float) -> float:
        """
        Compute decayed relevance.

        Formula: relevance * exp(-lambda * hours)
        Lambda = 0.05 → half-life ~14 hours
        """
        decay = math.exp(-0.05 * hours_since_update)
        return current_relevance * decay

    def entropy(self, name: str, _relevance: Optional[float] = None) -> float:
        """
        Entropy score in [0, 1] for an entity. Higher = more disordered.

        entropy = W_AGE*age_norm + W_ISO*isolation + W_REL*rel_gap
          age_norm  = 1 - exp(-age_days / HALFLIFE_DAYS)
          isolation = 1 - min(relation_count / MAX_RELATIONS, 1.0)
          rel_gap   = 1 - clamp01(relevance)

        A connected, relevant node stays low even when old; an isolated,
        faded node climbs toward 1.0. Unknown name -> 1.0 (nothing to keep).

        _relevance: optional override (used by prune_by_entropy's dry_run to
        project decay without persisting).
        """
        info = self._data["entities"].get(name)
        if info is None:
            return 1.0

        now = datetime.now()
        try:
            last = datetime.fromisoformat(info.get("last_updated", "2000-01-01"))
            age_days = max((now - last).total_seconds() / 86400.0, 0.0)
            age_norm = 1.0 - math.exp(-age_days / HALFLIFE_DAYS)
        except (ValueError, TypeError):
            age_norm = 1.0

        relation_count = len(self.get_relations(name))
        isolation = 1.0 - min(relation_count / MAX_RELATIONS, 1.0)

        rel = info.get("relevance", 1.0) if _relevance is None else _relevance
        rel_gap = 1.0 - _clamp01(rel)

        return _clamp01(W_AGE * age_norm + W_ISO * isolation + W_REL * rel_gap)

    def decay_all_entities(self) -> int:
        """
        Recalculate relevance for all entities based on time decay.
        
        Returns the number of entities updated.
        """
        now = datetime.now()
        updated = 0
        for name, info in self._data["entities"].items():
            try:
                last = datetime.fromisoformat(info.get("last_updated", "2000-01-01"))
                hours = (now - last).total_seconds() / 3600
                current = info.get("relevance", 1.0)
                new_rel = self._compute_relevance(hours, current)
                # Only update if significantly different
                if abs(new_rel - current) > 0.001:
                    info["relevance"] = new_rel
                    updated += 1
            except (ValueError, TypeError):
                info["relevance"] = 0.0
                updated += 1
        if updated > 0:
            self._save()
        return updated

    def prune_irrelevant(self, min_relevance: float = 0.1) -> int:
        """
        Remove entities below the minimum relevance threshold.
        
        Returns the number of entities pruned.
        """
        to_remove = [
            name for name, info in self._data["entities"].items()
            if info.get("relevance", 1.0) < min_relevance
        ]
        for name in to_remove:
            self.remove_entity(name)
        return len(to_remove)

    def prune_stale(
        self,
        min_relevance: float = 0.2,
        max_age_hours: int = 168,
        dry_run: bool = False,
    ) -> list[str]:
        """
        Decay relevance, then remove stale entities (and their relations).

        An entity is pruned if, after decay, its relevance is below
        ``min_relevance`` OR it has not been updated within ``max_age_hours``
        (default 7 days — deliberately more conservative than the 24h
        advisory threshold used by ``stale_entities``).

        Args:
            min_relevance: Relevance floor; below this → prune.
            max_age_hours: Age ceiling in hours; older → prune.
            dry_run: If True, return the names that WOULD be pruned without
                     decaying or deleting anything. The preview projects decay
                     in-line so it matches what a real (non-dry) run removes.

        Returns:
            List of entity names removed (or that would be removed if dry_run).
        """
        if not dry_run:
            self.decay_all_entities()

        now = datetime.now()
        cutoff = now - timedelta(hours=max_age_hours)
        to_remove: list[str] = []
        for name, info in self._data["entities"].items():
            # Effective relevance: a real run has already decayed+saved, so
            # info["relevance"] is current. dry_run projects the same decay
            # in-line (no persistence) so the preview matches a real run.
            relevance = info.get("relevance", 1.0)
            if dry_run:
                try:
                    last = datetime.fromisoformat(info.get("last_updated", "2000-01-01"))
                    hours = (now - last).total_seconds() / 3600
                    relevance = self._compute_relevance(hours, relevance)
                except (ValueError, TypeError):
                    relevance = 0.0

            if relevance < min_relevance:
                to_remove.append(name)
                continue
            try:
                updated = datetime.fromisoformat(info.get("last_updated", "2000-01-01"))
                if updated < cutoff:
                    to_remove.append(name)
            except (ValueError, TypeError):
                to_remove.append(name)

        if not dry_run:
            for name in to_remove:
                self.remove_entity(name)  # also drops relations + saves

        return to_remove

    def prune_by_entropy(self, threshold: float = 0.85, dry_run: bool = False) -> list[str]:
        """
        Decay relevance, then remove entities whose entropy exceeds ``threshold``.

        Unlike ``prune_stale`` (relevance OR age), entropy lets connectivity
        rescue an old node: a well-connected, still-relevant entity stays below
        threshold even when old, while isolated/faded nodes are pruned.

        dry_run projects the decay in-line (no persistence) so the preview
        matches a real run, matching the fidelity discipline of ``prune_stale``.

        Returns the list of names removed (or that would be removed if dry_run).
        """
        if not dry_run:
            self.decay_all_entities()

        now = datetime.now()
        to_remove: list[str] = []
        for name, info in self._data["entities"].items():
            if dry_run:
                try:
                    last = datetime.fromisoformat(info.get("last_updated", "2000-01-01"))
                    hours = (now - last).total_seconds() / 3600.0
                    current = info.get("relevance", 1.0)
                    projected = self._compute_relevance(hours, current)
                    # Mirror decay_all_entities: it skips writes when the change is
                    # negligible (<= 0.001), so a real run keeps the un-decayed value.
                    # Apply the same skip here to keep dry_run/real parity exact.
                    if abs(projected - current) <= 0.001:
                        projected = current
                except (ValueError, TypeError):
                    projected = 0.0
                score = self.entropy(name, _relevance=projected)
            else:
                score = self.entropy(name)
            if score > threshold:
                to_remove.append(name)

        if not dry_run:
            for name in to_remove:
                self.remove_entity(name)  # drops relations + saves

        return to_remove

    def recently_changed(self, hours: int = 24) -> set[str]:
        """Names of entities whose ``last_updated`` is within the last ``hours``."""
        cutoff = datetime.now() - timedelta(hours=hours)
        changed: set[str] = set()
        for name, info in self._data["entities"].items():
            try:
                updated = datetime.fromisoformat(info.get("last_updated", "2000-01-01"))
                if updated >= cutoff:
                    changed.add(name)
            except (ValueError, TypeError):
                continue
        return changed

    def to_dict(self) -> dict:
        """Return the raw world model data."""
        return dict(self._data)

    def status(self) -> dict:
        """Return status for monitoring."""
        entities = self._data["entities"]
        relevances = [info.get("relevance", 1.0) for info in entities.values()]
        avg_rel = sum(relevances) / len(relevances) if relevances else 0.0
        return {
            "entities": len(entities),
            "relations": len(self._data["relations"]),
            "predictions": len(self._data["predictions"]),
            "stale": len(self.stale_entities()),
            "avg_relevance": round(avg_rel, 3),
            "prunable": sum(1 for r in relevances if r < 0.1),
            "path": str(self.path),
        }
