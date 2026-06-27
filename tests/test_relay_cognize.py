"""v2.9.0 "Mind in the loop" — cognition-routed relay responder. A real liaison
mailbox on a tmp db + a SpyEngine that records read-trio calls and RAISES on any
mutator (the integrity boundary proof) + MockAdapter."""
import ast
import pathlib

from conscio.agency import relay_cognize
from conscio.agency.adapter import AdapterError, MockAdapter
from conscio.liaison import mailbox

PEER = "peer-1111"
OTHER = "stranger-9999"
ME = "me-0000"


def _db(tmp_path):
    return tmp_path / "liaison.db"


class SpyEngine:
    """Engine double: read-trio returns canned data and is recorded; every
    mutator RAISES — so a single accidental mutator call fails the run."""

    def __init__(self, *, injection="I am Test, a calm agent.",
                 recalls=("we discussed deploys before",),
                 advisory=None):
        self._injection = injection
        self._recalls = list(recalls)
        self._advisory = advisory if advisory is not None else {
            "coherence": {"score": 0.82, "dominant": "stable"},
            "goals": [{"description": "ship", "origin": "x",
                       "executable": True}],
            "status": {"action_lockdown": False, "brake": None}}
        self.calls = []

    def get_state_for_injection(self):
        self.calls.append("get_state_for_injection")
        return self._injection

    def recall(self, query, k=3, categories=None):
        self.calls.append(("recall", query, k))
        return list(self._recalls)

    def advisory(self):
        self.calls.append("advisory")
        return dict(self._advisory)

    def perceive(self, *a, **k):
        raise AssertionError("BOUNDARY: perceive called")

    def reflect(self, *a, **k):
        raise AssertionError("BOUNDARY: reflect called")

    def run(self, *a, **k):
        raise AssertionError("BOUNDARY: run called")

    def remember(self, *a, **k):
        raise AssertionError("BOUNDARY: remember called")


def test_noop_without_engine(tmp_path):
    a = MockAdapter(script=["hi"])
    assert relay_cognize.cognize_respond(None, a, _db(tmp_path), ME, [PEER]) == []
    assert a.calls == []


def test_noop_without_adapter(tmp_path):
    assert relay_cognize.cognize_respond(SpyEngine(), None, _db(tmp_path), ME,
                                         [PEER]) == []


def test_noop_without_peers(tmp_path):
    a = MockAdapter(script=["hi"])
    assert relay_cognize.cognize_respond(SpyEngine(), a, _db(tmp_path), ME,
                                         []) == []
    assert a.calls == []


def test_one_chat_one_reply_type_echoed(tmp_path):
    db = _db(tmp_path)
    mid = mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                       payload={"text": "how's the deploy going?"})
    a = MockAdapter(script=["going well, shipping soon"])
    sent = relay_cognize.cognize_respond(SpyEngine(), a, db, ME, [PEER])
    assert len(sent) == 1
    assert sent[0]["to"] == PEER and sent[0]["in_reply_to"] == mid
    box = mailbox.inbox(db, PEER, unread_only=True)
    assert len(box) == 1
    r = box[0]
    assert r["type"] == "chat"                          # type echoed
    assert r["payload"]["text"] == "going well, shipping soon"
    assert r["payload"]["auto_reply"] is True
    assert r["payload"]["in_reply_to"] == mid
    assert mailbox.inbox(db, ME, unread_only=True) == []   # inbound consumed


def test_integrity_boundary_only_read_trio_called(tmp_path):
    """B1: across a real run, ONLY the read-trio fires; mutators raise."""
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "ping"})
    spy = SpyEngine()
    a = MockAdapter(script=["pong"])
    relay_cognize.cognize_respond(spy, a, db, ME, [PEER])   # no AssertionError
    names = {c if isinstance(c, str) else c[0] for c in spy.calls}
    assert names <= {"get_state_for_injection", "recall", "advisory"}
    assert "get_state_for_injection" in names
    assert "recall" in names
    assert "advisory" in names


def test_recall_query_is_peer_text(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "tell me about the cache"})
    spy = SpyEngine()
    relay_cognize.cognize_respond(spy, MockAdapter(script=["sure"]), db, ME,
                                  [PEER])
    recalls = [c for c in spy.calls if isinstance(c, tuple) and c[0] == "recall"]
    assert recalls and recalls[0][1] == "tell me about the cache"


def test_prompt_includes_identity_memory_advisory_and_transcript(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "hello mind"})
    captured = {}

    def _gen(p):
        captured["p"] = p
        return "hi"

    a = MockAdapter(script=[_gen])
    relay_cognize.cognize_respond(SpyEngine(), a, db, ME, [PEER])
    p = captured["p"]
    assert "I am Test, a calm agent." in p              # identity
    assert "we discussed deploys before" in p           # recalled memory
    assert "coherence=0.82" in p                        # advisory signal
    assert "peer: hello mind" in p                      # transcript


def test_max_reply_chars_truncates_before_fit(tmp_path):
    db = _db(tmp_path)
    mid = mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                       payload={"text": "go on"})
    a = MockAdapter(script=["x" * 5000])
    relay_cognize.cognize_respond(SpyEngine(), a, db, ME, [PEER],
                                  max_reply_chars=2000)
    r = mailbox.inbox(db, PEER, unread_only=True)[0]
    assert len(r["payload"]["text"]) == 2000
    _ = mid


def test_loop_breaker_consumes_own_auto_reply_unanswered(tmp_path):
    db = _db(tmp_path)
    aid = mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                       payload={"text": "auto", "auto_reply": True})
    a = MockAdapter(script=["should-not-fire"])
    sent = relay_cognize.cognize_respond(SpyEngine(), a, db, ME, [PEER])
    assert sent == []
    assert a.calls == []                                # never generated
    assert mailbox.inbox(db, ME, unread_only=True) == []   # consumed
    assert mailbox.inbox(db, PEER, unread_only=True) == []  # no reply sent
    _ = aid


def test_reserved_type_ignored(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="review_request",
                 payload={"text": "review me"})
    a = MockAdapter(script=["nope"])
    sent = relay_cognize.cognize_respond(SpyEngine(), a, db, ME, [PEER])
    assert sent == []
    assert a.calls == []


def test_stranger_ignored(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=OTHER, to_instance=ME, type="chat",
                 payload={"text": "hi from nowhere"})
    a = MockAdapter(script=["nope"])
    sent = relay_cognize.cognize_respond(SpyEngine(), a, db, ME, [PEER])
    assert sent == []
    assert a.calls == []


def test_adapter_failure_leaves_unread(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "ping"})
    a = MockAdapter(script=[])                          # exhausted -> AdapterError
    sent = relay_cognize.cognize_respond(SpyEngine(), a, db, ME, [PEER])
    assert sent == []
    assert len(mailbox.inbox(db, ME, unread_only=True)) == 1   # still unread
    _ = AdapterError


def test_no_engine_import_in_module_source():
    """Import-shape: relay_cognize uses the engine via its passed-in argument,
    it does NOT import conscio.engine (no hard coupling to the engine module)."""
    src = pathlib.Path(relay_cognize.__file__).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert not mod.startswith("conscio.engine"), mod
            assert "engine" not in mod.split("."), mod
        if isinstance(node, ast.Import):
            for n in node.names:
                assert "engine" not in n.name.split("."), n.name
