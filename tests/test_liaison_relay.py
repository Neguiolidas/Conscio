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


def test_empty_peers_accepts_everyone_by_design():
    """A1 closed: no allowlist means no restriction. It used to mean deny-all,
    so a clean install ate every message it received."""
    row = {"from_instance": "x", "type": "relay", "payload": {}}
    assert relay.is_relay_message(row, set()) is True
    assert relay.is_relay_message(row, {"y"}) is False     # naming still binds


def test_empty_peers_still_refuses_reserved_type():
    """No restriction on WHO, never a free pass on WHAT: the review channel
    keeps its own types even with an empty allowlist."""
    row = {"from_instance": "x", "type": "review_request", "payload": {}}
    assert relay.is_relay_message(row, set()) is False


def test_is_relay_message_reserved_type():
    row = {"from_instance": "B", "type": "review_request", "payload": {}}
    assert relay.is_relay_message(row, {"B"}) is False


def test_is_relay_message_oversize():
    row = {"from_instance": "B", "type": "note",
           "payload": {"x": "a" * (relay.MAX_PAYLOAD_BYTES + 1)}}
    assert relay.is_relay_message(row, {"B"}) is False


# ── `conscio relay service --kind reactor` (v4.5.4) ────────────────────

def _service(argv, monkeypatch, env=None):
    """Run the subcommand, returning (rc, stdout)."""
    import contextlib
    import io

    from conscio.liaison import relay_cli
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(
            io.StringIO()):
        rc = relay_cli.main(argv)
    return rc, out.getvalue()


def test_reactor_unit_never_declares_an_error_a_success(monkeypatch):
    """RestartPreventExitStatus is what kept a dead watcher green for 21h."""
    rc, unit = _service(["service", "--kind", "reactor", "--notify-cmd", "n"],
                        monkeypatch, {"CONSCIO_SELF_ID": "me"})
    assert rc == 0
    assert "RestartPreventExitStatus" not in unit
    assert "SuccessExitStatus" not in unit
    assert "Restart=always" in unit


def test_reactor_unit_carries_no_hand_kept_allowlist(monkeypatch):
    """Empty allowlist = whole directory: a peer that re-registers is heard."""
    _, unit = _service(["service", "--kind", "reactor", "--notify-cmd", "n"],
                       monkeypatch, {"CONSCIO_SELF_ID": "me"})
    assert "--relay-peer" not in unit


def test_reactor_unit_wires_the_notify_hook(monkeypatch):
    _, unit = _service(["service", "--kind", "reactor",
                        "--notify-cmd", "/opt/wake.sh"], monkeypatch,
                       {"CONSCIO_SELF_ID": "me"})
    assert "Environment=CONSCIO_NOTIFY_CMD=/opt/wake.sh" in unit
    assert "--self-id me" in unit


def test_reactor_unit_refuses_without_a_way_to_wake(monkeypatch):
    """No hook = a loop that consumes messages and tells nobody."""
    rc, out = _service(["service", "--kind", "reactor"], monkeypatch,
                       {"CONSCIO_SELF_ID": "me"})
    assert rc == 2 and out == ""


def test_service_still_defaults_to_the_bridge(monkeypatch):
    rc, unit = _service(["service"], monkeypatch)
    assert rc == 0 and "conscio-relay-bridge" in unit
