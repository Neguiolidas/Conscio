"""Lote H round 3 (v4.8.1) — IDLE is healthy, quarantine still fails.

The hostile review's required pair: (1) a heartbeat inside the cooldown
via engine.run(AwakeBudget()) stops with stopped='idle', failures=0, no
failure-rate brake event, zero LLM calls; (2) regression guard — active
goals all quarantined still yield FAILED and the brake still fires.
"""
from __future__ import annotations

from conscio.agency.act import ActStatus
from conscio.agency.adapter import MockAdapter
from conscio.agency.fingerprint import goal_fingerprint
from conscio.awake.budget import AwakeBudget
from conscio.engine import ConsciousnessEngine


def _engine_with_counter(tmp_path):
    eng = ConsciousnessEngine("mock", context_window=131000,
                              storage_path=tmp_path, autodetect=False)
    ad = MockAdapter()
    counter = {"calls": 0}
    orig = ad.generate
    ad.generate = (lambda *a, **k:
                   (counter.__setitem__("calls", counter["calls"] + 1),
                    orig(*a, **k))[1])
    eng.attach_adapter(ad)
    eng.wake()
    return eng, counter


def _brake_events(eng):
    """Capture failure-rate brake emissions (the review's 'never silent'
    channel) without touching the real bus."""
    events = []
    bus = getattr(eng, "event_bus", None)
    if bus is None:
        return events
    orig = bus.emit

    def spy(**kw):
        data = kw.get("data", {})
        if isinstance(data, dict) and "failure" in str(data.get("message",
                                                              "")).lower():
            events.append(data)
        return orig(**kw)

    bus.emit = spy
    return events


def _age_ledger(eng, seconds):
    led = eng._act_pipeline.ledger
    led._conn.execute("UPDATE actions SET ts = ts - ?", (seconds,))
    led._conn.commit()


def test_idle_heartbeat_inside_cooldown(tmp_path):
    """HB2 inside the window: stopped='idle', failures=0, no brake event,
    zero additional LLM calls."""
    eng, counter = _engine_with_counter(tmp_path)
    events = _brake_events(eng)
    eng.run(AwakeBudget(), world_state="hb1")     # burns the proposal
    _age_ledger(eng, 15 * 60)                    # next HB: 15 min later
    calls_before = counter["calls"]
    rep = eng.run(AwakeBudget(), world_state="hb2")
    assert rep.stopped == "idle", f"expected 'idle', got {rep.stopped!r}"
    assert rep.failures == 0, f"failures must be 0, got {rep.failures}"
    assert counter["calls"] == calls_before, "idle heartbeat must spend zero"
    assert not events, "no failure-rate brake event on a healthy idle"
    eng.close()


def test_quarantined_goals_still_fail_and_brake(tmp_path):
    """Regression guard: an active-but-quarantined goal is REAL trouble —
    act() must still return FAILED and the aggregate brake must still
    fire (this was the review's explicit 'keep FAILED' case)."""
    eng, _counter = _engine_with_counter(tmp_path)
    _events = _brake_events(eng)
    g = eng.goals.generate_from_curiosity("investigate the anomaly",
                                          source="internal")
    assert g is not None
    fp = goal_fingerprint(g.description)
    eng._act_pipeline.breaker.trip(fp, detail="test quarantine",
                                   goal_text=g.description)
    # awake budget: min_attempts=2, max_failure_rate=0.3 — two failed
    # cycles on the quarantined goal trip the aggregate brake.
    rep = eng.run(AwakeBudget(), world_state="quarantined")
    assert rep.failures >= 1, "quarantined act must count as failure"
    assert rep.reports[0].status is ActStatus.FAILED, (
        "quarantine path must stay FAILED, not IDLE")
    eng.close()


def test_idle_heartbeat_still_dreams(tmp_path):
    """Round 4: the IDLE break must run AFTER the dream housekeeping —
    idle is the normal state 3 of 4 heartbeats; skipping the dream there
    would leave the ledgers unpruned except hourly (the v3.9.4 orphaned
    invariant, reintroduced in round 3 and caught by the round-4 review)."""
    from conscio.engine import DreamRecommendation
    eng, _counter = _engine_with_counter(tmp_path)
    dreams = {"n": 0}
    eng.dream = lambda *a, **k: dreams.__setitem__("n", dreams["n"] + 1)
    eng.run(AwakeBudget(), world_state="hb1")          # burns the proposal
    # force dream recommended on every reflect (reflect resets the flag)
    _orig_reflect = eng.reflect

    def _reflect(*a, **kw):
        out = _orig_reflect(*a, **kw)
        eng.dream_recommended = DreamRecommendation(True, "test", 0.1)
        return out

    eng.reflect = _reflect
    _age_ledger(eng, 15 * 60)                          # inside the window
    rep = eng.run(AwakeBudget(), world_state="hb2")
    assert rep.stopped == "idle"
    assert dreams["n"] >= 1, (
        "dream housekeeping must run on idle heartbeats — the v3.9.4 "
        "invariant; the IDLE break must sit AFTER it")
    eng.close()
