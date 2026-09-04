# tests/test_liaison_reactor.py
"""Tests for conscio.liaison.reactor — the agnostic reactive dispatcher.

The reactor is the "delegate every inbound message to the agent" layer:
it reads new inbound messages, runs a notify hook (a subprocess command
configured by the environment) for EVERY one of them, and ONLY marks a
message read once its hook succeeded (at-least-once — a failed hook retries
next tick). Universal across agent environments: `CONSCIO_NOTIFY_CMD` points
at whatever wakes YOUR agent.
"""
import json
import time

from conscio.liaison import agents, directory, mailbox, reactor


def _bind(tmp_path):
    return tmp_path / "liaison.db"


class TestNoSilentMode:
    """The opt-out is gone: a consumed-but-never-surfaced message is
    indistinguishable from a lost one."""

    def test_the_opt_out_helper_no_longer_exists(self):
        assert not hasattr(reactor, "should_notify")
        assert not hasattr(reactor, "SILENT_KEYS")


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
    def _unread(self, db, self_id="self"):
        return len(mailbox.inbox(db, self_id, unread_only=True))

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
        # marcada como lida → próximo tick não re-entrega
        assert self._unread(db) == 0

    def test_failed_hook_does_not_advance(self, tmp_path):
        db = _bind(tmp_path)
        self_id, peer = "self", "peer-b"
        mailbox.send(db, from_instance=peer, to_instance=self_id,
                     type="chat", payload={"text": "fica"})
        # hook real falha (exit 7) → 0 entregues, cursor NÃO avança
        n = reactor.dispatch(db, self_id=self_id, peers=[peer],
                             notify_cmd="exit 7")
        assert n == 0
        assert self._unread(db) == 1

    def test_dispatch_notifies_a_message_marked_silent(self, tmp_path):
        """The old opt-out key must no longer buy silence."""
        db = _bind(tmp_path)
        self_id, peer = "self", "peer-c"
        mailbox.send(db, from_instance=peer, to_instance=self_id,
                     type="chat",
                     payload={"text": "sigiloso", "_meta": {"silent": True}})
        calls = []
        n = reactor.dispatch(db, self_id=self_id, peers=[peer],
                             notify_cmd="cat",
                             _notify=lambda cmd, m: (calls.append(m) or True))
        assert n == 1
        assert len(calls) == 1   # the hook DID run
        assert self._unread(db) == 0

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
        assert self._unread(db) == 0     # nada endereçado a mim


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

# ── v4.5.4 Task 8: read_ts é a contabilidade única; thread em sessão ──────

def test_dispatch_marks_read_ts(tmp_path, monkeypatch):
    db = tmp_path / "r.db"
    mailbox.send(db, from_instance="b", to_instance="me", type="relay",
                 payload={"x": 1})
    monkeypatch.setattr(reactor, "run_notify_hook", lambda *a, **k: True)
    assert reactor.dispatch(db, self_id="me", peers=["b"], notify_cmd="true") == 1
    rows = mailbox.inbox(db, "me", unread_only=False)
    assert rows[0]["read_ts"] is not None
    # segunda passada não reentrega (A8)
    assert reactor.dispatch(db, self_id="me", peers=["b"], notify_cmd="true") == 0


def test_failed_notify_does_not_mark_read(tmp_path, monkeypatch):
    db = tmp_path / "r.db"
    mailbox.send(db, from_instance="b", to_instance="me", type="relay",
                 payload={})
    monkeypatch.setattr(reactor, "run_notify_hook", lambda *a, **k: False)
    reactor.dispatch(db, self_id="me", peers=["b"], notify_cmd="true")
    assert mailbox.inbox(db, "me", unread_only=True) != []


