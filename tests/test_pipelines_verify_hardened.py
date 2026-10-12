import pytest

from conscio.engine import ConsciousnessEngine


@pytest.fixture
def engine(tmp_path):
    eng = ConsciousnessEngine(model_name="test", storage_path=str(tmp_path))
    yield eng
    eng.close()


def _obs(engine, text="[abc1234] done"):
    from conscio import obsstore
    return obsstore.put_observation(
        engine._obs_conn(), tool="Bash", input_text="git commit",
        output_text=text, session_id="s1", project="p", agent="a",
        ts="2026-09-13T10:00:00")


def test_no_criteria_is_not_a_pass(engine):
    """Verificar coisa nenhuma deixa de ser aprovado."""
    result = engine.verify(criteria=[])
    assert result["pass"] is False
    assert result["reason"] == "no criteria"


def test_self_issued_marker_is_not_evidence(engine):
    """Evento cujo verify:evidence e so o ID do criterio nao prova nada."""
    engine.event_bus.emit("host:event", "external",
                          {"verify:evidence": "C1", "text": "C1"})
    result = engine.verify(criteria=[{"id": "C1", "description": "d"}])
    assert result["pass"] is False
    assert result["failed"][0]["reason"] == "evidence is not a resolvable pointer"


def test_pointer_that_resolves_passes(engine):
    oid = _obs(engine)
    engine.event_bus.emit("host:event", "external",
                          {"verify:evidence": "C1", "text": f"obs:{oid}"})
    result = engine.verify(criteria=[{"id": "C1", "description": "d"}])
    assert result["pass"] is True


def test_well_formed_pointer_to_nothing_does_not_pass(engine):
    """A EMENDA A2: obs:999999 tem a forma certa e nao aponta para nada.

    Aceitar a forma seria trocar 'o criterio se aprova citando o proprio nome'
    por 'se aprova citando obs: e um numero inventado'.
    """
    _obs(engine)                       # ha captura, so nao a citada
    engine.event_bus.emit("host:event", "external",
                          {"verify:evidence": "C1", "text": "obs:999999"})
    result = engine.verify(criteria=[{"id": "C1", "description": "d"}])
    assert result["pass"] is False
    assert result["failed"][0]["reason"] == "evidence is not a resolvable pointer"


def test_blob_hash_that_exists_resolves(engine):
    from conscio import obsstore
    conn = engine._obs_conn()
    _obs(engine)
    h = conn.execute("SELECT h FROM blobs LIMIT 1").fetchone()[0]
    assert obsstore is not None
    engine.event_bus.emit("host:event", "external",
                          {"verify:evidence": "C1", "text": h})
    result = engine.verify(criteria=[{"id": "C1", "description": "d"}])
    assert result["pass"] is True


def test_missing_evidence_is_still_reported_as_missing(engine):
    result = engine.verify(criteria=[{"id": "C1", "description": "d"}])
    assert result["pass"] is False
    assert result["failed"][0]["reason"] == "no evidence found"


# ── Slice 6: Verifier Unavailable Hardened Tests (T1–T9) ───────────────

def test_t1_open_fails_c1_in_unverified(engine, monkeypatch):
    import sqlite3

    engine.event_bus.emit(
        "host:event", "external", {"verify:evidence": "C1", "text": "obs:1"}
    )
    def _raise_conn():
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(engine, "_obs_conn", _raise_conn)
    res = engine.verify(criteria=[{"id": "C1", "description": "d1"}])
    assert res["pass"] is False
    assert res["verifier_unavailable"] is True
    assert res["unverified"] == [
        {
            "id": "C1",
            "description": "d1",
            "evidence": "obs:1",
            "reason": "verifier unavailable",
        }
    ]
    assert res["failed"] == []
    assert res["reason"] == "verifier unavailable (OperationalError)"


def test_t2_closed_cached_connection_reports_unverified(engine):
    engine.event_bus.emit(
        "host:event", "external", {"verify:evidence": "C1", "text": "obs:1"}
    )
    conn = engine._obs_conn()
    conn.close()
    res = engine.verify(criteria=[{"id": "C1", "description": "d1"}])
    assert res["pass"] is False
    assert res["verifier_unavailable"] is True
    assert res["unverified"] == [
        {
            "id": "C1",
            "description": "d1",
            "evidence": "obs:1",
            "reason": "verifier unavailable",
        }
    ]
    assert res["failed"] == []
    assert res["reason"] == "verifier unavailable (ProgrammingError)"


