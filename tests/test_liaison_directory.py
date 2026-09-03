"""v4.5.4 C2 — directory of public peer cards."""
import json
import time

import pytest

from conscio.liaison import directory


@pytest.fixture(autouse=True)
def _root(tmp_path, monkeypatch):
    monkeypatch.setenv("CONSCIO_RELAY_ROOT", str(tmp_path / "relay"))
    yield tmp_path / "relay"


def _card(cid="agent-a", **kw):
    base = {"instance_id": cid, "spool": "", "url": "", "modelo": "m",
            "familia": "f", "runtime": "r", "papel": "executor",
            "capabilities": ["relay"], "updated_at": time.time()}
    base.update(kw)
    return base


def test_publish_then_peers_roundtrip():
    directory.publish(_card("agent-a"))
    directory.publish(_card("agent-b"))
    ids = {p["instance_id"] for p in directory.peers()}
    assert ids == {"agent-a", "agent-b"}


def test_peers_excludes_self():
    directory.publish(_card("agent-a"))
    directory.publish(_card("agent-b"))
    assert [p["instance_id"] for p in directory.peers(exclude="agent-a")] == ["agent-b"]


def test_peers_has_no_liveness_filter():
    """Peer parado ainda tem endereço: spool é arquivo, não sessão (C2)."""
    directory.publish(_card("old", updated_at=time.time() - 10 * 3600))
    assert [p["instance_id"] for p in directory.peers()] == ["old"]
    assert directory.is_live(directory.get("old")) is False


def test_traversal_id_is_rejected():
    assert directory.valid_id("../../etc") is False
    assert directory.valid_id("a b") is False
    assert directory.valid_id("6a46de9d-8722-411e-9548-a9785b266f48") is True
    with pytest.raises(ValueError):
        directory.spool_dir("../../etc")
    with pytest.raises(ValueError):
        directory.publish(_card("../evil"))


def test_publish_is_atomic_and_leaves_no_tmp():
    directory.publish(_card("agent-a"))
    files = sorted(p.name for p in (directory.relay_root() / "peers").iterdir())
    assert files == ["agent-a.json"]


def test_corrupt_card_is_skipped_not_fatal():
    directory.publish(_card("good"))
    (directory.relay_root() / "peers" / "bad.json").write_text("{not json",
                                                              encoding="utf-8")
    assert [p["instance_id"] for p in directory.peers()] == ["good"]


def test_forget_and_prune():
    directory.publish(_card("gone", updated_at=time.time() - 40 * 86400))
    directory.publish(_card("here"))
    assert directory.prune(max_age_days=30.0) == 1
    assert [p["instance_id"] for p in directory.peers()] == ["here"]
    assert directory.forget("here") is True
    assert directory.peers() == []


def test_card_file_is_valid_json_with_expected_keys():
    directory.publish(_card("agent-a", url="http://host:8789"))
    raw = json.loads((directory.relay_root() / "peers" / "agent-a.json")
                     .read_text(encoding="utf-8"))
    assert raw["url"] == "http://host:8789"
    assert raw["capabilities"] == ["relay"]
