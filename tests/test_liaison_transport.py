"""One exit door for the relay: local spool or remote HTTP, chosen by the card.

The failures this guards: a sender writing into someone else's db, a path
taken from a stranger's card, and an outbox line that claims "sent" when
nothing was delivered.
"""
import json

import pytest

from conscio.liaison import directory, relay_transport


@pytest.fixture(autouse=True)
def _root(tmp_path, monkeypatch):
    monkeypatch.setenv("CONSCIO_RELAY_ROOT", str(tmp_path / "relay"))


def _msg():
    return {"from": "a", "to": "b", "type": "relay", "payload": {"x": 1}}


def test_local_card_writes_to_spool():
    card = {"instance_id": "b", "spool": "whatever", "url": ""}
    assert relay_transport.deliver(card, _msg()) is True
    assert len(list(directory.spool_dir("b").glob("*.json"))) == 1


def test_deliver_ignores_spool_path_from_card(tmp_path):
    """I1: the path comes from the validated id, never from a foreign card."""
    evil = tmp_path / "escape"
    card = {"instance_id": "b", "spool": str(evil), "url": ""}
    relay_transport.deliver(card, _msg())
    assert not evil.exists()
    assert len(list(directory.spool_dir("b").glob("*.json"))) == 1


def test_remote_card_uses_http(monkeypatch):
    sent = {}
    monkeypatch.setattr(relay_transport, "transport_post",
                        lambda url, msg, token, **kw: sent.update(
                            url=url, token=token) or relay_transport.ACCEPTED)
    card = {"instance_id": "b", "spool": "", "url": "http://h:8789"}
    remotes = relay_transport.remotes_path()
    remotes.parent.mkdir(parents=True, exist_ok=True)
    remotes.write_text(json.dumps({"b": {"url": "http://h:8789",
                                         "token": "tk"}}), encoding="utf-8")
    assert relay_transport.deliver_route(card, _msg()) == relay_transport.VIA_HTTP
    assert sent == {"url": "http://h:8789", "token": "tk"}
    assert list(directory.spool_dir("b").glob("*.json")) == []


def test_silent_remote_is_parked_in_its_spool_here(monkeypatch):
    """A remote that never answers may be a client-only peer that pulls this
    host's spool (measured 2026-10-03): park its mail there instead of dropping."""
    def boom(url, msg, token, **kw):
        raise OSError("connection refused")
    monkeypatch.setattr(relay_transport, "transport_post", boom)
    card = {"instance_id": "b", "spool": "", "url": "http://h:8789"}
    assert relay_transport.deliver_route(card, _msg()) == relay_transport.PARKED
    assert relay_transport.deliver(card, _msg()) is True
    parked = sorted(directory.spool_dir("b").glob("*.json"))
    assert len(parked) == 2
    assert json.loads(parked[0].read_text(encoding="utf-8")) == _msg()


def test_rejecting_remote_is_not_parked(monkeypatch):
    """A bridge that answered "no" is alive and misconfigured: surface it."""
    monkeypatch.setattr(relay_transport, "transport_post",
                        lambda url, msg, token, **kw: relay_transport.REJECTED)
    card = {"instance_id": "b", "spool": "", "url": "http://h:8789"}
    assert relay_transport.deliver(card, _msg()) is False
    assert list(directory.spool_dir("b").glob("*.json")) == []


def test_silent_remote_with_unwritable_spool_is_false(monkeypatch):
    monkeypatch.setattr(relay_transport, "transport_post",
                        lambda url, msg, token, **kw: relay_transport.UNREACHABLE)

    def no_disk(cid, msg):
        raise OSError("read-only file system")
    monkeypatch.setattr(relay_transport.spool, "deposit", no_disk)
    card = {"instance_id": "b", "spool": "", "url": "http://h:8789"}
    assert relay_transport.deliver_route(card, _msg()) == ""


def test_card_without_any_address_is_false():
    assert relay_transport.deliver({"instance_id": "b", "spool": "",
                                    "url": ""}, _msg()) is False


def test_invalid_id_is_refused_before_the_filesystem():
    assert relay_transport.deliver({"instance_id": "../../etc", "spool": "",
                                    "url": ""}, _msg()) is False


def test_sender_never_writes_in_recipient_db(tmp_path):
    """Criterion 5: B's db only changes when B ITSELF ingests the spool."""
    from conscio.liaison import mailbox, spool
    db_b = tmp_path / "b.db"
    mailbox.send(db_b, from_instance="b", to_instance="b", type="relay",
                 payload={"seed": 1})          # creates the file
    before = db_b.stat().st_mtime_ns, db_b.stat().st_size
    relay_transport.deliver({"instance_id": "b", "spool": "s", "url": ""}, _msg())
    assert (db_b.stat().st_mtime_ns, db_b.stat().st_size) == before
    assert spool.ingest(db_b, "b") == 1        # only now does the owner write
    assert db_b.stat().st_size >= before[1]


def test_save_remotes_keeps_the_token_private(tmp_path):
    """remotes.json holds bearer tokens: never world-readable."""
    import stat
    relay_transport.save_remotes({"b": {"url": "http://h:8789", "token": "tk"}})
    mode = stat.S_IMODE(relay_transport.remotes_path().stat().st_mode)
    assert mode == relay_transport.REMOTES_MODE
    assert relay_transport.load_remotes()["b"]["token"] == "tk"
