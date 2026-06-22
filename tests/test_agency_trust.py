"""TrustMatrix tests (F2) — MockAdapter-free, pure local state."""
from conscio.agency.ledger import ActionLedger
from conscio.agency.trust import TrustMatrix
from conscio.meta_cognition import MetaCognition


def test_expire_error_removes_oldest_matching(tmp_path):
    meta = MetaCognition(tmp_path)
    meta.record_error("act:fs_write:exec_fail")     # oldest
    meta.record_error("act:fs_write:skeptic_fail")
    meta.record_error("act:fs_read:exec_fail")      # different prefix
    removed = meta.expire_error("act:fs_write")
    assert removed == 1
    patterns = [ep["pattern"] for ep in meta.frequent_errors(min_count=1)]
    assert "act:fs_write:exec_fail" not in patterns      # oldest gone
    assert "act:fs_write:skeptic_fail" in patterns
    assert "act:fs_read:exec_fail" in patterns


def test_expire_error_no_match_returns_zero(tmp_path):
    meta = MetaCognition(tmp_path)
    assert meta.expire_error("act:nothing") == 0


# ── TrustMatrix ─────────────────────────────────────────────────────────


def _seed_meta(meta, task="fs_read", n=12, outcome="success",
               confidence=None):
    for _ in range(n):
        c = confidence if confidence is not None else (
            1.0 if outcome == "success" else 0.0)
        meta.record_confidence(task, c, outcome)


def _trust(tmp_path, meta, reflect_count=0):
    led = ActionLedger(tmp_path / "conscio.db")
    return TrustMatrix(meta, led, tmp_path / "conscio.db",
                       reflect_count_fn=lambda: reflect_count), led


def test_warmup_floor_grants_one_try_on_virgin_db(tmp_path):
    meta = MetaCognition(tmp_path)
    trust, led = _trust(tmp_path, meta)
    # virgin: calibration=0.5, accuracy=0.5 -> 2*0.25 rounds to 0 -> raw=1;
    # the warmup floor keeps it >= 1 regardless
    assert trust.max_action_retries("new_tool") >= 1
    trust.close()
    led.close()


def test_error_penalty_can_block(tmp_path):
    meta = MetaCognition(tmp_path)
    _seed_meta(meta, task="fs_write", n=12, outcome="failure", confidence=1.0)
    for _ in range(3):
        meta.record_error("act:fs_write:exec_fail")
    for _ in range(3):
        meta.record_error("act:fs_write:skeptic_fail")
    trust, led = _trust(tmp_path, meta)
    # ledger needs >= WARMUP_MIN_ROWS rows so the floor stops applying
    for _ in range(10):
        led.record(goal_fp="g", tool="fs_write", args_json="{}",
                   rationale="", tier="T2", status="failed")
    # accuracy=0 -> raw = 1 + 0 - 2 = -1 -> clamp 0; probation may grant 1
    assert trust.max_action_retries("fs_write") in (0, 1)
    trust.close()
    led.close()


def test_probation_grants_probe_after_epoch(tmp_path):
    meta = MetaCognition(tmp_path)
    _seed_meta(meta, task="fs_write", n=12, outcome="failure", confidence=1.0)
    for _ in range(3):
        meta.record_error("act:fs_write:exec_fail")
    for _ in range(3):
        meta.record_error("act:fs_write:skeptic_fail")
    count = {"n": 0}
    led = ActionLedger(tmp_path / "conscio.db")
    for _ in range(10):
        led.record(goal_fp="g", tool="fs_write", args_json="{}",
                   rationale="", tier="T2", status="failed")
    trust = TrustMatrix(meta, led, tmp_path / "conscio.db",
                        reflect_count_fn=lambda: count["n"])
    count["n"] = 10                  # consume epoch 0 (if granted)
    trust.max_action_retries("fs_write")
    count["n"] = 60                  # epoch 2: new probe due
    probed = trust.max_action_retries("fs_write")
    assert probed >= 1               # probation revives the blocked task
    again = trust.max_action_retries("fs_write")
    assert again == probed           # idempotent within the same epoch
    trust.close()
    led.close()


def test_on_success_expires_oldest_error(tmp_path):
    meta = MetaCognition(tmp_path)
    for _ in range(3):
        meta.record_error("act:fs_write:exec_fail")
    trust, led = _trust(tmp_path, meta)
    trust.on_success("fs_write")
    assert all(not ep["pattern"].startswith("act:fs_write")
               for ep in meta.frequent_errors(min_count=1))
    trust.close()
    led.close()


def test_autonomy_levels(tmp_path):
    meta = MetaCognition(tmp_path)
    trust, led = _trust(tmp_path, meta)
    assert trust.autonomy_level("fs_read") == 1          # cold start
    # earn L2: calibration >= 0.6, accuracy >= 0.7, >= 10 ledger records
    _seed_meta(meta, task="fs_read", n=12, outcome="success", confidence=0.9)
    for _ in range(10):
        led.record(goal_fp="g", tool="fs_read", args_json="{}",
                   rationale="", tier="T2", status="executed")
    assert trust.autonomy_level("fs_read") == 2
    trust.close()
    led.close()


