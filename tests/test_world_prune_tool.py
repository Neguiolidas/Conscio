# tests/test_world_prune_tool.py
"""`world_prune` — the tool that makes the `prune_stale` maintenance goal
satisfiable.

Field report (v3.9.3, live daemon): the maintenance drive raises an
auto-executable goal to prune stale entities, but the action registry held only
filesystem, memory, event and goal tools. Nothing there could prune a world
model, so the actor reached for the one thing that looked close — fs_write —
invented sandbox paths, failed three times and tripped the breaker. The work the
goal asks for is `WorldModel.prune_stale()`, entirely in-process.
"""
from datetime import datetime, timedelta

import pytest

from conscio import ConsciousnessEngine
from conscio.agency.adapter import MockAdapter
from conscio.agency.tools import Risk, make_default_registry
from conscio.context_manager import ConsciousnessState
from conscio.world_model import WorldModel


@pytest.fixture
def world(tmp_path):
    return WorldModel(tmp_path / "world")


def _age(world: WorldModel, name: str, hours: int) -> None:
    """Backdate an entity so prune_stale sees it as old."""
    stamp = (datetime.now() - timedelta(hours=hours)).isoformat()
    world._data["entities"][name]["last_updated"] = stamp
    world._save()


class TestRegistration:
    def test_absent_without_a_world_model(self, tmp_path):
        reg = make_default_registry(sandbox_root=tmp_path / "sb")
        assert reg.get("world_prune") is None

    def test_present_when_a_world_model_is_wired(self, tmp_path, world):
        reg = make_default_registry(sandbox_root=tmp_path / "sb",
                                    world_model=world)
        assert reg.get("world_prune") is not None
        assert "world_prune" in reg.catalog_text()

    def test_risk_is_medium_so_the_goal_stays_satisfiable(self, tmp_path, world):
        """HIGH never auto-executes (act.py R6). A HIGH world_prune would leave
        the auto-executable maintenance goal exactly as stuck as it was."""
        reg = make_default_registry(sandbox_root=tmp_path / "sb",
                                    world_model=world)
        assert reg.get("world_prune").risk is Risk.MEDIUM

    def test_a_trial_registry_cannot_prune(self, tmp_path):
        """A trial replays a skill authored by ANOTHER instance, in a tmpdir,
        to decide whether to adopt it. It is built with no backends at all
        (engine.py `_run_trial`) — so a foreign skill reaching for world_prune
        fails its trial as an unknown tool instead of pruning local memory."""
        reg = make_default_registry(sandbox_root=tmp_path / "trial",
                                    content_store=None, event_bus=None,
                                    goal_generator=None)
        assert reg.get("world_prune") is None
        assert not reg.dispatch("world_prune", {}).ok

    def test_takes_no_arguments(self, tmp_path, world):
        """The actor invents arguments it is given room to invent — that is how
        the field failure produced paths that did not exist."""
        reg = make_default_registry(sandbox_root=tmp_path / "sb",
                                    world_model=world)
        assert reg.get("world_prune").params == {}


class TestDispatch:
    def test_removes_an_aged_entity_and_names_it(self, tmp_path, world):
        world.add_entity("forgotten_service", "service", state="idle")
        world.add_entity("live_service", "service", state="running")
        _age(world, "forgotten_service", hours=200)     # past the 7-day ceiling

        reg = make_default_registry(sandbox_root=tmp_path / "sb",
                                    world_model=world)
        result = reg.dispatch("world_prune", {})

        assert result.ok
        assert "forgotten_service" in result.output
        assert "forgotten_service" not in world._data["entities"]
        assert "live_service" in world._data["entities"]

    def test_says_why_when_nothing_qualifies(self, tmp_path, world):
        """The drive flags an entity stale at 24h; prune_stale only removes at
        7 days. An empty result is normal, and has to read as normal or the
        actor treats the tool as broken and reaches for something else."""
        world.add_entity("fresh", "service", state="running")

        reg = make_default_registry(sandbox_root=tmp_path / "sb",
                                    world_model=world)
        result = reg.dispatch("world_prune", {})

        assert result.ok
        assert "nothing pruned" in result.output
        assert "fresh" in world._data["entities"]

    def test_long_lists_are_summarised(self, tmp_path, world):
        for i in range(14):
            world.add_entity(f"e{i}", "thing", state="x")
            _age(world, f"e{i}", hours=200)

        reg = make_default_registry(sandbox_root=tmp_path / "sb",
                                    world_model=world)
        result = reg.dispatch("world_prune", {})

        assert result.ok and "pruned 14 stale entities" in result.output
        assert "+4 more" in result.output


class TestLiveWiring:
    def test_the_act_pipeline_registry_exposes_it(self, tmp_path):
        """Registering the tool changes nothing until the live registry is
        actually built with the engine's world model."""
        with ConsciousnessEngine("glm-5.1", storage_path=tmp_path) as eng:
            pipe = eng.attach_adapter(MockAdapter(script=[]),
                                      sandbox_root=tmp_path / "sb")
            assert pipe.registry.get("world_prune") is not None


class TestSkepticSkip:
    """world_prune is in-process GC, no external effect — the skeptic audit
    must be skipped, exactly like memory_note / host_health.

    Field report (v4.4.x, live daemon): the maintenance drive spawned the
    prune_stale goal; the actor picked world_prune every cycle; the skeptic
    LLM refused it (`act:world_prune:skeptic_fail` 37x), and each failure fed
    the next reflect() as a curiosity anomaly -> a new goal -> the same
    proposal -> the same refusal: a noise loop that vetoed legitimate work.
    """

    def test_world_prune_skips_the_skeptic_audit(self, tmp_path, world):
        """A skeptic that would FAIL must not run for world_prune. The actor
        response alone should be enough for the action to proceed — so with a
        script holding one proposal and no audit fill, report must not reflect
        a skeptic rejection."""
        with ConsciousnessEngine("glm-5.1", storage_path=tmp_path) as eng:
            pipe = eng.attach_adapter(
                MockAdapter(script=[
                    '{"tool": "world_prune", "args": {}, "rationale": "r",'
                    ' "expected_outcome": "e"}',
                ]),
                sandbox_root=tmp_path / "sb")
            report = eng.act(ConsciousnessState(active_goals=["prune stale"]))
            # Skip = PASS, audited=False -> the action executes (world_prune
            # is MEDIUM, not HIGH, so it is not parked pending approval).
            assert report.status.value in ("executed", "proposed")
            assert "skeptic" not in (report.reason or "")

    def test_the_loop_does_not_mint_recurring_world_prune_failures(
            self, tmp_path, world):
        """Regression: without the skip, a FAIL verdict on world_prune would
        record act:world_prune:skeptic_fail, which frequent_errors() surfaces
        and reflect() converts into an anomaly -> a curiosity goal -> the same
        doomed proposal. Prove the recorded error never appears."""
        with ConsciousnessEngine("glm-5.1", storage_path=tmp_path) as eng:
            meta = eng.meta
            meta.expire_error("act:world_prune:", max_remove=10)
            pipe = eng.attach_adapter(
                MockAdapter(script=[
                    '{"tool": "world_prune", "args": {}, "rationale": "r",'
                    ' "expected_outcome": "e"}',
                ]),
                sandbox_root=tmp_path / "sb")
            eng.act(ConsciousnessState(active_goals=["prune stale"]))
            pats = meta.frequent_errors(min_count=1)
            assert not any(
                p["pattern"] == "act:world_prune:skeptic_fail"
                for p in pats)
