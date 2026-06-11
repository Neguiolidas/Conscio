# tests/test_auto_evolution.py
"""Tests for AutoEvolution — self-modification with safety gates."""

import pytest

from conscio.auto_evolution import (
    AutoEvolution,
    EvolutionType,
    ProposalStatus,
)


@pytest.fixture
def evo(tmp_path):
    """Fresh AutoEvolution instance per test."""
    return AutoEvolution(storage_path=tmp_path)


# --- propose_skill_create ---

def test_propose_skill_create_returns_proposal(evo):
    p = evo.propose_skill_create(
        skill_name="deploy",
        description="Deploy to prod",
        content_sketch="steps for deploy",
        rationale="team needs it",
    )
    assert p.evolution_type == EvolutionType.SKILL_CREATE
    assert p.status == ProposalStatus.PENDING
    assert "deploy" in p.description
    assert p.risk_level == "medium"
    assert p.changes["skill_name"] == "deploy"


def test_propose_skill_create_persists(evo):
    evo.propose_skill_create("x", "desc", "sketch", "reason")
    evo2 = AutoEvolution(storage_path=evo.path.parent)
    assert len(evo2.pending_proposals()) == 1


# --- propose_memory_update ---

def test_propose_memory_update(evo):
    p = evo.propose_memory_update("key1", "val1", "reason")
    assert p.evolution_type == EvolutionType.MEMORY_UPDATE
    assert p.changes == {"key": "key1", "value": "val1"}
    assert p.risk_level == "low"


# --- propose_pattern_learn ---

def test_propose_pattern_learn(evo):
    p = evo.propose_pattern_learn("retry_loop", "add backoff", "seen 3x")
    assert p.evolution_type == EvolutionType.PATTERN_LEARN
    assert p.changes["pattern"] == "retry_loop"
    assert p.changes["lesson"] == "add backoff"


# --- mark_applied ---

def test_mark_applied(evo):
    p = evo.propose_skill_create("a", "b", "c", "d")
    evo.approve(p.id)
    result = evo.mark_applied(p.id)
    assert result is not None
    assert result.status == ProposalStatus.APPLIED
    assert result.applied_at is not None


def test_mark_applied_not_approved(evo):
    p = evo.propose_skill_create("a", "b", "c", "d")
    # Still PENDING, not APPROVED → mark_applied returns None
    assert evo.mark_applied(p.id) is None


def test_mark_applied_unknown_id(evo):
    assert evo.mark_applied("nonexistent") is None


# --- mark_rolled_back ---

def test_mark_rolled_back(evo):
    p = evo.propose_skill_create("a", "b", "c", "d")
    evo.approve(p.id)
    evo.mark_applied(p.id)
    result = evo.mark_rolled_back(p.id)
    assert result is not None
    assert result.status == ProposalStatus.ROLLED_BACK


def test_mark_rolled_back_not_applied(evo):
    p = evo.propose_skill_create("a", "b", "c", "d")
    assert evo.mark_rolled_back(p.id) is None


# --- recent_proposals ---

def test_recent_proposals_default(evo):
    for i in range(15):
        evo.propose_skill_create(f"skill_{i}", "d", "s", "r")
    recent = evo.recent_proposals()
    assert len(recent) == 10
    assert recent[-1].changes["skill_name"] == "skill_14"


def test_recent_proposals_custom_n(evo):
    for i in range(5):
        evo.propose_skill_create(f"skill_{i}", "d", "s", "r")
    recent = evo.recent_proposals(n=3)
    assert len(recent) == 3


# --- applied_proposals ---

def test_applied_proposals_empty(evo):
    evo.propose_skill_create("a", "b", "c", "d")
    assert evo.applied_proposals() == []


def test_applied_proposals_with_applied(evo):
    p1 = evo.propose_skill_create("a", "b", "c", "d")
    evo.propose_memory_update("k", "v", "r")
    evo.approve(p1.id)
    evo.mark_applied(p1.id)
    # p2 still pending
    applied = evo.applied_proposals()
    assert len(applied) == 1
    assert applied[0].id == p1.id
