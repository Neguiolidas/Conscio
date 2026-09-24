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


# ── v4.7.2: dormancy, orphan spools, prune never unpairs ─────────────────────

def test_dormant_for_only_local_cards_silent_past_threshold():
    now = time.time()
    fresh = _card("fresh", spool="/s/fresh", updated_at=now - 3600)
    old = _card("old", spool="/s/old", updated_at=now - 4 * 86400)
    remote = _card("far", spool="", url="http://10.0.0.2:8789",
                   updated_at=now - 90 * 86400)
    assert directory.dormant_for(fresh, now) is None
    assert directory.dormant_for(old, now) == pytest.approx(4 * 86400)
    assert directory.dormant_for(remote, now) is None   # age = pairing time
    assert directory.dormant_for(None, now) is None     # unknown, not dormant


def test_prune_never_collects_a_paired_remote():
    """Its updated_at is the pairing time: collecting it would silently
    unpair a live machine 30 days after `relay pair`."""
    directory.publish(_card("far", spool="", url="http://10.0.0.2:8789",
                            updated_at=time.time() - 90 * 86400))
    assert directory.prune(max_age_days=30.0) == 0
    assert [p["instance_id"] for p in directory.peers()] == ["far"]


def test_orphan_spools_lists_only_cardless_spools_with_mail(_root):
    spool = _root / "spool"
    for cid, n in (("orphan", 2), ("owned", 3), ("empty-orphan", 0)):
        (spool / cid).mkdir(parents=True)
        for i in range(n):
            (spool / cid / f"{i}.json").write_text("{}", encoding="utf-8")
    directory.publish(_card("owned"))
    (spool / "alias").symlink_to(spool / "orphan")     # counted under its target
    assert directory.orphan_spools() == [("orphan", 2)]


def test_orphan_spools_without_spool_dir_is_empty():
    assert directory.orphan_spools() == []
