# tests/test_liaison_relay.py
import pytest

from conscio.liaison import relay


def test_payload_size_compact():
    assert relay.payload_size({"a": 1}) == len(b'{"a":1}')


# ── envelope_of (v4.5: identidade no envelope) ────────────────────────

def test_envelope_of_returns_meta_from():
    row = {"payload": {"_meta": {"from": {"id": "A", "modelo": "opus"},
                                  "id": 7}, "text": "oi"}}
    assert relay.envelope_of(row) == {"id": "A", "modelo": "opus"}


def test_envelope_of_none_when_absent():
    assert relay.envelope_of({"payload": {"text": "oi"}}) is None


def test_envelope_of_none_when_meta_malformed():
    # _meta existe mas from não é dict → None (defensivo)
    assert relay.envelope_of({"payload": {"_meta": {"from": "A"}, "text": "x"}}) is None
    assert relay.envelope_of({"payload": {"_meta": "notadict"}}) is None


def test_envelope_of_none_when_payload_not_dict():
    assert relay.envelope_of({"payload": "raw string"}) is None


def test_constants():
    assert relay.MAX_PAYLOAD_BYTES == 64 * 1024
    assert relay.RETENTION_DAYS == 7
    assert relay.RESERVED_TYPES == {"review_request", "review_verdict"}


def test_validate_send_happy():
    relay.validate_send(to="B", type="note", payload={"x": 1}, peers={"B"})


def test_validate_send_empty_type():
    with pytest.raises(ValueError):
        relay.validate_send(to="B", type="", payload={}, peers={"B"})


def test_validate_send_reserved_type():
    for t in ("review_request", "review_verdict"):
        with pytest.raises(ValueError):
            relay.validate_send(to="B", type=t, payload={}, peers={"B"})


def test_validate_send_non_dict_payload():
    with pytest.raises(ValueError):
        relay.validate_send(to="B", type="note", payload="nope", peers={"B"})


def test_validate_send_unknown_peer():
    with pytest.raises(ValueError):
        relay.validate_send(to="C", type="note", payload={}, peers={"B"})


def test_validate_send_oversize():
    big = {"x": "a" * (relay.MAX_PAYLOAD_BYTES + 1)}
    with pytest.raises(ValueError):
        relay.validate_send(to="B", type="note", payload=big, peers={"B"})


def test_is_relay_message_peer_ok():
    row = {"from_instance": "B", "type": "note", "payload": {"x": 1}}
    assert relay.is_relay_message(row, {"B"}) is True


def test_is_relay_message_non_peer():
    row = {"from_instance": "Z", "type": "note", "payload": {}}
    assert relay.is_relay_message(row, {"B"}) is False


def test_is_relay_message_reserved_type():
    row = {"from_instance": "B", "type": "review_request", "payload": {}}
    assert relay.is_relay_message(row, {"B"}) is False


def test_is_relay_message_oversize():
    row = {"from_instance": "B", "type": "note",
           "payload": {"x": "a" * (relay.MAX_PAYLOAD_BYTES + 1)}}
    assert relay.is_relay_message(row, {"B"}) is False
