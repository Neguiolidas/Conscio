# tests/test_noosphere_fingerprint.py
def test_leaf_matches_known_value():
    from conscio.agency.fingerprint import goal_fingerprint
    import hashlib
    expected = hashlib.sha256("deploy".encode("utf-8")).hexdigest()[:16]
    assert goal_fingerprint("deploy") == expected
    assert len(goal_fingerprint("anything")) == 16


def test_act_reexport_is_same_value():
    from conscio.agency.act import goal_fingerprint as via_act
    from conscio.agency.fingerprint import goal_fingerprint as leaf
    assert via_act("x") == leaf("x")
