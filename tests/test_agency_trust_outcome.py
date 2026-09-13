from conscio.agency import outcome as o
from conscio.agency.act import apply_outcome_to_trust


class _Meta:
    def __init__(self):
        self.errors = []
        self.expired = []

    def record_error(self, pattern):
        self.errors.append(pattern)

    def expire_error(self, pattern):
        self.expired.append(pattern)


class _Trust:
    """Espelha a TrustMatrix real: on_success delega em meta.expire_error
    (trust.py:86). Se a producao mudar esse caminho, este duble mente."""

    def __init__(self, meta):
        self.meta = meta

    def on_success(self, task_type):
        self.meta.expire_error(f"act:{task_type}")


def _apply(outcome_value):
    meta = _Meta()
    apply_outcome_to_trust(_Trust(meta), meta, "bash", outcome_value)
    return meta


def test_verified_moves_trust_up():
    meta = _apply(o.VERIFIED)
    assert meta.expired == ["act:bash"] and meta.errors == []


def test_contradicted_records_error():
    """TrustMatrix nao tem caminho negativo; o peso entra por frequent_errors."""
    meta = _apply(o.CONTRADICTED)
    assert meta.errors == ["act:bash"] and meta.expired == []


def test_pending_moves_nothing():
    meta = _apply(o.PENDING)
    assert meta.errors == [] and meta.expired == []


def test_unsupported_moves_nothing():
    meta = _apply(o.UNSUPPORTED)
    assert meta.errors == [] and meta.expired == []


def test_out_of_scope_moves_nothing():
    meta = _apply(o.OUT_OF_SCOPE)
    assert meta.errors == [] and meta.expired == []


def test_missing_collaborators_do_not_raise():
    """O hook roda fora do engine: trust/meta podem nao existir."""
    apply_outcome_to_trust(None, None, "bash", o.VERIFIED)
    apply_outcome_to_trust(None, None, "bash", o.CONTRADICTED)


def test_the_real_trust_matrix_still_delegates_to_expire_error():
    """Ancora o duble no codigo real: se on_success parar de chamar
    expire_error, os testes acima viram ficcao e este falha junto."""
    import inspect

    from conscio.agency.trust import TrustMatrix
    assert "expire_error" in inspect.getsource(TrustMatrix.on_success)
