"""Unit tests for the deterministic world_state → entities extractor.

The extractor is a pure function: no clock, no rng, no I/O, no LLM. The same
world_state must always yield the same entities (stable names are what make
reality-tracking's prev_state→state comparison meaningful).
"""
from conscio.world_extract import extract_entities
from conscio.mcp.schemas import event_to_frame


def test_extracts_string_fact():
    assert extract_entities("status: degraded") == {
        "status": {"type": "attribute", "state": "degraded", "attributes": {}},
    }


def test_extracts_bool_fact():
    e = extract_entities("alerting=True")
    assert e["alerting"]["state"] == "True"
    assert e["alerting"]["type"] == "flag"


def test_extracts_numeric_signal():
    e = extract_entities("latency_ms=245.0")
    assert e["latency_ms"]["state"] == "245.0"
    assert e["latency_ms"]["type"] == "metric"


def test_skips_source_header():
    assert extract_entities("[market:exchange]") == {}


def test_skips_freetext_observation():
    # No `key: value` / `key=value` shape → not a structured fact.
    assert extract_entities("BTC spiked 2% overnight") == {}


def test_mixed_frame_keeps_only_structured_facts():
    ws = "[market:exchange]\nBTC spiked 2%\nstatus: ok\nalerting=False\nrsi=71.5"
    e = extract_entities(ws)
    assert set(e) == {"status", "alerting", "rsi"}
    assert e["status"]["state"] == "ok"
    assert e["alerting"]["type"] == "flag"
    assert e["rsi"]["type"] == "metric"


def test_last_line_wins_on_duplicate_key():
    # Later observation is the current state.
    e = extract_entities("status: ok\nstatus: degraded")
    assert e["status"]["state"] == "degraded"


def test_value_may_contain_equals_when_colon_form():
    # colon-space form is tried first, so `=` inside the value is preserved.
    e = extract_entities("query: a=b&c=d")
    assert e["query"]["state"] == "a=b&c=d"


def test_empty_and_blank_input_yields_empty():
    assert extract_entities("") == {}
    assert extract_entities("   \n\n\t") == {}


def test_deterministic_same_input_same_output():
    ws = "status: ok\nrsi=71.5\nalerting=True"
    assert extract_entities(ws) == extract_entities(ws)


def test_roundtrip_from_real_production_producer():
    # The real prod path: event → frame → world_state → entities.
    frame = event_to_frame({
        "type": "obs", "source": "exch", "category": "market",
        "payload": {"status": "degraded", "alerting": True, "latency_ms": 245},
    })
    e = extract_entities(frame.to_world_state())
    assert e["status"]["state"] == "degraded"
    assert e["alerting"]["state"] == "True"
    assert e["latency_ms"]["state"] == "245.0"
