"""AwakeBudget — binding budget for autonomous Awake Mode operation.

Tighter than the manual ActBudget: fewer cycles, fewer tokens, faster
wall-clock limit, lower failure tolerance. Awake acts conservatively
because it runs without a human in the loop.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..agency.loop import ActBudget


@dataclass
class AwakeBudget(ActBudget):
    max_cycles: int = 3
    max_llm_calls: int = 10
    max_tokens: int = 20_000
    max_wall_s: float = 60.0
    max_failure_rate: float = 0.3
    min_attempts: int = 2
