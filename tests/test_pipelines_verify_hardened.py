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
