# tests/test_shard_engine.py
from conscio.shard_engine import Shard, infer_shard, ShardEngine, _event_text


class _BusStub:
    """Captures emit() kwargs without touching sqlite."""
    def __init__(self):
        self.events = []
    def emit(self, **kw):
        self.events.append(kw)


def test_event_text_scans_values_not_keys():
    # The key 'confidence' must NOT appear; only values are scanned.
    text = _event_text({"type": "reflection", "data": {"confidence": 0.8, "note": "all calm"}})
    assert "confidence" not in text
    assert "all calm" in text
    assert "reflection" in text  # type is included


def test_infer_shard_engineer():
    events = [{"type": "tool_call", "data": {"action": "implement new feature"}}]
    assert infer_shard(events) is Shard.ENGINEER


def test_infer_shard_values_only_no_key_noise():
    # 'confidence' is a key, not a value -> no keyword hit -> None.
    events = [{"type": "perception", "data": {"confidence": 0.9, "note": "sky is blue"}}]
    assert infer_shard(events) is None


def test_infer_shard_whole_word_no_substring():
    # 'debugger' contains 'debug', 'bugfix' contains 'bug' — neither is a whole word.
    events = [{"type": "system", "data": {"text": "ran the debugger on bugfix"}}]
    assert infer_shard(events) is None


def test_infer_shard_whole_word_matches_standalone():
    events = [{"type": "error", "data": {"text": "found a bug"}}]
    assert infer_shard(events) is Shard.SECURITY_ANALYST


def test_infer_shard_tiebreak_most_recent_wins():
    # Equal scores (1 each) -> the shard from the newest event (index 0) wins.
    events = [
        {"type": "tool_call", "data": {"text": "refactor module"}},   # idx0 JANITOR
        {"type": "tool_call", "data": {"text": "implement parser"}},  # idx1 ENGINEER
    ]
    assert infer_shard(events) is Shard.JANITOR


def test_infer_shard_no_match_returns_none():
    events = [{"type": "perception", "data": {"text": "the sky is blue today"}}]
    assert infer_shard(events) is None


def test_infer_shard_respects_window():
    # window=1 -> only the newest event counts.
    events = [{"type": "tool_call", "data": {"text": "implement feature"}}]        # ENGINEER
    events += [{"type": "tool_call", "data": {"text": "refactor"}} for _ in range(30)]  # JANITOR
    assert infer_shard(events, window=1) is Shard.ENGINEER


def test_shard_engine_emits_transition_only_on_change():
    bus = _BusStub()
    eng = ShardEngine(bus)

    eng.update([{"type": "tool_call", "data": {"text": "implement feature"}}])
    assert eng.current is Shard.ENGINEER
    assert len(bus.events) == 1
    assert bus.events[0]["data"]["to"] == "ENGINEER"
    assert bus.events[0]["data"]["from"] is None

    # Same shard again -> no new event.
    eng.update([{"type": "tool_call", "data": {"text": "build code"}}])
    assert eng.current is Shard.ENGINEER
    assert len(bus.events) == 1

    # Change -> new event with correct from/to.
    eng.update([{"type": "tool_call", "data": {"text": "refactor cleanup"}}])
    assert eng.current is Shard.JANITOR
    assert len(bus.events) == 2
    assert bus.events[1]["data"]["from"] == "ENGINEER"
    assert bus.events[1]["data"]["to"] == "JANITOR"


def test_shard_engine_none_keeps_last_shard():
    bus = _BusStub()
    eng = ShardEngine(bus)
    eng.update([{"type": "tool_call", "data": {"text": "implement feature"}}])
    eng.update([{"type": "perception", "data": {"text": "quiet window"}}])  # None inferred
    assert eng.current is Shard.ENGINEER       # unchanged
    assert len(bus.events) == 1                # no transition emitted


from conscio.context_manager import ConsciousnessState, ContextManager, ContextMode


def test_state_injection_includes_shard():
    st = ConsciousnessState(shard="ENGINEER", context_mode=ContextMode.COMPACT)
    assert "▷ shard: ENGINEER" in st.to_injection()


def test_state_injection_suppresses_empty_shard():
    st = ConsciousnessState(shard="", context_mode=ContextMode.COMPACT)
    assert "shard:" not in st.to_injection()


def test_state_injection_suppresses_shard_in_minimal():
    st = ConsciousnessState(shard="ENGINEER", context_mode=ContextMode.MINIMAL)
    assert "shard:" not in st.to_injection()


def test_build_state_passes_shard(tmp_path):
    cm = ContextManager(model_name="claude-opus-4-8", storage_path=tmp_path)
    st = cm.build_state(state_summary="working", shard="JANITOR")
    assert st.shard == "JANITOR"
