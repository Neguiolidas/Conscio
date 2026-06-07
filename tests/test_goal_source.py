# tests/test_goal_source.py
from conscio.goal_generator import GoalGenerator


def test_self_prompt_source_carries_and_persists(tmp_path):
    g = GoalGenerator(tmp_path)
    goal = g.generate_from_curiosity("contradiction on X", source="self_prompt")
    assert goal is not None and goal.source == "self_prompt"
    # survives save/load round-trip
    g2 = GoalGenerator(tmp_path)
    loaded = next(x for x in g2._goals if x.id == goal.id)
    assert loaded.source == "self_prompt"


def test_maintenance_and_evolution_accept_source(tmp_path):
    g = GoalGenerator(tmp_path)
    m = g.generate_from_maintenance("self_prompt", "botX", source="self_prompt")
    e = g.generate_from_evolution("shore up blind spot", target="trading", source="self_prompt")
    assert m.source == "self_prompt" and e.source == "self_prompt"


def test_source_defaults_to_internal(tmp_path):
    g = GoalGenerator(tmp_path)
    c = g.generate_from_curiosity("a")
    m = g.generate_from_maintenance("check", "t")
    e = g.generate_from_evolution("improve")
    assert c.source == m.source == e.source == "internal"
