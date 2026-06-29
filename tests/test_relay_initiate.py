"""v2.10.0 "Initiative" — proactive cognition initiator. Real liaison mailbox on a
tmp db + a SpyEngine whose read-trio is recorded and whose every mutator RAISES
(the read-only boundary proof) + MockAdapter."""
import ast
import pathlib

from conscio.agency import relay_initiate
from conscio.agency.adapter import MockAdapter
from conscio.liaison import mailbox

PEER = "peer-1111"
PEER2 = "peer-2222"
ME = "me-0000"


def _db(tmp_path):
    return tmp_path / "liaison.db"


class StoreSpy:
    def __init__(self):
        self.calls = []

    def index(self, *a, **k):
        self.calls.append((a, k))
        return 1


class SpyEngine:
    def __init__(self, *, lockdown=False, brake=None, advisory_raises=False,
                 injection="I am Test, a calm agent.", recalls=("past chat",)):
        self._lockdown = lockdown
        self._brake = brake
        self._advisory_raises = advisory_raises
        self._injection = injection
        self._recalls = list(recalls)
        self.calls = []
        self.content_store = StoreSpy()

    def advisory(self):
        self.calls.append("advisory")
        if self._advisory_raises:
            raise RuntimeError("advisory down")
        return {"coherence": {"score": 0.8, "dominant": "stable"},
                "goals": [],
                "status": {"action_lockdown": self._lockdown,
                           "brake": self._brake}}

    def get_state_for_injection(self):
        self.calls.append("inject")
        return self._injection

    def recall(self, query, k=3, categories=None):
        self.calls.append(("recall", query, k))
        return list(self._recalls)

    def perceive(self, *a, **k):
        raise AssertionError("BOUNDARY: perceive called")

    def reflect(self, *a, **k):
        raise AssertionError("BOUNDARY: reflect called")

    def run(self, *a, **k):
        raise AssertionError("BOUNDARY: run called")

    def remember(self, *a, **k):
        raise AssertionError("BOUNDARY: remember called")


# ---- no-op guards -----------------------------------------------------------
def test_noop_without_engine(tmp_path):
    assert relay_initiate.initiate(None, MockAdapter(script=["x"]),
                                   _db(tmp_path), ME, [PEER]) == []


def test_noop_without_adapter(tmp_path):
    assert relay_initiate.initiate(SpyEngine(), None, _db(tmp_path), ME,
                                   [PEER]) == []


def test_noop_without_peers(tmp_path):
    assert relay_initiate.initiate(SpyEngine(), MockAdapter(script=["x"]),
                                   _db(tmp_path), ME, []) == []


# ---- stage-1 gates (gate 6 / fail-closed) -----------------------------------
def test_lockdown_suppresses(tmp_path):
    a = MockAdapter(script=["should not fire"])
    sent = relay_initiate.initiate(SpyEngine(lockdown=True), a, _db(tmp_path),
                                   ME, [PEER])
    assert sent == [] and a.calls == []


def test_brake_suppresses(tmp_path):
    a = MockAdapter(script=["nope"])
    sent = relay_initiate.initiate(SpyEngine(brake="failure-rate brake"), a,
                                   _db(tmp_path), ME, [PEER])
    assert sent == [] and a.calls == []


def test_advisory_failure_suppresses_fail_closed(tmp_path):
    a = MockAdapter(script=["nope"])
    sent = relay_initiate.initiate(SpyEngine(advisory_raises=True), a,
                                   _db(tmp_path), ME, [PEER])
    assert sent == [] and a.calls == []


# ---- directed ---------------------------------------------------------------
def test_directed_salient_sends_opener(tmp_path):
    db = _db(tmp_path)
    a = MockAdapter(script=["hey, the deploy finished"])
    sent = relay_initiate.initiate(SpyEngine(), a, db, ME, [PEER])
    assert len(sent) == 1 and sent[0]["to"] == PEER
    assert sent[0]["mode"] == "directed"
    box = mailbox.inbox(db, PEER, unread_only=True)
    assert len(box) == 1
    assert box[0]["type"] == "chat"
    assert box[0]["payload"]["text"] == "hey, the deploy finished"
    assert box[0]["payload"]["initiated"] is True
    assert "auto_reply" not in box[0]["payload"]


def test_directed_nothing_suppresses(tmp_path):
    db = _db(tmp_path)
    sent = relay_initiate.initiate(SpyEngine(), MockAdapter(script=["NOTHING"]),
                                   db, ME, [PEER])
    assert sent == []
    assert mailbox.inbox(db, PEER, unread_only=True) == []


def test_directed_empty_suppresses(tmp_path):
    db = _db(tmp_path)
    sent = relay_initiate.initiate(SpyEngine(), MockAdapter(script=["   "]),
                                   db, ME, [PEER])
    assert sent == []


def test_directed_no_storm_last_msg_mine_skips(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=ME, to_instance=PEER, type="chat",
                 payload={"text": "earlier opener", "initiated": True})
    a = MockAdapter(script=["should not fire"])
    sent = relay_initiate.initiate(SpyEngine(), a, db, ME, [PEER])
    assert sent == [] and a.calls == []                 # awaiting their reply