def test_t3_mixed_missing_evidence_and_unavailable(engine, monkeypatch):
    import sqlite3

    engine.event_bus.emit(
        "host:event", "external", {"verify:evidence": "C2", "text": "obs:2"}
    )
    def _raise_conn():
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(engine, "_obs_conn", _raise_conn)
    res = engine.verify(
        criteria=[
            {"id": "C1", "description": "d1"},
            {"id": "C2", "description": "d2"},
        ]
    )
    assert res["pass"] is False
    assert res["verifier_unavailable"] is True
    assert res["failed"] == [
        {
            "id": "C1",
            "description": "d1",
            "reason": "no evidence found",
        }
    ]
    assert res["unverified"] == [
        {
            "id": "C2",
            "description": "d2",
            "evidence": "obs:2",
            "reason": "verifier unavailable",
        }
    ]


def test_t4_open_fails_no_criteria_have_evidence(engine, monkeypatch):
    import sqlite3

    def _raise_conn():
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(engine, "_obs_conn", _raise_conn)
    res = engine.verify(
        criteria=[
            {"id": "C1", "description": "d1"},
            {"id": "C2", "description": "d2"},
        ]
    )
    assert res["pass"] is False
    assert res["verifier_unavailable"] is False
    assert res["unverified"] == []
    assert res["failed"] == [
        {"id": "C1", "description": "d1", "reason": "no evidence found"},
        {"id": "C2", "description": "d2", "reason": "no evidence found"},
    ]
    assert "reason" not in res


def test_t5_midway_failure_keeps_already_verified(engine, monkeypatch):
    import sqlite3

    oid = _obs(engine)
    engine.event_bus.emit(
        "host:event", "external", {"verify:evidence": "C1", "text": f"obs:{oid}"}
    )
    engine.event_bus.emit(
        "host:event", "external", {"verify:evidence": "C2", "text": "obs:9999"}
    )
    engine.event_bus.emit(
        "host:event", "external", {"verify:evidence": "C3", "text": "obs:9998"}
    )

    real_conn = engine._obs_conn()

    class ConnWrapper:
        def __init__(self, real):
            self.real = real
            self.query_count = 0
            self.fail_count = 0

        def execute(self, sql, params=()):
            self.query_count += 1
            if self.query_count > 1:
                self.fail_count += 1
                raise sqlite3.OperationalError("database disk image is malformed")
            return self.real.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self.real, name)

    wrapper = ConnWrapper(real_conn)
    monkeypatch.setattr(engine, "_obs_conn", lambda: wrapper)

    res = engine.verify(
        criteria=[
            {"id": "C1", "description": "d1"},
            {"id": "C2", "description": "d2"},
            {"id": "C3", "description": "d3"},
        ]
    )
    assert res["pass"] is False
    assert res["verifier_unavailable"] is True
    assert len(res["verified"]) == 1
    assert res["verified"][0]["id"] == "C1"
    assert res["unverified"] == [
        {
            "id": "C2",
            "description": "d2",
            "evidence": "obs:9999",
            "reason": "verifier unavailable",
        },
        {
            "id": "C3",
            "description": "d3",
            "evidence": "obs:9998",
            "reason": "verifier unavailable",
        },
    ]
    assert res["failed"] == []
    assert wrapper.fail_count == 1


def test_t6_non_sqlite_lookup_error_raises(engine, monkeypatch):
    engine.event_bus.emit(
        "host:event", "external", {"verify:evidence": "C1", "text": "obs:1"}
    )
    real_conn = engine._obs_conn()

    class BuggyWrapper:
        def execute(self, *args, **kwargs):
            raise RuntimeError("internal bug")

        def __getattr__(self, name):
            return getattr(real_conn, name)

    monkeypatch.setattr(engine, "_obs_conn", lambda: BuggyWrapper())
    with pytest.raises(RuntimeError, match="internal bug"):
        engine.verify(criteria=[{"id": "C1", "description": "d1"}])
    # The lock must come back released even when the lookup raises a
    # non-sqlite error: a mutant that releases it only on success is RED here.
    assert not engine._obs_lock.locked()


