"""
Meta-Cognition — Self-assessment and blind spot detection.

The agent's ability to think about its own thinking:
- Track confidence scores per task type
- Detect recurring failure patterns
- Identify blind spots (areas where confidence is consistently low)
- Record self-critiques after complex interactions
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional


class MetaCognition:
    """
    Self-assessment engine.
    
    Data structure:
    {
        "confidence_history": [
            {"timestamp": "...", "task_type": "...", "confidence": 0.0-1.0, "outcome": "success|failure|partial"}
        ],
        "blind_spots": ["area where confidence is consistently low"],
        "error_patterns": [
            {"pattern": "description", "count": N, "last_seen": "ISO timestamp"}
        ],
        "self_critiques": [
            {"timestamp": "...", "task": "...", "what_i_did": "...", "what_i_should_do": "..."}
        ]
    }
    """

    def __init__(self, storage_path: Path):
        self.path = storage_path / "meta_cognition.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except json.JSONDecodeError:
                pass
        return {
            "confidence_history": [],
            "blind_spots": [],
            "error_patterns": [],
            "self_critiques": [],
        }

    def _save(self) -> None:
        # Keep only last 100 entries per category to prevent unbounded growth
        data = self._data
        data["confidence_history"] = data["confidence_history"][-100:]
        data["self_critiques"] = data["self_critiques"][-50:]
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    # --- Confidence Tracking ---

    def record_confidence(self, task_type: str, confidence: float,
                          outcome: str = "pending") -> None:
        """
        Record a confidence assessment for a task.
        
        Args:
            task_type: Category of task (e.g., "coding", "trading", "debugging")
            confidence: Self-assessed confidence (0-1)
            outcome: "success", "failure", "partial", or "pending"
        """
        self._data["confidence_history"].append({
            "timestamp": datetime.now().isoformat(),
            "task_type": task_type,
            "confidence": confidence,
            "outcome": outcome,
        })
        self._detect_blind_spots()
        self._save()

    def update_outcome(self, task_type: str, outcome: str) -> None:
        """Update the outcome of the most recent confidence record for a task type."""
        for entry in reversed(self._data["confidence_history"]):
            if entry["task_type"] == task_type and entry["outcome"] == "pending":
                entry["outcome"] = outcome
                break
        self._detect_blind_spots()
        self._save()

    def average_confidence(self, task_type: str = "") -> float:
        """Get average confidence, optionally filtered by task type."""
        entries = self._data["confidence_history"]
        if task_type:
            entries = [e for e in entries if e["task_type"] == task_type]
        if not entries:
            return 0.5
        return sum(e["confidence"] for e in entries) / len(entries)

    def accuracy(self, task_type: str = "") -> float:
        """Get accuracy (success rate) for completed tasks."""
        entries = [e for e in self._data["confidence_history"] if e["outcome"] != "pending"]
        if task_type:
            entries = [e for e in entries if e["task_type"] == task_type]
        if not entries:
            return 0.5
        successes = sum(1 for e in entries if e["outcome"] == "success")
        return successes / len(entries)

    def calibration_score(self) -> float:
        """
        How well-calibrated is the agent's confidence?
        
        Perfect calibration: confidence matches accuracy.
        Returns 0-1 where 1 = perfectly calibrated.
        """
        entries = [e for e in self._data["confidence_history"] if e["outcome"] != "pending"]
        if len(entries) < 5:
            return 0.5  # Not enough data

        # Simple calibration: compare average confidence with accuracy
        avg_conf = sum(e["confidence"] for e in entries) / len(entries)
        acc = self.accuracy()
        # Distance from perfect calibration (0 = perfect, 1 = worst)
        distance = abs(avg_conf - acc)
        return 1.0 - distance

    # --- Blind Spot Detection ---

    def _detect_blind_spots(self) -> None:
        """Auto-detect areas where confidence is consistently low or accuracy is poor."""
        # Group by task type
        task_entries: dict[str, list] = {}
        for e in self._data["confidence_history"]:
            task_entries.setdefault(e["task_type"], []).append(e)

        blind_spots = []
        for task, entries in task_entries.items():
            if len(entries) < 3:
                continue
            completed = [e for e in entries if e["outcome"] != "pending"]
            if len(completed) < 2:
                continue
            avg_conf = sum(e["confidence"] for e in completed) / len(completed)
            accuracy = sum(1 for e in completed if e["outcome"] == "success") / len(completed)
            # Blind spot: low confidence OR overconfident but inaccurate
            if avg_conf < 0.4 or (avg_conf > 0.7 and accuracy < 0.4):
                blind_spots.append(task)

        # Merge: keep manually-added blind spots that aren't auto-detected
        auto_detected = set(blind_spots)
        manual_blind_spots = [b for b in self._data.get("blind_spots", [])
                              if b not in auto_detected]
        self._data["blind_spots"] = blind_spots + manual_blind_spots

    # --- Error Patterns ---

    def record_error(self, pattern: str) -> None:
        """Record an error pattern for tracking."""
        # Check if pattern already exists
        for ep in self._data["error_patterns"]:
            if ep["pattern"] == pattern:
                ep["count"] += 1
                ep["last_seen"] = datetime.now().isoformat()
                self._save()
                return
        # New pattern
        self._data["error_patterns"].append({
            "pattern": pattern,
            "count": 1,
            "last_seen": datetime.now().isoformat(),
        })
        self._save()

    def frequent_errors(self, min_count: int = 2) -> list[dict]:
        """Get error patterns that occur frequently."""
        return [
            ep for ep in self._data["error_patterns"]
            if ep["count"] >= min_count
        ]

    # --- Self-Critique ---

    def add_critique(self, task: str, what_i_did: str, what_i_should_do: str) -> None:
        """Record a self-critique after a complex interaction."""
        self._data["self_critiques"].append({
            "timestamp": datetime.now().isoformat(),
            "task": task,
            "what_i_did": what_i_did,
            "what_i_should_do": what_i_should_do,
        })
        self._save()

    def recent_critiques(self, n: int = 5) -> list[dict]:
        """Get the most recent self-critiques."""
        return self._data["self_critiques"][-n:]

    # --- Summary for Context Injection ---

    def summary(self) -> str:
        """
        Generate a compact meta-cognition summary for context injection.
        
        Fits within the meta_cognition token budget.
        """
        parts = []
        avg = self.average_confidence()
        cal = self.calibration_score()

        parts.append(f"Confidence: {avg:.0%} | Calibration: {cal:.0%}")

        if self._data["blind_spots"]:
            spots = ", ".join(self._data["blind_spots"][:3])
            parts.append(f"Blind spots: {spots}")

        freq_errors = self.frequent_errors()
        if freq_errors:
            top = freq_errors[0]
            parts.append(f"Top error: {top['pattern']} ({top['count']}x)")

        return " | ".join(parts) if parts else "No self-assessment data yet."

    def to_dict(self) -> dict:
        return dict(self._data)

    def status(self) -> dict:
        return {
            "confidence_records": len(self._data["confidence_history"]),
            "blind_spots": len(self._data["blind_spots"]),
            "error_patterns": len(self._data["error_patterns"]),
            "critiques": len(self._data["self_critiques"]),
            "avg_confidence": self.average_confidence(),
            "calibration": self.calibration_score(),
            "path": str(self.path),
        }
