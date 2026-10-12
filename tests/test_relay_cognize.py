"""v2.9.0 "Mind in the loop" — cognition-routed relay responder. A real liaison
mailbox on a tmp db + a SpyEngine that records read-trio calls and RAISES on any
mutator (the integrity boundary proof) + MockAdapter."""
import ast
import pathlib
import re

import pytest

from conscio.agency import relay_cognize
from conscio.agency.adapter import AdapterError, MockAdapter
from conscio.content_layer import ContentLayerManager
from conscio.content_store import ContentStore
from conscio.liaison import mailbox

# Slice 4 T3/T5 helpers: the case/whitespace-insensitive marker match. The
# marker carries the 16-hex content-derived id (optional id tail for the
# neutralized forgeries).
_MARKER = re.compile(
    r"\[\s*/?\s*UNTRUSTED\s*FOREIGN\s*DATA\s*(?:id\s*=[0-9a-f]{16})?\]",
    re.IGNORECASE)


def _outside_envelopes(text: str) -> str:
    """Parts of `text` OUTSIDE every untrusted-data envelope (open/close
    pairing, case/whitespace-insensitive markers)."""
    parts: list[str] = []
    depth = 0
    last = 0
    for m in _MARKER.finditer(text):
        if depth == 0:
            parts.append(text[last:m.start()])
        if "/" in m.group(0):
            last = m.end()
            depth = max(0, depth - 1)
        else:
            depth += 1
            last = m.end()
    if depth == 0:
        parts.append(text[last:])
    return "".join(parts)

PEER = "peer-1111"
OTHER = "stranger-9999"
ME = "me-0000"


def _db(tmp_path):
    return tmp_path / "liaison.db"


class StoreSpy:
    """Records content_store.index calls; optionally raises to test that a
    failing write never breaks the reply."""

    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def index(self, label, content, category, **kw):
        self.calls.append({"label": label, "content": content,
                           "category": category})
        if self.fail:
            raise RuntimeError("store down")
        return 1


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
        self.content_store = StoreSpy()

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
    assert "hello mind" in p                             # peer words in transcript
    assert re.search(r"\[UNTRUSTED FOREIGN DATA id=[0-9a-f]{16}\]", p)  # slice 4: peer line enveloped


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


def test_remember_false_does_not_write(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "hi"})
    spy = SpyEngine()
    relay_cognize.cognize_respond(spy, MockAdapter(script=["yo"]), db, ME,
                                  [PEER])                      # remember default
    assert spy.content_store.calls == []                      # no episodic write


def test_remember_true_writes_episodic(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "how is the cache?"})
    spy = SpyEngine()
    relay_cognize.cognize_respond(spy, MockAdapter(script=["cache is warm"]),
                                  db, ME, [PEER], remember=True)
    assert len(spy.content_store.calls) == 1
    c = spy.content_store.calls[0]
    assert c["category"] == "external"
    assert "how is the cache?" in c["content"]                # peer text
    assert "cache is warm" in c["content"]                    # own reply
    assert PEER[:12] in c["label"]


def test_remember_true_still_forbids_mutators(tmp_path):
    """F4: even when remembering, perceive/reflect/run are never called (they
    RAISE in SpyEngine). The run completes and writes only via content_store."""
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "ping"})
    spy = SpyEngine()
    relay_cognize.cognize_respond(spy, MockAdapter(script=["pong"]), db, ME,
                                  [PEER], remember=True)       # no AssertionError
    names = {c if isinstance(c, str) else c[0] for c in spy.calls}
    assert names <= {"get_state_for_injection", "recall", "advisory"}
    assert len(spy.content_store.calls) == 1


def test_remember_write_failure_does_not_break_reply(tmp_path):
    db = _db(tmp_path)
    mid = mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                       payload={"text": "ping"})
    spy = SpyEngine()
    spy.content_store = StoreSpy(fail=True)                    # store is down
    sent = relay_cognize.cognize_respond(spy, MockAdapter(script=["pong"]),
                                         db, ME, [PEER], remember=True)
    assert len(sent) == 1                                      # reply still sent
    r = mailbox.inbox(db, PEER, unread_only=True)[0]
    assert r["payload"]["text"] == "pong"
    assert r["payload"]["in_reply_to"] == mid


