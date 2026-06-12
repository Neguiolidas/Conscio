"""TrustMatrix tests (F2) — MockAdapter-free, pure local state."""
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