def test_dispatch_ingests_the_spool(tmp_path, monkeypatch):
    """O db do agente novo nem existe: quem o cria é o spool (A8/C5)."""
    from conscio.liaison import directory, spool
    monkeypatch.setenv(directory.RELAY_ROOT_ENV, str(tmp_path / "relay"))
    db = tmp_path / "fresh.db"
    spool.deposit("me", {"from": "b", "to": "me", "type": "relay",
                         "payload": {"z": 9}})
    got = []
    n = reactor.dispatch(db, self_id="me", peers=[], notify_cmd="true",
                         _notify=lambda cmd, m: (got.append(m) or True))
    assert n == 1
    assert got[0]["payload"]["z"] == 9


def test_empty_peers_dispatches_everything(tmp_path):
    """A1 no reactor: sem allowlist não é sem entrega."""
    db = tmp_path / "r.db"
    mailbox.send(db, from_instance="whoever", to_instance="me", type="relay",
                 payload={"x": 1})
    got = []
    n = reactor.dispatch(db, self_id="me", peers=[], notify_cmd="true",
                         _notify=lambda cmd, m: (got.append(m) or True))
    assert (n, len(got)) == (1, 1)


def test_reactor_thread_survives_exception(tmp_path, monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise RuntimeError("db locked")

    monkeypatch.setattr(reactor, "dispatch", boom)
    th = reactor.ReactorThread(tmp_path / "x.db", "me", "true", interval=0.01)
    th.start()
    time.sleep(0.3)
    th.stop()
    assert len(calls) >= 2                  # não morreu na primeira exceção
    assert "db locked" in th.last_error     # I8: erro visível, não engolido


def test_reactor_thread_ticks_and_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(reactor, "dispatch", lambda *a, **k: 0)
    th = reactor.ReactorThread(tmp_path / "x.db", "me", "true", interval=0.01)
    th.start()
    time.sleep(0.1)
    th.stop()
    assert th.ticks >= 1
    assert th.last_error == ""
    assert not th.is_alive()                # stop() realmente encerra


def test_reactor_has_no_idle_shutdown():
    """R1: ocioso é o estado normal de um canal A2A, não condição de término."""
    import inspect
    src = inspect.getsource(reactor)
    assert "INACTIVITY" not in src.upper()
    assert "sys.exit(2)" not in src


def test_no_second_bookkeeping_survives():
    """R2: cursor por peer + read_ts seriam duas contabilidades da mesma coisa
    (classe do bug A9). read_ts é a única — é a que o inbox também enxerga."""
    import inspect
    src = inspect.getsource(reactor)
    for dead in ("_load_state", "_save_state", "last_seen_id"):
        assert dead not in src


# ── single-reactor lock (v4.5.4): two reactors notified everything twice ──

def test_second_reactor_is_refused_the_lock(tmp_path):
    from conscio.liaison import reactor
    db = tmp_path / "liaison.db"
    first = reactor.acquire_lock(db, "agent-a")
    assert first is not None
    assert reactor.acquire_lock(db, "agent-a") is None   # would double-notify
    reactor.release_lock(first)


def test_lock_is_inherited_once_the_holder_lets_go(tmp_path):
    """A dropped service must not leave the mailbox unattended."""
    from conscio.liaison import reactor
    db = tmp_path / "liaison.db"
    held = reactor.acquire_lock(db, "agent-a")
    reactor.release_lock(held)
    second = reactor.acquire_lock(db, "agent-a")
    assert second is not None
    reactor.release_lock(second)


def test_lock_is_per_agent_not_per_mailbox_file(tmp_path):
    from conscio.liaison import reactor
    db = tmp_path / "liaison.db"
    a = reactor.acquire_lock(db, "agent-a")
    b = reactor.acquire_lock(db, "agent-b")
    assert a is not None and b is not None      # different agents, no contention
    reactor.release_lock(a)
    reactor.release_lock(b)


def test_thread_idles_while_another_reactor_holds_the_lock(tmp_path):
    """The in-session thread must not race a running service."""
    from conscio.liaison import reactor
    db = tmp_path / "liaison.db"
    outsider = reactor.acquire_lock(db, "agent-a")
    calls = []
    th = reactor.ReactorThread(db, "agent-a", notify_cmd="true", interval=0.01)
    monkey = lambda *a, **k: calls.append(1)
    orig, reactor.dispatch = reactor.dispatch, monkey
    try:
        th.start()
        time.sleep(0.15)
        th.stop()
        assert calls == []                          # idled, did not dispatch
    finally:
        reactor.dispatch = orig
        reactor.release_lock(outsider)


class TestReactorPublishesItsCard:
    """An agent whose only persistent process is the reactor must still be
    findable: it reads the directory, so it has to appear in it too."""

    def _root(self, tmp_path, monkeypatch):
        monkeypatch.setenv(directory.RELAY_ROOT_ENV, str(tmp_path / "relay"))

    def test_tick_publishes_a_card_for_an_agent_with_no_mcp_server(
            self, tmp_path, monkeypatch):
        self._root(tmp_path, monkeypatch)
        db = tmp_path / "liaison.db"
        me = "agent-solo"
        assert directory.get(me) is None          # invisible before the tick
        reactor.dispatch(db, self_id=me, peers=[], notify_cmd="true")
        card = directory.get(me)
        assert card is not None
        assert card["instance_id"] == me
        assert card["spool"] == str(directory.spool_dir(me))

    def test_card_carries_the_identity_the_agent_registered(
            self, tmp_path, monkeypatch):
        self._root(tmp_path, monkeypatch)
        db = tmp_path / "liaison.db"
        me = "agent-ident"
        agents.register_agent(db, instance_id=me, model="opus",
                              familia="claude", runtime="claude-code",
                              papel="executor")
        reactor.dispatch(db, self_id=me, peers=[], notify_cmd="true")
        card = directory.get(me)
        assert (card["modelo"], card["familia"]) == ("opus", "claude")
        assert (card["runtime"], card["papel"]) == ("claude-code", "executor")

    def test_republish_is_throttled_so_a_5s_loop_is_not_a_write_storm(
            self, tmp_path, monkeypatch):
        self._root(tmp_path, monkeypatch)
        db = tmp_path / "liaison.db"
        me = "agent-throttle"
        reactor.dispatch(db, self_id=me, peers=[], notify_cmd="true")
        first = directory.get(me)["updated_at"]
        reactor.dispatch(db, self_id=me, peers=[], notify_cmd="true")
        assert directory.get(me)["updated_at"] == first    # not rewritten

    def test_hall_membership_survives_the_refresh(self, tmp_path, monkeypatch):
        """halls belong to the agent, not to the process that republishes."""
        self._root(tmp_path, monkeypatch)
        db = tmp_path / "liaison.db"
        me = "agent-halls"
        # updated_at antigo: o throttle NÃO pode mascarar o teste — o tick
        # precisa mesmo reescrever o cartão para a preservação valer algo.
        directory.publish({"instance_id": me, "halls": ["h1"],
                           "updated_at": 0.0})
        reactor.dispatch(db, self_id=me, peers=[], notify_cmd="true")
        card = directory.get(me)
        assert card["updated_at"] > 0.0          # foi mesmo republicado
        assert card["halls"] == ["h1"]

    def test_reactor_does_not_wipe_identity_or_capabilities(
            self, tmp_path, monkeypatch):
        """O tick de presença não é dono da identidade: re-registrar sem
        identity apagava `model`, e um ("relay",) fixo apagava as demais
        capabilities a cada 5s."""
        self._root(tmp_path, monkeypatch)
        db = tmp_path / "liaison.db"
        me = "agent-keep"
        agents.register_agent(db, instance_id=me, model="opus",
                              familia="claude", capabilities=("audit", "relay"))
        reactor.dispatch(db, self_id=me, peers=[], notify_cmd="true")
        row = agents.get_agent(db, me)
        assert row["model"] == "opus"
        assert set(row["capabilities"]) == {"audit", "relay"}