def test_remember_true_skips_consumed_auto_reply(tmp_path):
    """A loop-breaker auto_reply is consumed, never answered -> never remembered."""
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "auto", "auto_reply": True})
    spy = SpyEngine()
    relay_cognize.cognize_respond(spy, MockAdapter(script=["x"]), db, ME,
                                  [PEER], remember=True)
    assert spy.content_store.calls == []


def test_one_reply_per_peer_per_cycle(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "q1"})
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "q2"})
    a = MockAdapter(script=["one answer", "must not fire"])
    sent = relay_cognize.cognize_respond(SpyEngine(), a, db, ME, [PEER])
    assert len(sent) == 1 and len(a.calls) == 1


def test_burst_wider_than_thread_window_fully_covered(tmp_path):
    # same coverage contract as relay_respond: the one reply per peer must be
    # built over a window that includes EVERY row consumed as answered.
    db = _db(tmp_path)
    for i in range(30):
        mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                     payload={"text": f"burst-{i:02d}"}, ts=float(i))
    captured = {}
    a = MockAdapter(script=[lambda p: captured.setdefault("p", p) or "ok"])
    sent = relay_cognize.cognize_respond(SpyEngine(), a, db, ME, [PEER],
                                         limit=50)
    assert len(sent) == 1
    assert "burst-00" in captured["p"]         # oldest reached the adapter
    assert mailbox.inbox(db, ME, unread_only=True) == []   # all consumed


# ── Ato 1b: delta transcript cuts answered history in cognize_respond too ────

def test_delta_no_history_excludes_answered_turns(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "earlier question"}, ts=1.0)
    mailbox.send(db, from_instance=ME, to_instance=PEER, type="chat",
                 payload={"text": "earlier answer", "auto_reply": True},
                 ts=2.0)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "fresh question"}, ts=3.0)
    captured = {}
    a = MockAdapter(script=[lambda p: captured.setdefault("p", p) or "ok"])
    relay_cognize.cognize_respond(SpyEngine(), a, db, ME, [PEER], delta=True)
    p = captured["p"]
    assert "fresh question" in p                # delta turn present
    assert "earlier question" not in p          # answered history cut
    assert "earlier answer" not in p


def test_delta_default_off_preserves_history(tmp_path):
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "earlier question"}, ts=1.0)
    mailbox.send(db, from_instance=ME, to_instance=PEER, type="chat",
                 payload={"text": "earlier answer", "auto_reply": True},
                 ts=2.0)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "fresh question"}, ts=3.0)
    captured = {}
    a = MockAdapter(script=[lambda p: captured.setdefault("p", p) or "ok"])
    relay_cognize.cognize_respond(SpyEngine(), a, db, ME, [PEER])  # delta=False
    p = captured["p"]
    assert "earlier question" in p              # full history kept by default
    assert "fresh question" in p


# ── slice 4: peer lines enveloped in the cognize prompt too (T3) + the stored
#    exchange keeps the peer side enveloped, never raw (T5) ───────────────────

def test_cognize_respond_peer_text_only_inside_envelope(tmp_path):
    """T3 (cognize builder): peer words reach the prompt ONLY inside an
    envelope; the legit reply still goes out with the peer's words present."""
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "omega77 mind probe"}, ts=1.0)
    captured = {}
    a = MockAdapter(script=[lambda p: captured.setdefault("p", p) or "ok omega77"])
    sent = relay_cognize.cognize_respond(SpyEngine(), a, db, ME, [PEER])
    assert len(sent) == 1                       # legit: the reply went out
    p = captured["p"]
    assert "omega77 mind probe" in p            # peer words in the prompt
    assert "omega77 mind probe" not in _outside_envelopes(p)  # only inside one
    assert "source: relay:" + PEER[:12] in p