def test_directed_eligible_when_last_msg_peer(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "yo"})
    sent = relay_initiate.initiate(SpyEngine(), MockAdapter(script=["reply!"]),
                                   db, ME, [PEER])
    assert len(sent) == 1


def test_directed_max_reply_chars_truncates(tmp_path):
    db = _db(tmp_path)
    relay_initiate.initiate(SpyEngine(), MockAdapter(script=["z" * 5000]), db,
                            ME, [PEER], max_reply_chars=2000)
    box = mailbox.inbox(db, PEER, unread_only=True)
    assert len(box[0]["payload"]["text"]) == 2000


def test_directed_recall_query_is_last_peer_text(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "about the cache"})
    spy = SpyEngine()
    relay_initiate.initiate(spy, MockAdapter(script=["ok"]), db, ME, [PEER])
    recalls = [c for c in spy.calls if isinstance(c, tuple) and c[0] == "recall"]
    assert recalls and recalls[0][1] == "about the cache"


# ---- broadcast --------------------------------------------------------------
def test_broadcast_salient_fans_out(tmp_path):
    db = _db(tmp_path)
    a = MockAdapter(script=["shipping v2.10 tonight"])
    sent = relay_initiate.initiate(SpyEngine(), a, db, ME, [PEER, PEER2],
                                   broadcast=True)
    assert {s["to"] for s in sent} == {PEER, PEER2}
    assert all(s["mode"] == "broadcast" for s in sent)
    for p in (PEER, PEER2):
        box = mailbox.inbox(db, p, unread_only=True)
        assert box[0]["payload"]["broadcast"] is True
        assert box[0]["payload"]["initiated"] is True


def test_broadcast_nothing_suppresses(tmp_path):
    db = _db(tmp_path)
    sent = relay_initiate.initiate(SpyEngine(), MockAdapter(script=["NOTHING"]),
                                   db, ME, [PEER, PEER2], broadcast=True)
    assert sent == []


def test_broadcast_first_time_allowed(tmp_path):
    db = _db(tmp_path)
    sent = relay_initiate.initiate(SpyEngine(), MockAdapter(script=["hello all"]),
                                   db, ME, [PEER], broadcast=True)
    assert len(sent) == 1


def test_broadcast_outstanding_guard_blocks(tmp_path):
    db = _db(tmp_path)
    # a prior broadcast with NO peer engagement since
    mailbox.send(db, from_instance=ME, to_instance=PEER, type="chat",
                 payload={"text": "old", "broadcast": True}, ts=100.0)
    a = MockAdapter(script=["should not fire"])
    sent = relay_initiate.initiate(SpyEngine(), a, db, ME, [PEER],
                                   broadcast=True)
    assert sent == [] and a.calls == []


def test_broadcast_outstanding_guard_allows_after_engagement(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=ME, to_instance=PEER, type="chat",
                 payload={"text": "old", "broadcast": True}, ts=100.0)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "a peer replied"}, ts=200.0)   # engagement
    sent = relay_initiate.initiate(SpyEngine(), MockAdapter(script=["again!"]),
                                   db, ME, [PEER], broadcast=True)
    assert len(sent) == 1


def test_broadcast_outstanding_guard_counts_read_engagement(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=ME, to_instance=PEER, type="chat",
                 payload={"text": "old", "broadcast": True}, ts=100.0)
    rid = mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                       payload={"text": "replied then read"}, ts=200.0)
    mailbox.mark_read(db, [rid])                         # reactive responder ate it
    sent = relay_initiate.initiate(SpyEngine(), MockAdapter(script=["again!"]),
                                   db, ME, [PEER], broadcast=True)
    assert len(sent) == 1                                # read engagement counts


# ---- integrity boundary -----------------------------------------------------
def test_integrity_boundary_only_read_trio_no_store_write(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "hi"})
    spy = SpyEngine()
    relay_initiate.initiate(spy, MockAdapter(script=["pong"]), db, ME, [PEER])
    names = {c if isinstance(c, str) else c[0] for c in spy.calls}
    assert names <= {"advisory", "inject", "recall"}
    assert spy.content_store.calls == []                 # no episodic write


# ---- adapter failure --------------------------------------------------------
def test_adapter_failure_directed_skips(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "hi"})
    sent = relay_initiate.initiate(SpyEngine(), MockAdapter(script=[]), db, ME,
                                   [PEER])              # exhausted -> AdapterError
    assert sent == []


def test_adapter_failure_broadcast_returns_empty(tmp_path):
    db = _db(tmp_path)
    sent = relay_initiate.initiate(SpyEngine(), MockAdapter(script=[]), db, ME,
                                   [PEER], broadcast=True)
    assert sent == []


# ---- import shape -----------------------------------------------------------
def test_no_engine_import_in_module_source():
    src = pathlib.Path(relay_initiate.__file__).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert not mod.startswith("conscio.engine"), mod
            assert "engine" not in mod.split("."), mod
        if isinstance(node, ast.Import):
            for n in node.names:
                assert "engine" not in n.name.split("."), n.name
