"""
Auto-Evolution — Self-modification with safety gates.

The agent's ability to improve itself:
- Propose skill modifications based on error patterns
- Suggest prompt adjustments based on meta-cognition
- Detect opportunities for new skills
- ALL changes require human approval before being applied
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .meta_cognition import MetaCognition


class EvolutionType(Enum):
    SKILL_PATCH = "skill_patch"           # Modify an existing skill
    SKILL_CREATE = "skill_create"         # Create a new skill
    MEMORY_UPDATE = "memory_update"       # Update memory with new facts
    PROMPT_ADJUST = "prompt_adjust"       # Suggest prompt changes
    CONFIG_CHANGE = "config_change"       # Suggest config changes
    PATTERN_LEARN = "pattern_learn"       # Learn from a recurring pattern


class ProposalStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"


class EvolutionProposal:
    """A proposed self-modification, awaiting human approval."""

    def __init__(
        self,
        evolution_type: EvolutionType,
        description: str,
        rationale: str,
        changes: dict,
        risk_level: str = "low",  # low | medium | high
    ):
        self.id = f"evo_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        self.evolution_type = evolution_type
        self.description = description
        self.rationale = rationale
        self.changes = changes
        self.risk_level = risk_level
        self.status = ProposalStatus.PENDING
        self.created_at = datetime.now().isoformat()
        self.reviewed_at: Optional[str] = None
        self.applied_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.evolution_type.value,
            "description": self.description,
            "rationale": self.rationale,
            "changes": self.changes,
            "risk_level": self.risk_level,
            "status": self.status.value,
            "created_at": self.created_at,
            "reviewed_at": self.reviewed_at,
            "applied_at": self.applied_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EvolutionProposal":
        p = cls(
            evolution_type=EvolutionType(data["type"]),
            description=data["description"],
            rationale=data["rationale"],
            changes=data["changes"],
            risk_level=data.get("risk_level", "low"),
        )
        p.id = data.get("id", p.id)
        p.status = ProposalStatus(data.get("status", "pending"))
        p.created_at = data.get("created_at", p.created_at)
        p.reviewed_at = data.get("reviewed_at")
        p.applied_at = data.get("applied_at")
        return p


class AutoEvolution:
    """
    Self-modification engine with mandatory human approval gates.
    
    SAFETY RULES (non-negotiable):
    1. No modification is ever applied automatically
    2. All proposals require explicit human approval
    3. High-risk proposals require additional confirmation
    4. All changes are logged and reversible
    5. The engine cannot modify its own safety rules
    """

    PROPOSAL_FILE = "evolution_proposals.json"

    def __init__(self, storage_path: Path):
        self.path = storage_path / self.PROPOSAL_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._proposals: list[EvolutionProposal] = self._load()

    def _load(self) -> list[EvolutionProposal]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                return [EvolutionProposal.from_dict(p) for p in data]
            except (json.JSONDecodeError, KeyError):
                pass
        return []

    def _save(self) -> None:
        data = [p.to_dict() for p in self._proposals]
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    # --- Proposal Generation ---

    def propose_skill_patch(
        self,
        skill_name: str,
        issue: str,
        suggested_fix: str,
        rationale: str,
    ) -> EvolutionProposal:
        """Propose a modification to an existing skill."""
        proposal = EvolutionProposal(
            evolution_type=EvolutionType.SKILL_PATCH,
            description=f"Patch skill '{skill_name}': {issue}",
            rationale=rationale,
            changes={
                "skill_name": skill_name,
                "issue": issue,
                "suggested_fix": suggested_fix,
            },
            risk_level="low",
        )
        self._proposals.append(proposal)
        self._save()
        return proposal

    def propose_skill_create(
        self,
        skill_name: str,
        description: str,
        content_sketch: str,
        rationale: str,
    ) -> EvolutionProposal:
        """Propose creating a new skill."""
        proposal = EvolutionProposal(
            evolution_type=EvolutionType.SKILL_CREATE,
            description=f"Create skill '{skill_name}': {description}",
            rationale=rationale,
            changes={
                "skill_name": skill_name,
                "description": description,
                "content_sketch": content_sketch,
            },
            risk_level="medium",
        )
        self._proposals.append(proposal)
        self._save()
        return proposal

    def propose_memory_update(
        self,
        key: str,
        value: str,
        rationale: str,
    ) -> EvolutionProposal:
        """Propose updating a memory entry."""
        proposal = EvolutionProposal(
            evolution_type=EvolutionType.MEMORY_UPDATE,
            description=f"Update memory '{key}'",
            rationale=rationale,
            changes={"key": key, "value": value},
            risk_level="low",
        )
        self._proposals.append(proposal)
        self._save()
        return proposal

    def propose_pattern_learn(
        self,
        pattern: str,
        lesson: str,
        rationale: str,
    ) -> EvolutionProposal:
        """Propose learning from a recurring pattern."""
        proposal = EvolutionProposal(
            evolution_type=EvolutionType.PATTERN_LEARN,
            description=f"Learn pattern: {pattern}",
            rationale=rationale,
            changes={"pattern": pattern, "lesson": lesson},
            risk_level="low",
        )
        self._proposals.append(proposal)
        self._save()
        return proposal

    # --- Auto-observation from MetaCognition ---

    def observe_errors(self, meta_cognition: "MetaCognition") -> list[EvolutionProposal]:
        """
        Observe error patterns from MetaCognition and auto-propose fixes.

        For each frequent error (count >= 2), generates a PATTERN_LEARN proposal
        with the pattern and a suggested lesson. Deduplicates against existing
        pending proposals to avoid spam.

        Returns list of newly created proposals (empty if nothing new).
        """

        frequent = meta_cognition.frequent_errors(min_count=2)
        if not frequent:
            return []

        # Dedup: existing pending proposal descriptions
        existing_descs = {p.description for p in self.pending_proposals()}

        new_proposals = []
        for error in frequent:
            pattern = error.get("pattern", "unknown")
            count = error.get("count", 0)
            # This must match what propose_pattern_learn generates as description
            expected_desc = f"Learn pattern: Recurring error: {pattern}"

            if expected_desc in existing_descs:
                continue

            # Infer a lesson from the error pattern
            lesson = f"Add guard/check for '{pattern}' — occurred {count}x without resolution"

            proposal = self.propose_pattern_learn(
                pattern=f"Recurring error: {pattern}",
                lesson=lesson,
                rationale=f"Auto-observed: error '{pattern}' occurred {count} times. "
                          f"Suggesting pattern capture to prevent recurrence.",
            )
            new_proposals.append(proposal)
            existing_descs.add(proposal.description)

        return new_proposals

    # --- Review & Approval ---

    def approve(self, proposal_id: str) -> Optional[EvolutionProposal]:
        """Approve a pending proposal. Returns None if not found or not pending."""
        for p in self._proposals:
            if p.id == proposal_id and p.status == ProposalStatus.PENDING:
                p.status = ProposalStatus.APPROVED
                p.reviewed_at = datetime.now().isoformat()
                self._save()
                return p
        return None

    def reject(self, proposal_id: str, reason: str = "") -> Optional[EvolutionProposal]:
        """Reject a pending proposal."""
        for p in self._proposals:
            if p.id == proposal_id and p.status == ProposalStatus.PENDING:
                p.status = ProposalStatus.REJECTED
                p.reviewed_at = datetime.now().isoformat()
                p.changes["rejection_reason"] = reason
                self._save()
                return p
        return None

    def mark_applied(self, proposal_id: str) -> Optional[EvolutionProposal]:
        """Mark an approved proposal as successfully applied."""
        for p in self._proposals:
            if p.id == proposal_id and p.status == ProposalStatus.APPROVED:
                p.status = ProposalStatus.APPLIED
                p.applied_at = datetime.now().isoformat()
                self._save()
                return p
        return None

    def mark_rolled_back(self, proposal_id: str) -> Optional[EvolutionProposal]:
        """Mark an applied proposal as rolled back (reverted)."""
        for p in self._proposals:
            if p.id == proposal_id and p.status == ProposalStatus.APPLIED:
                p.status = ProposalStatus.ROLLED_BACK
                self._save()
                return p
        return None

    # --- Queries ---

    def pending_proposals(self) -> list[EvolutionProposal]:
        """Get all pending proposals awaiting approval."""
        return [p for p in self._proposals if p.status == ProposalStatus.PENDING]

    def recent_proposals(self, n: int = 10) -> list[EvolutionProposal]:
        """Get the most recent proposals regardless of status."""
        return self._proposals[-n:]

    def applied_proposals(self) -> list[EvolutionProposal]:
        """Get all successfully applied proposals."""
        return [p for p in self._proposals if p.status == ProposalStatus.APPLIED]

    # --- Summary ---

    def summary(self) -> str:
        """Compact summary for context injection."""
        pending = len(self.pending_proposals())
        applied = len(self.applied_proposals())
        return f"Evolution: {pending} pending, {applied} applied"

    def to_dict(self) -> list[dict]:
        return [p.to_dict() for p in self._proposals]

    def status(self) -> dict:
        return {
            "total_proposals": len(self._proposals),
            "pending": len(self.pending_proposals()),
            "applied": len(self.applied_proposals()),
            "rejected": len([p for p in self._proposals if p.status == ProposalStatus.REJECTED]),
            "path": str(self.path),
        }
