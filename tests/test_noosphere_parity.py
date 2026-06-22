# tests/test_noosphere_parity.py
# Imports conscio.agency.skills to read its module-level constant — kept in a
# SEPARATE file from the engine-free proof (which forbids that import).
def test_min_serve_rate_parity():
    from conscio.agency.skills import MIN_SERVE_RATE as SRC
    from conscio.noosphere.publish import MIN_SERVE_RATE as NOO
    assert NOO == SRC


def test_fingerprint_parity():
    from conscio.agency.act import goal_fingerprint as via_act
    from conscio.agency.fingerprint import goal_fingerprint as leaf
    assert via_act("deploy") == leaf("deploy")


def test_audit_constants_match_engine_kernels():
    from conscio.agency import breaker, trust
    from conscio.noosphere import audit
    assert audit.BREAKER_THRESHOLD == breaker.DEFAULT_MAX_RETRIES
    assert audit.L2_ACCURACY == trust.L2_ACCURACY
    assert audit.L3_ACCURACY == trust.L3_ACCURACY
    assert audit.AUTONOMY_MIN_ROWS == trust.AUTONOMY_MIN_ROWS
