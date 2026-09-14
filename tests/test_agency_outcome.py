import time

from conscio.agency import outcome as o


def test_pending_inside_window_stays_pending():
    now = time.time()
    assert o.effective(o.PENDING, now - 3600, now) == o.PENDING


def test_pending_past_retention_reads_as_unsupported():
    now = time.time()
    old = now - (o.RETENTION_DAYS + 1) * 86400
    assert o.is_expired(o.PENDING, old, now) is True
    assert o.effective(o.PENDING, old, now) == o.UNSUPPORTED


def test_terminal_outcome_never_expires():
    now = time.time()
    old = now - 999 * 86400
    assert o.is_expired(o.VERIFIED, old, now) is False
    assert o.effective(o.VERIFIED, old, now) == o.VERIFIED


def test_out_of_scope_never_expires_and_is_not_pending():
    now = time.time()
    old = now - 999 * 86400
    assert o.is_expired(o.OUT_OF_SCOPE, old, now) is False
    assert o.effective(o.OUT_OF_SCOPE, old, now) == o.OUT_OF_SCOPE
