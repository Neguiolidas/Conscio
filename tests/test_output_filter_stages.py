"""Tests for DedupBlocks + SecretMask filter stages."""
from conscio.output_filter import DedupBlocks, SecretMask, build_stage, FilterPipeline


# ── DedupBlocks ──

def test_dedup_collapses_long_run():
    s = DedupBlocks(min_run=3)
    text = "\n".join(["err"] * 5 + ["done"])
    out = s.apply(text)
    assert out == "err\n… (×5)\ndone"


def test_dedup_keeps_short_run_below_min():
    s = DedupBlocks(min_run=3)
    text = "a\na\nb"  # run of 2, below min_run
    assert s.apply(text) == "a\na\nb"


def test_dedup_preserves_distinct_lines():
    s = DedupBlocks(min_run=3)
    text = "a\nb\nc"
    assert s.apply(text) == "a\nb\nc"


def test_dedup_name():
    assert DedupBlocks().name() == "dedup_blocks"


# ── SecretMask ──

def test_mask_openai_key():
    s = SecretMask()
    out = s.apply("token is sk-abcdef0123456789ABCDEFGHIJ done")
    assert "sk-abcdef" not in out
    assert "***REDACTED***" in out


def test_mask_key_value_preserves_label():
    s = SecretMask()
    out = s.apply("api_key: supersecretvalue123")
    assert "supersecretvalue123" not in out
    assert "api_key" in out  # label kept, value redacted
    assert "***REDACTED***" in out


def test_mask_github_token():
    s = SecretMask()
    out = s.apply("ghp_0123456789abcdefABCDEF0123456789ab")
    assert "ghp_0123456789" not in out


def test_mask_leaves_normal_text_untouched():
    s = SecretMask()
    text = "the cat sat on the mat and ran fast"
    assert s.apply(text) == text


def test_mask_name():
    assert SecretMask().name() == "secret_mask"


# ── Registry + crash-safety ──

def test_build_stage_dedup_and_mask():
    assert isinstance(build_stage("dedup_blocks", {"min_run": 4}), DedupBlocks)
    assert isinstance(build_stage("secret_mask", {}), SecretMask)


def test_stages_are_crash_safe_in_pipeline():
    # A pipeline that includes both stages returns a string, never raises.
    p = FilterPipeline(stages=[SecretMask(), DedupBlocks(min_run=2)])
    out = p.apply("api_key: x\nrepeat\nrepeat\nrepeat")
    assert isinstance(out, str)
    assert "***REDACTED***" in out
