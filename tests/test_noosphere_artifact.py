# tests/test_noosphere_artifact.py
import hashlib
from conscio.noosphere import artifact


def _body():
    return artifact.build_body(
        goal_fp="abc", goal_text="deploy",
        tool_seq=["a", "b"],
        plan_template=[{"tool": "a", "args": {}, "rationale": "r"},
                       {"tool": "b", "args": {"x": 1}, "rationale": "s"}])


def test_body_has_content_only_fields():
    b = _body()
    assert set(b) == {"schema_version", "goal_fp", "goal_text",
                      "tool_seq", "plan_template"}
    assert "published_ts" not in b and "successes" not in b


def test_hash_is_deterministic_and_canonical():
    canon = artifact.canonical_bytes(_body())
    assert artifact.content_hash(canon) == hashlib.sha256(canon).hexdigest()
    # key order in the input dict must not change the hash
    reordered = {"plan_template": _body()["plan_template"],
                 "tool_seq": ["a", "b"], "goal_text": "deploy",
                 "goal_fp": "abc", "schema_version": artifact.ARTIFACT_SCHEMA}
    assert artifact.canonical_bytes(reordered) == canon


def test_hash_changes_when_content_changes():
    h1 = artifact.content_hash(artifact.canonical_bytes(_body()))
    other = _body()
    other["goal_text"] = "destroy"
    h2 = artifact.content_hash(artifact.canonical_bytes(other))
    assert h1 != h2
