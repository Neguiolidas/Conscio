from conscio.awake.budget import AwakeBudget
from conscio.agency.loop import ActBudget


def test_defaults():
    b = AwakeBudget()
    assert b.max_cycles == 3
    assert b.max_tokens == 20_000
    assert b.max_wall_s == 60.0
    assert b.max_failure_rate == 0.3
    assert b.min_attempts == 2


def test_inherits_act_budget():
    b = AwakeBudget()
    assert isinstance(b, ActBudget)


def test_can_override():
    b = AwakeBudget(max_cycles=5)
    assert b.max_cycles == 5
    assert b.max_tokens == 20_000


def test_is_dataclass():
    from dataclasses import fields
    b = AwakeBudget()
    names = {f.name for f in fields(b)}
    assert "max_cycles" in names
    assert "max_failure_rate" in names