def test_t7_gate_vetoed_payload_carries_unverified(engine, monkeypatch):
    import sqlite3

    engine.event_bus.emit(
        "host:event", "external", {"verify:evidence": "C1", "text": "obs:1"}
    )
    def _raise_conn():
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(engine, "_obs_conn", _raise_conn)
    res = engine.verify(criteria=[{"id": "C1", "description": "d1"}])
    assert res["pass"] is False

    vetoed = engine.event_bus.query(type="gate:vetoed")
    assert len(vetoed) == 1
    assert vetoed[0].data["verifier_unavailable"] is True
    assert vetoed[0].data["unverified_criteria"] == ["C1"]
    assert vetoed[0].data["failed_criteria"] == []

    verified_ev = engine.event_bus.query(type="pipeline:verified")
    assert len(verified_ev) == 0


def test_t8_lock_held_during_open_and_lookup(engine, monkeypatch):
    oid = _obs(engine)
    engine.event_bus.emit(
        "host:event", "external", {"verify:evidence": "C1", "text": f"obs:{oid}"}
    )

    lock_held_during_query = False
    real_conn = engine._obs_conn()

    class SpyLock:
        def __init__(self, real_lock):
            self.real_lock = real_lock
            self.acquired_count = 0
            self.is_held = False

        def __enter__(self):
            self.real_lock.acquire()
            self.acquired_count += 1
            self.is_held = True
            return self

        def __exit__(self, *args):
            self.is_held = False
            self.real_lock.release()

    spy_lock = SpyLock(engine._obs_lock)
    engine._obs_lock = spy_lock

    class ConnSpy:
        def __init__(self, real):
            self.real = real

        def execute(self, *args, **kwargs):
            nonlocal lock_held_during_query
            if spy_lock.is_held:
                lock_held_during_query = True
            return self.real.execute(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.real, name)

    monkeypatch.setattr(engine, "_obs_conn", lambda: ConnSpy(real_conn))

    res = engine.verify(criteria=[{"id": "C1", "description": "d1"}])
    assert res["pass"] is True
    assert spy_lock.acquired_count == 1
    assert lock_held_during_query is True


def test_t9_shape_no_criteria_and_all_pass_have_unverified_keys(engine):
    r_empty = engine.verify(criteria=[])
    assert r_empty["pass"] is False
    assert r_empty["unverified"] == []
    assert r_empty["verifier_unavailable"] is False

    oid = _obs(engine)
    engine.event_bus.emit(
        "host:event", "external", {"verify:evidence": "C1", "text": f"obs:{oid}"}
    )
    r_pass = engine.verify(criteria=[{"id": "C1", "description": "d1"}])
    assert r_pass["pass"] is True
    assert r_pass["unverified"] == []
    assert r_pass["verifier_unavailable"] is False


# ── Slice 6 rework: B1 prose is never unverified; lock/release/shape teeth ──

def test_b1_store_down_prose_evidence_stays_failed(engine, monkeypatch):
    """B1: prose evidence is not pointer-shaped, so with the store down it
    must FAIL with the unresolvable-pointer reason, never 'unverified'.
    `unverified` holds criteria whose POINTER could not be looked up, and
    prose has no pointer to look up — store up or down."""
    import sqlite3

    engine.event_bus.emit(
        "host:event", "external",
        {"verify:evidence": "C1", "text": "looks good, trust me"},
    )

    def _raise_conn():
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(engine, "_obs_conn", _raise_conn)
    res = engine.verify(criteria=[{"id": "C1", "description": "d1"}])
    assert res["pass"] is False
    assert res["failed"] == [
        {"id": "C1", "description": "d1",
         "reason": "evidence is not a resolvable pointer"},
    ]
    assert res["unverified"] == []
    assert res["verifier_unavailable"] is False
    assert "reason" not in res