def test_l3_when_elite_and_no_recent_trips(tmp_path):
    meta = MetaCognition(tmp_path)
    _seed_meta(meta, task="fs_read", n=12, outcome="success", confidence=0.9)
    led = ActionLedger(tmp_path / "conscio.db")
    for _ in range(10):
        led.record(goal_fp="g", tool="fs_read", args_json="{}",
                   rationale="", tier="T2", status="executed")
    trust = TrustMatrix(meta, led, tmp_path / "conscio.db",
                        trips_since_fn=lambda ts: 0)
    assert trust.autonomy_level("fs_read") == 3
    trust.close()
    led.close()


def test_recent_trip_caps_at_l2(tmp_path):
    meta = MetaCognition(tmp_path)
    _seed_meta(meta, task="fs_read", n=12, outcome="success", confidence=0.9)
    led = ActionLedger(tmp_path / "conscio.db")
    for _ in range(10):
        led.record(goal_fp="g", tool="fs_read", args_json="{}",
                   rationale="", tier="T2", status="executed")
    trust = TrustMatrix(meta, led, tmp_path / "conscio.db",
                        trips_since_fn=lambda ts: 1)
    assert trust.autonomy_level("fs_read") == 2
    trust.close()
    led.close()


def test_l3_needs_elite_calibration(tmp_path):
    meta = MetaCognition(tmp_path)
    # avg_conf 0.7 vs accuracy 1.0 -> calibration 0.7: L2 yes, L3 no
    _seed_meta(meta, task="fs_read", n=12, outcome="success", confidence=0.7)
    led = ActionLedger(tmp_path / "conscio.db")
    for _ in range(10):
        led.record(goal_fp="g", tool="fs_read", args_json="{}",
                   rationale="", tier="T2", status="executed")
    trust = TrustMatrix(meta, led, tmp_path / "conscio.db",
                        trips_since_fn=lambda ts: 0)
    assert trust.autonomy_level("fs_read") == 2
    trust.close()
    led.close()


def test_no_trip_evidence_caps_at_l2(tmp_path):
    """Without trips_since_fn wiring L3 is unreachable (fail-safe)."""
    meta = MetaCognition(tmp_path)
    _seed_meta(meta, task="fs_read", n=12, outcome="success", confidence=0.9)
    led = ActionLedger(tmp_path / "conscio.db")
    for _ in range(10):
        led.record(goal_fp="g", tool="fs_read", args_json="{}",
                   rationale="", tier="T2", status="executed")
    trust = TrustMatrix(meta, led, tmp_path / "conscio.db")
    assert trust.autonomy_level("fs_read") == 2
    trust.close()
    led.close()


# ── B-003b: tz-correct L3 trip window (I-A3) ─────────────────────────────


def test_try_break_trips_window_counts_recent_trip_under_nonutc_tz(
        monkeypatch, tmp_path):
    """engine._trips_since must count a recent trip regardless of machine TZ.

    Trip events are stored naive-UTC; the OLD code built the window boundary via
    datetime.fromtimestamp(ts) (naive LOCAL), so under UTC+X the boundary landed in
    the future and recent trips were undercounted -> L3 granted despite trips.
    """
    import time

    from conscio.engine import ConsciousnessEngine

    monkeypatch.setenv("TZ", "Asia/Tokyo")               # UTC+9
    time.tzset()
    eng = ConsciousnessEngine(model_name="glm-5.1", storage_path=tmp_path)
    try:
        eng.event_bus.emit(
            type="error", category="system",
            data={"msg": "Intractable dissonance on goal X"})
        boundary = time.time() - 5                        # window opened 5s ago
        assert eng._trips_since(boundary) >= 1            # OLD local-tz code -> 0
    finally:
        eng.close()
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


def test_fast_path_requires_high_calibration(tmp_path):
    meta = MetaCognition(tmp_path)
    trust, led = _trust(tmp_path, meta)
    assert trust.fast_path_ok() is False                 # 0.5 cold start
    _seed_meta(meta, n=20, outcome="success", confidence=0.95)
    assert trust.fast_path_ok() == (meta.calibration_score() >= 0.75)
    trust.close()
    led.close()


def test_autonomy_constants_extracted_and_distinct():
    from conscio.agency import trust
    assert trust.L2_ACCURACY == 0.7
    assert trust.L3_ACCURACY == 0.85
    assert trust.AUTONOMY_MIN_ROWS == 10
    # WARMUP_MIN_ROWS is a different concept (max_action_retries floor),
    # equal to 10 only by coincidence — must remain its own constant.
    assert trust.WARMUP_MIN_ROWS == 10
    assert "AUTONOMY_MIN_ROWS" in dir(trust)
