# tests/test_liaison_reactor.py
"""Tests for conscio.liaison.reactor — the agnostic reactive dispatcher.

The reactor is the "delegate every inbound message to the agent" layer:
it reads new messages past the watcher cursor, runs a notify hook (a
subprocess command configured by the environment) for each non-silent one,
and ONLY advances the cursor past messages whose hook succeeded (at-least-
once — a failed hook retries next tick). Universal across agent
environments: `CONSCIO_NOTIFY_CMD` points at whatever wakes YOUR agent.
"""
import json

from conscio.liaison import mailbox, reactor


def _bind(tmp_path):
    return tmp_path / "liaison.db"


class TestShouldNotify:
    def test_default_notifies(self):
        assert reactor.should_notify({"payload": {"text": "oi"}}) is True

    def test_silent_meta_opts_out(self):
        msg = {"payload": {"text": "oi", "_meta": {"from": {}, "silent": True}}}
        assert reactor.should_notify(msg) is False

    def test_silent_at_payload_top_level(self):
        assert reactor.should_notify({"payload": {"silent": True}}) is False

    def test_silent_false_still_notifies(self):
        assert reactor.should_notify(
            {"payload": {"silent": False, "text": "oi"}}) is True

    def test_missing_payload_notifies(self):
        assert reactor.should_notify({}) is True


class TestRunNotifyHook:
    def test_runs_command_with_message_stdin(self, tmp_path):
        out = tmp_path / "got.json"
        r = reactor.run_notify_hook(f"cat > {out}", {
            "id": 1, "from_instance": "peer", "payload": {"text": "oi"}})
        assert r is True
        assert json.loads(out.read_text())["id"] == 1

    def test_failing_hook_returns_false_no_raise(self):
        assert reactor.run_notify_hook("exit 7", {"id": 1}) is False

    def test_missing_command_returns_false(self):
        assert reactor.run_notify_hook("", {"id": 1}) is False

    def test_hook_timeout_no_hang(self):
        assert reactor.run_notify_hook("sleep 5", {"id": 1}, timeout=1) is False


class TestDispatch:
    def _cursor(self, db, peer):
        return int(reactor._load_state(db).get(peer, {}).get("last_seen_id", 0))

    def test_dispatch_notifies_and_advances(self, tmp_path):
        db = _bind(tmp_path)
        self_id, peer = "self", "peer-a"
        mailbox.send(db, from_instance=peer, to_instance=self_id,
                     type="chat", payload={"text": "oi"})
        calls = []
        n = reactor.dispatch(db, self_id=self_id, peers=[peer],
                             notify_cmd="cat",
                             _notify=lambda cmd, m: (calls.append(m) or True))
        assert n == 1
        assert len(calls) == 1
        assert calls[0]["payload"]["text"] == "oi"
        # cursor avançou → próximo tick não re-entrega
        assert self._cursor(db, peer) > 0

    def test_failed_hook_does_not_advance(self, tmp_path):
        db = _bind(tmp_path)
        self_id, peer = "self", "peer-b"
        mailbox.send(db, from_instance=peer, to_instance=self_id,
                     type="chat", payload={"text": "fica"})
        # hook real falha (exit 7) → 0 entregues, cursor NÃO avança
        n = reactor.dispatch(db, self_id=self_id, peers=[peer],
                             notify_cmd="exit 7")
        assert n == 0
        assert self._cursor(db, peer) == 0

    def test_dispatch_skips_silent(self, tmp_path):
        db = _bind(tmp_path)
        self_id, peer = "self", "peer-c"
        mailbox.send(db, from_instance=peer, to_instance=self_id,
                     type="chat",
                     payload={"text": "sigiloso", "_meta": {"silent": True}})
        calls = []
        n = reactor.dispatch(db, self_id=self_id, peers=[peer],
                             notify_cmd="cat",
                             _notify=lambda cmd, m: (calls.append(m) or True))
        assert n == 1            # "consumida" (cursor avança) mesmo sem notificar
        assert calls == []       # o hook NÃO rodou
        assert self._cursor(db, peer) > 0

    def test_dispatch_ignores_own_messages(self, tmp_path):
        db = _bind(tmp_path)
        self_id, peer = "self", "peer-d"
        mailbox.send(db, from_instance=self_id, to_instance=peer,
                     type="chat", payload={"text": "minha"})
        calls = []
        n = reactor.dispatch(db, self_id=self_id, peers=[peer],
                             notify_cmd="cat",
                             _notify=lambda cmd, m: (calls.append(m) or True))
        assert n == 0
        assert calls == []
        assert self._cursor(db, peer) == 0


class TestNeverRaises:
    def test_dispatch_missing_db(self, tmp_path):
        db = tmp_path / "nope.db"
        assert reactor.dispatch(db, self_id="s", peers=["p"],
                                notify_cmd="echo hi") == 0

    def test_dispatch_no_peers(self, tmp_path):
        db = _bind(tmp_path)
        assert reactor.dispatch(db, self_id="s", peers=[], notify_cmd="x") == 0

    def test_dispatch_no_self(self, tmp_path):
        db = _bind(tmp_path)
        assert reactor.dispatch(db, self_id="", peers=["p"],
                                notify_cmd="x") == 0