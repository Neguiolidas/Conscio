"""Test for conscio.health MCP tool (v3.1.1)."""
from conscio.mcp.schemas import BASE_TOOL_DEFS


def test_health_in_schemas():
    """conscio.health must be present in BASE_TOOL_DEFS with correct shape."""
    names = [t["name"] for t in BASE_TOOL_DEFS]
    assert "conscio.health" in names, "conscio.health missing from schemas"

    health = next(t for t in BASE_TOOL_DEFS if t["name"] == "conscio.health")
    assert "description" in health
    assert "health" in health["description"].lower()
    assert "read-only" in health["description"].lower()
    assert health["inputSchema"]["type"] == "object"
    assert health["inputSchema"]["properties"] == {}


def test_health_check_engine_method(tmp_path):
    """engine.health_check() returns the expected dict shape."""
    from conscio import ConsciousnessEngine

    with ConsciousnessEngine(model_name="t", storage_path=str(tmp_path)) as eng:
        hc = eng.health_check()
        assert "healthy" in hc
        assert "mode" in hc
        assert "model" in hc
        assert "pending_proposals" in hc
        assert "active_goals" in hc
        assert "stale_entities" in hc
        assert hc["healthy"] is True


def test_health_check_is_read_only(tmp_path):
    """health_check must not emit any events (pure read-only)."""
    from conscio import ConsciousnessEngine

    with ConsciousnessEngine(model_name="t", storage_path=str(tmp_path)) as eng:
        before = eng.event_bus.stats()["total_events"]
        hc = eng.health_check()
        after = eng.event_bus.stats()["total_events"]
        assert after == before, (
            f"health_check emitted {after - before} events (not read-only): {hc}")