def test_b1_store_down_pointer_unverified_prose_failed(engine, monkeypatch):
    """B1 mixed: with the store down, the well-formed pointer goes to
    `unverified` (its lookup could not happen) while the prose criterion
    still fails on its own merits."""
    import sqlite3

    engine.event_bus.emit(
        "host:event", "external", {"verify:evidence": "C1", "text": "obs:1"}
    )
    engine.event_bus.emit(
        "host:event", "external",
        {"verify:evidence": "C2", "text": "looks good, trust me"},
    )

    def _raise_conn():
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(engine, "_obs_conn", _raise_conn)
    res = engine.verify(
        criteria=[{"id": "C1", "description": "d1"},
                  {"id": "C2", "description": "d2"}]
    )
    assert res["pass"] is False
    assert res["failed"] == [
        {"id": "C2", "description": "d2",
         "reason": "evidence is not a resolvable pointer"},
    ]
    assert res["unverified"] == [
        {"id": "C1", "description": "d1", "evidence": "obs:1",
         "reason": "verifier unavailable"},
    ]
    assert res["verifier_unavailable"] is True
    assert res["reason"] == "verifier unavailable (OperationalError)"


def test_t8b_lock_not_held_during_event_emission(engine, monkeypatch):
    """T8b: the _obs_conn contract covers the open and the lookups only.
    The result build and the event emission run with the lock released: a
    mutant that extends the `with engine._obs_lock:` block over the emission
    must go RED here."""
    oid = _obs(engine)
    engine.event_bus.emit(
        "host:event", "external",
        {"verify:evidence": "C1", "text": f"obs:{oid}"},
    )

    real_lock = engine._obs_lock

    class SpyLock:
        def __init__(self):
            self.is_held = False

        def __enter__(self):
            real_lock.acquire()
            self.is_held = True
            return self

        def __exit__(self, *args):
            self.is_held = False
            real_lock.release()

    spy_lock = SpyLock()
    engine._obs_lock = spy_lock

    held_during_emit: list[tuple[str, bool]] = []
    real_emit = engine.event_bus.emit

    def spy_emit(event_type, source, data):
        held_during_emit.append((event_type, spy_lock.is_held))
        return real_emit(event_type, source, data)

    monkeypatch.setattr(engine.event_bus, "emit", spy_emit)

    res = engine.verify(criteria=[{"id": "C1", "description": "d1"}])
    assert res["pass"] is True
    assert held_during_emit == [("pipeline:verified", False)]


def test_t9b_all_four_result_paths_shape(engine, monkeypatch):
    """T9b: the result shape is the same on all four paths — `unverified` and
    `verifier_unavailable` always present; a top-level `reason` only on the
    no-criteria and unavailable paths, naming only the exception class."""
    import re
    import sqlite3

    # path 1: no criteria
    r = engine.verify(criteria=[])
    assert r["pass"] is False
    assert r["reason"] == "no criteria"
    assert r["unverified"] == [] and r["verifier_unavailable"] is False

    # path 2: all pass
    oid = _obs(engine)
    engine.event_bus.emit(
        "host:event", "external",
        {"verify:evidence": "C1", "text": f"obs:{oid}"},
    )
    r = engine.verify(criteria=[{"id": "C1", "description": "d1"}])
    assert r["pass"] is True
    assert "reason" not in r
    assert r["unverified"] == [] and r["verifier_unavailable"] is False

    # path 3: real failure (prose evidence, store healthy)
    engine.event_bus.emit(
        "host:event", "external",
        {"verify:evidence": "C2", "text": "looks good, trust me"},
    )
    r = engine.verify(criteria=[{"id": "C2", "description": "d2"}])
    assert r["pass"] is False
    assert "reason" not in r
    assert r["failed"][0]["reason"] == "evidence is not a resolvable pointer"
    assert r["unverified"] == [] and r["verifier_unavailable"] is False

    # path 4: unavailable (store down, pointer evidence)
    def _raise_conn():
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(engine, "_obs_conn", _raise_conn)
    engine.event_bus.emit(
        "host:event", "external",
        {"verify:evidence": "C3", "text": "obs:1"},
    )
    r = engine.verify(criteria=[{"id": "C3", "description": "d3"}])
    assert r["pass"] is False
    assert r["verifier_unavailable"] is True
    assert r["unverified"][0]["id"] == "C3"
    assert re.fullmatch(r"verifier unavailable \([A-Za-z_][A-Za-z0-9_]*\)",
                        r["reason"]), r["reason"]