def test_remembered_exchange_envelops_the_peer_side(tmp_path):
    """T5 (measured real): the stored exchange indexes the peer side ALREADY
    enveloped, so engine.recall can hand the LM a marked envelope, not raw
    peer text. Our own `me:` reply stays plain (it is ours)."""
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "probe88 cache temperature"})
    spy = SpyEngine()
    relay_cognize.cognize_respond(spy, MockAdapter(script=["warm"]), db, ME,
                                  [PEER], remember=True)
    assert len(spy.content_store.calls) == 1
    content = spy.content_store.calls[0]["content"]
    assert "probe88 cache temperature" in content      # peer words stored
    assert "warm" in content                             # our reply stored
    assert re.search(r"\[UNTRUSTED FOREIGN DATA id=[0-9a-f]{16}\]", content)  # the envelope is present
    # the peer side is wrapped BEFORE our plain `me:` reply line:
    prefix, sep, rest = content.partition("\nme: ")
    assert sep, content
    assert rest == "warm"
    assert _MARKER.search(prefix) is not None           # an envelope closed it


def test_surrogate_peer_text_completes_and_stores_exchange(tmp_path):
    """B1 on the cognize path: a lone surrogate must not crash the responder.
    The reply goes out, and the exchange is stored with the body's surrogate
    dropped (the stored record is an enveloped body on its own line)."""
    db = _db(tmp_path)
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "a\ud800b"})
    spy = SpyEngine()
    sent = relay_cognize.cognize_respond(spy, MockAdapter(script=["ok"]), db, ME,
                                         [PEER], remember=True)
    assert len(sent) == 1
    assert mailbox.inbox(db, ME, unread_only=True) == []    # inbound consumed
    assert len(spy.content_store.calls) == 1
    stored = spy.content_store.calls[0]
    assert stored["category"] == "external"
    assert "ab" in stored["content"].split("\n")


# ── slice 4 B4-3: recall's 300-character cut (known limitation) ──────────────

class _RecallOverStore(SpyEngine):
    """SpyEngine whose recall is the REAL ContentLayerManager.recall over a real
    ContentStore (FTS5), so the 300-character cut is the one production applies.
    The identity and advisory reads stay canned, as in SpyEngine."""

    def __init__(self, store):
        super().__init__()
        self.content_store = store
        self._layers = ContentLayerManager(content_store=store, world_model=None)

    def recall(self, query, k=3, categories=None):
        self.calls.append(("recall", query, k))
        return self._layers.recall(query, k, categories)


def _memory_section(prompt: str) -> str:
    """The 'Relevant memory' block: from its heading to the next blank line."""
    head = "Relevant memory:\n"
    assert head in prompt, prompt
    return prompt.split(head, 1)[1].split("\n\n", 1)[0]


@pytest.mark.xfail(strict=True, reason=(
    "pre-existing: ContentLayerManager.recall cuts each snippet at 300 characters "
    "(content_layer.py:164) and a stored envelope is longer, so its close marker "
    "and guidance are lost in the cognize prompt; fix in a later slice"))
def test_recalled_envelope_keeps_its_close_marker_in_the_cognize_prompt(tmp_path):
    db = _db(tmp_path)
    engine = _RecallOverStore(ContentStore(db_path=tmp_path / "ctx.db"))
    long_text = "omega99 " + "a remembered detail about the cache warm-up. " * 8
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": long_text}, ts=1.0)
    relay_cognize.cognize_respond(engine, MockAdapter(script=["noted"]), db, ME,
                                  [PEER], remember=True)          # stores the exchange
    mailbox.send(db, from_instance=PEER, to_instance=ME, type="chat",
                 payload={"text": "omega99"}, ts=2.0)
    captured = {}
    adapter = MockAdapter(script=[lambda p: captured.setdefault("p", p) or "ok"])
    relay_cognize.cognize_respond(engine, adapter, db, ME, [PEER])  # recalls it
    memory = _memory_section(captured["p"])
    opens = re.findall(r"\[UNTRUSTED FOREIGN DATA id=([0-9a-f]{16})\]", memory)
    assert opens, "the stored exchange was not recalled into the prompt"
    for eid in opens:
        assert f"[/UNTRUSTED FOREIGN DATA id={eid}]" in memory, (
            "a recalled envelope lost its close marker at the 300-character cut")
