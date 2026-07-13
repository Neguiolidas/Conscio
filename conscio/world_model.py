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

from .guards import atomic_write_text, read_json_dict

# Entropy scoring (v0.4): higher entropy = more disordered = better prune candidate.
HALFLIFE_DAYS = 7        # age normalization half-life
MAX_RELATIONS = 8        # relations at/above which an entity is fully "connected"
W_AGE = 0.4              # entropy weights — must sum to 1.0
W_ISO = 0.3
W_REL = 0.3

# Prediction-error log (v0.4): bounded sliding window, kept in the world JSON.
PREDICTION_LOG_RETENTION_HOURS = 168   # 7 days
PREDICTION_LOG_MAX = 500               # hard cap backstop

# Relevance decay (v0.4): exponential memory decay per hour since last update.
RELEVANCE_DECAY_LAMBDA = 0.05          # lambda in exp(-lambda*hours) — half-life ~14h

# Per-entity state history (v0.8): bounded log for state-contradiction scans.
STATE_LOG_MAX = 5


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
        """Load world model from disk.

        B-011: merge over the default skeleton so a valid-but-incomplete file
        (legacy/migrated, missing relations/predictions/prediction_log) can't
        KeyError on first use.
        """
        return read_json_dict(
            self.path,
            {"entities": {}, "relations": [], "predictions": [], "prediction_log": []},
        )

    def _save(self) -> None:
        """Save world model to disk."""
        atomic_write_text(self.path,
                          json.dumps(self._data, indent=2, ensure_ascii=False))

    # --- Entities ---

    def add_entity(self, name: str, entity_type: str,
                   attributes: Optional[dict] = None,
                   state: str = "") -> None:
        """Add or update an entity in the world model.

        Preserves and extends the bounded state_log (v0.8): a state that differs
        from the prior one is appended (kept to STATE_LOG_MAX). Re-adding clears
        any stale `contradicted` flag — re-perception invalidates the cached
        verdict until the next dream reconciles.

        NOTE (pre-existing contract): calling without `state=` resets `state` to
        "" (and logs nothing, since "" is falsy). A re-add without a state blanks
        the entity's state — expected, not new in v0.8.
        """
        existing = self._data["entities"].get(name)
        relevance = existing.get("relevance", 1.0) if existing else 1.0
        # Boost relevance on re-add (entity is being referenced)
        relevance = min(relevance + 0.3, 1.0)
        state_log = list(existing.get("state_log", [])) if existing else []
        prev_state = existing.get("state", "") if existing else ""
        if state and state != prev_state:
            state_log.append({"state": state, "ts": datetime.now().isoformat()})
        state_log = state_log[-STATE_LOG_MAX:]  # normalize even an oversized legacy log
        # Reality tracking (v0.4 producer wiring): re-perceiving a KNOWN entity
        # tests the model's prior belief (prev_state) against the fresh
        # observation (state). Match = confirmation (error 0); mismatch =
        # surprise (error 1). This is the SOLE production producer feeding
        # recent_prediction_error_rate() -> reality_score() (a coherence
        # dimension) and engine meta_confidence. Skip first-ever observations
        # (no prior belief) and blanking re-adds (empty new state) so the rate
        # stays meaningful. No save here — the _save() below persists both the
        # entity update and the log entry in one write.
        if existing is not None and prev_state and state:
            self._log_prediction_outcome(name, prev_state, state)
        self._data["entities"][name] = {
            "type": entity_type,
            "attributes": attributes or {},
            "state": state,
            "last_updated": datetime.now().isoformat(),
            "relevance": relevance,
            "state_log": state_log,
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
        """Update an entity's state, appending to the bounded state_log only when
        the state actually changes (dedup consecutive identical)."""
        if name not in self._data["entities"]:
            return
        info = self._data["entities"][name]
        if state != info.get("state", ""):
            info.setdefault("state_log", []).append(
                {"state": state, "ts": datetime.now().isoformat()})
        info["state"] = state
        info["last_updated"] = datetime.now().isoformat()
        if "state_log" in info:
            info["state_log"] = info["state_log"][-STATE_LOG_MAX:]
        self._save()

    def get_entity(self, name: str) -> Optional[dict]:
        """Get an entity by name."""
        return self._data["entities"].get(name)

    def list_entities(self, limit: int = 5) -> list[dict]:
        """Top-N entities by relevance (descending). Each dict carries its 'name'."""
        items = [{"name": name, **info} for name, info in self._data["entities"].items()]
        items.sort(key=lambda e: e.get("relevance", 0.0), reverse=True)
        return items[:limit]

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

    def list_relations(self) -> list[dict]:
        """All relations as a shallow-copied list (public read — replaces the
        private world._data scan in coherence/dreaming, killing the v0.6 tech debt)."""
        return [dict(r) for r in self._data["relations"]]

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
        decay = math.exp(-RELEVANCE_DECAY_LAMBDA * hours_since_update)
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

    def _log_prediction_outcome(self, entity: str, expected_state: str,
                                actual_state: str) -> bool:
        """Append a prediction outcome to the bounded sliding-window log WITHOUT
        saving (the caller persists). Entries older than
        PREDICTION_LOG_RETENTION_HOURS are dropped, then a hard cap of
        PREDICTION_LOG_MAX newest entries is enforced (prevents file inflation).
        Returns True on surprise (mismatch)."""
        error = 0 if expected_state == actual_state else 1
        now = datetime.now()
        log = self._data.setdefault("prediction_log", [])
        log.append({
            "entity": entity,
            "expected": expected_state,
            "actual": actual_state,
            "error": error,
            "ts": now.isoformat(),
        })

        cutoff = now - timedelta(hours=PREDICTION_LOG_RETENTION_HOURS)
        kept: list[dict] = []
        for e in log:
            try:
                if datetime.fromisoformat(e["ts"]) >= cutoff:
                    kept.append(e)
            except (ValueError, TypeError, KeyError):
                continue
        if len(kept) > PREDICTION_LOG_MAX:
            kept = kept[-PREDICTION_LOG_MAX:]
        self._data["prediction_log"] = kept
        return bool(error)

    def record_prediction(self, entity: str, expected_state: str, actual_state: str) -> bool:
        """
        Record a world prediction outcome. Returns True on surprise (mismatch).

        Public API for direct/explicit recording. The PRIMARY production
        producer is add_entity() re-perception (see _log_prediction_outcome).
        Appends to the bounded sliding-window log and persists.
        """
        surprise = self._log_prediction_outcome(entity, expected_state, actual_state)
        self._save()
        return surprise

    def recent_prediction_error_rate(self, window_hours: int = 24) -> float:
        """Fraction of recorded predictions in the window that were wrong (0.0 if none)."""
        log = self._data.get("prediction_log", [])
        if not log:
            return 0.0
        cutoff = datetime.now() - timedelta(hours=window_hours)
        errors = 0
        total = 0
        for e in log:
            try:
                if datetime.fromisoformat(e["ts"]) >= cutoff:
                    total += 1
                    errors += int(e.get("error", 0))
            except (ValueError, TypeError, KeyError):
                continue
        return errors / total if total else 0.0

    def to_dict(self) -> dict:
        """Return the raw world model data."""
        return dict(self._data)

    def entity_count(self) -> int:
        """Total entities (public read — coherence touches no private state)."""
        return len(self._data["entities"])

    def mark_contradictions(self, detector, dry_run: bool = False) -> list[str]:
        """Scan relations (per from→to pair) + entity state_logs via `detector`,
        writing a cached `contradicted: bool` onto each entity. The ONLY place
        ontology embedding I/O happens — runs off the hot path (dream Reconcile).

        Relations flag their `from` entity, but ONLY when it is a real entity —
        an orphan `from` (relation referencing a non-modeled name) is skipped, so
        the returned set always equals what `contradicted_entities()` will read.

        State-log contradictions use the bounded window: a flag set from opposed
        states (e.g. operational/offline) is SELF-RESOLVING — it ages out as the
        opposed state rolls past STATE_LOG_MAX. Relations are the primary
        simultaneous-contradiction signal; state-log is a recency-window heuristic.

        Returns the sorted names flagged. dry_run computes the would-flag set
        WITHOUT writing (so a dry dream reports without mutating the graph).
        """
        flagged: set[str] = set()

        by_pair: dict = {}
        for r in self._data["relations"]:
            by_pair.setdefault((r.get("from", ""), r.get("to", "")), []).append(
                r.get("relation", "")
            )
        for (frm, _to), preds in by_pair.items():
            if not frm or frm not in self._data["entities"]:
                continue
            if any(detector.relations_contradict(preds[i], preds[j])
                   for i in range(len(preds)) for j in range(i + 1, len(preds))):
                flagged.add(frm)

        for name, info in self._data["entities"].items():
            states = [e.get("state", "") for e in info.get("state_log", [])]
            if any(detector.states_contradict(states[i], states[j])
                   for i in range(len(states)) for j in range(i + 1, len(states))):
                flagged.add(name)

        if not dry_run:
            for name, info in self._data["entities"].items():
                info["contradicted"] = name in flagged
            self._save()

        return sorted(flagged)

    def contradicted_entities(self) -> list[str]:
        """Cheap read of cached `contradicted` flags. No network, no scan."""
        return [n for n, info in self._data["entities"].items()
                if info.get("contradicted")]

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