@pytest.mark.xfail(
    strict=True,
    reason="pre-existing (AGENTS §8): once the cached obs connection is "
           "closed, engine._obs_conn never revalidates it, so every later "
           "verify stays 'verifier unavailable'; not fixed in slice 6",
)
def test_closed_cached_connection_is_never_revalidated(engine):
    """Pins the pre-existing staleness: after one close, a later verify with
    a perfectly resolvable pointer keeps reporting verifier unavailable
    instead of reopening the store. A fix would make both verifies pass."""
    oid = _obs(engine)
    engine.event_bus.emit(
        "host:event", "external",
        {"verify:evidence": "C1", "text": f"obs:{oid}"},
    )
    engine._obs_conn().close()          # the cache is poisoned from here on
    first = engine.verify(criteria=[{"id": "C1", "description": "d1"}])
    assert first["verifier_unavailable"] is True
    second = engine.verify(criteria=[{"id": "C1", "description": "d1"}])
    assert second["pass"] is True        # needs revalidation — fails today


# ── slice 6b: the evidence pointer grammar is ASCII and SQLite-sized ────────

@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        ("obs:0", True),
        ("obs:007", True),                      # leading zeros stay valid
        ("obs:12", True),
        ("obs:9223372036854775807", True),      # 2**63 - 1: SQLite INTEGER max
        ("obs:", False),
        ("obs:²", False),                       # superscript digit
        ("obs:١٢", False),                      # Arabic-Indic digits
        ("obs:\U0001d7cf\U0001d7d0", False),    # math bold digits
        ("obs:-1", False),
        ("obs:+1", False),
        ("obs:1 2", False),
        ("obs:12a", False),
        ("obs:9223372036854775808", False),     # 2**63: above the max
        ("obs:" + "1" * 4301, False),           # CPython int() digit cap
        ("obs:" + "0" * 5000 + "7", True),      # leading zeros, value 7
        ("obs:" + "0" * 5000, True),            # leading zeros, value 0
        ("obs:" + "9" * 20, False),             # 20 significant digits: > max
    ],
)
def test_pointer_grammar_table(evidence, expected):
    from conscio.pipelines import _is_pointer
    assert _is_pointer(evidence) is expected


@pytest.mark.parametrize(
    ("label", "evidence", "setup_rows", "expected_pass"),
    [
        # Fix-round 3: zero-padded WELL-FORMED ids go through engine.verify
        # end to end — they resolve when the row exists and fail (never
        # raise) when it does not. RED on round 2: _resolves called int() on
        # the RAW suffix, so these crashed with ValueError (CPython's
        # 4300-digit cap counts leading zeros).
        ("padded id 7 resolves when row 7 exists",
         "obs:" + "0" * 5000 + "7", 7, True),
        ("padded id 7 fails when no row 7",
         "obs:" + "0" * 5000 + "7", 1, False),
        ("all-zeros id 0 fails, no row 0 exists",
         "obs:" + "0" * 5000, 1, False),
        # Shapes OUTSIDE the grammar stay non-pointers end to end.
        ("4301 ones is not a pointer",
         "obs:" + "1" * 4301, 1, False),
        ("10000 ones is not a pointer",
         "obs:" + "1" * 10000, 1, False),
    ],
)
def test_obs_shapes_end_to_end(engine, label, evidence, setup_rows,
                               expected_pass):
    """One grammar, one id parser, verified through the whole verify() path.
    Well-formed padded ids must behave exactly like their ASCII spelling;
    every other shape must fail with the non-pointer reason, never raise."""
    for _ in range(setup_rows):
        _obs(engine)
    engine.event_bus.emit(
        "host:event", "external",
        {"verify:evidence": "C1", "text": evidence},
    )
    res = engine.verify(criteria=[{"id": "C1", "description": "d1"}])
    assert res["pass"] is expected_pass, (label, res)
    if expected_pass:
        assert res["verified"][0]["id"] == "C1"
    else:
        assert res["failed"] == [
            {"id": "C1", "description": "d1",
             "reason": "evidence is not a resolvable pointer"},
        ]
        assert res["unverified"] == []


