"""Unit tests for the deterministic world_state → entities extractor.

The extractor is a pure function: no clock, no rng, no I/O, no LLM. The same
world_state must always yield the same entities (stable names are what make
reality-tracking's prev_state→state comparison meaningful).
"""
from conscio.world_extract import extract_entities, extract_relations
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


# --- extract_relations: [source] header → (source, "reports", fact) edges ---
# The frame header is the ONLY relation source in the producer contract
# (PerceptionFrame.to_world_state emits it unconditionally); no syntax is
# invented that nothing produces.

def test_relations_from_header_and_facts():
    ws = "[host:cpu]\nload=0.85\nstatus: hot"
    assert extract_relations(ws) == [
        ("host:cpu", "reports", "load"),
        ("host:cpu", "reports", "status"),
    ]


def test_no_header_no_relations():
    # Facts without a frame source have no edge to hang off — never guess.
    assert extract_relations("status: degraded\nload=0.85") == []


def test_freetext_under_header_yields_no_relation():
    assert extract_relations("[host]\nBTC spiked 2% overnight") == []


def test_header_resets_per_frame_section():
    # The daemon joins frames with a blank line; each header rebinds source.
    ws = "[host:cpu]\nload=0.85\n\n[market:exch]\nstatus: ok"
    assert extract_relations(ws) == [
        ("host:cpu", "reports", "load"),
        ("market:exch", "reports", "status"),
    ]


def test_duplicate_fact_yields_single_relation():
    ws = "[host]\nstatus: ok\nstatus: degraded"
    assert extract_relations(ws) == [("host", "reports", "status")]


def test_relations_empty_and_deterministic():
    assert extract_relations("") == []
    ws = "[host]\nload=0.85\nstatus: hot"
    assert extract_relations(ws) == extract_relations(ws)


def test_relations_roundtrip_from_real_production_producer():
    frame = event_to_frame({
        "type": "obs", "source": "exch", "category": "market",
        "payload": {"status": "degraded", "latency_ms": 245},
    })
    rels = extract_relations(frame.to_world_state())
    assert ("market:exch", "reports", "status") in rels
    assert ("market:exch", "reports", "latency_ms") in rels
