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