def test_g1_superscript_pointer_fails_not_raises(engine):
    """G1: obs:² passes str.isdigit() but is not the grammar. verify must
    report the criterion failed as a non-pointer, never raise ValueError."""
    engine.event_bus.emit(
        "host:event", "external",
        {"verify:evidence": "C1", "text": "obs:²"},
    )
    res = engine.verify(criteria=[{"id": "C1", "description": "d1"}])
    assert res["pass"] is False
    assert res["failed"] == [
        {"id": "C1", "description": "d1",
         "reason": "evidence is not a resolvable pointer"},
    ]
    assert res["unverified"] == []


def test_g2_oversized_pointer_fails_not_raises(engine):
    """G2: a 25-digit number cannot be held by SQLite's INTEGER column, and
    today the bind raises OverflowError out of verify."""
    engine.event_bus.emit(
        "host:event", "external",
        {"verify:evidence": "C1", "text": "obs:" + "9" * 25},
    )
    res = engine.verify(criteria=[{"id": "C1", "description": "d1"}])
    assert res["pass"] is False
    assert res["failed"][0]["reason"] == "evidence is not a resolvable pointer"
    assert res["unverified"] == []


def test_g3_range_boundary_lookup_happens_only_below_max(engine, monkeypatch):
    """G3: obs:9223372036854775807 IS a pointer — the lookup runs and, the
    row being absent, it fails exactly like any well-formed pointer to
    nothing. obs:9223372036854775808 is NOT a pointer and must never reach
    the lookup. The no-lookup claim is proven by recording `_resolves` calls,
    not inferred from the reason string."""
    from conscio import pipelines

    _obs(engine)                       # observations exist, just not those cited
    engine.event_bus.emit(
        "host:event", "external",
        {"verify:evidence": "C1", "text": "obs:9223372036854775807"},
    )
    engine.event_bus.emit(
        "host:event", "external",
        {"verify:evidence": "C2", "text": "obs:9223372036854775808"},
    )
    looked_up: list[str] = []
    real_resolves = pipelines._resolves

    def recording_resolves(detail, conn):
        looked_up.append(detail)
        return real_resolves(detail, conn)

    monkeypatch.setattr(pipelines, "_resolves", recording_resolves)
    res = engine.verify(
        criteria=[{"id": "C1", "description": "d1"},
                  {"id": "C2", "description": "d2"}]
    )
    assert res["pass"] is False
    by_id = {f["id"]: f["reason"] for f in res["failed"]}
    assert by_id["C1"] == "evidence is not a resolvable pointer"  # looked up, absent
    assert by_id["C2"] == "evidence is not a resolvable pointer"  # not a pointer
    assert res["unverified"] == []
    assert "obs:9223372036854775807" in looked_up                 # max WAS looked up
    assert "obs:9223372036854775808" not in looked_up             # max+1 was NOT


def test_g4_arabic_indic_spelling_does_not_resolve(engine):
    """G4: the same row id written in Arabic-Indic digits is not the grammar:
    it must fail as a non-pointer, never resolve as the ASCII number.
    (RED today when the store resolves it: int('١٢') == 12.)"""
    oid = _obs(engine)
    arabic = "obs:" + "".join(chr(0x0660 + int(d)) for d in str(oid))
    engine.event_bus.emit(
        "host:event", "external",
        {"verify:evidence": "C1", "text": arabic},
    )
    res = engine.verify(criteria=[{"id": "C1", "description": "d1"}])
    assert res["pass"] is False
    assert res["failed"][0]["reason"] == "evidence is not a resolvable pointer"
    assert res["unverified"] == []


def test_g5_store_down_nonascii_pointer_fails_not_unverified(engine, monkeypatch):
    """G5: with the store down, a non-ASCII spelling is still not a pointer:
    it fails with the non-pointer reason — never unverified, never raising."""
    import sqlite3

    engine.event_bus.emit(
        "host:event", "external",
        {"verify:evidence": "C1", "text": "obs:²"},
    )

    def _raise_conn():
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(engine, "_obs_conn", _raise_conn)
    res = engine.verify(criteria=[{"id": "C1", "description": "d1"}])
    assert res["pass"] is False
    assert res["failed"] == [
        {"id": "C1", "description": "d1",
         "reason": "evidence is not a resolvable pointer"},
    ]
    assert res["unverified"] == []
    assert res["verifier_unavailable"] is False
