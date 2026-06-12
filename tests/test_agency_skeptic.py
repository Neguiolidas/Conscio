"""Skeptic phase tests (F2) — clean call, fail-closed, two modes."""
from conscio.agency.adapter import AdapterCaps, MockAdapter
from conscio.agency.contracts import ActionProposal
from conscio.agency.skeptic import (Skeptic, build_skeptic_prompt,
                                    parse_checklist)


def _proposal(tool="fs_read", args=None):
    return ActionProposal(tool=tool, args=args or {"path": "notes.md"},
                          rationale="check previous state",
                          expected_outcome="file content returned")


# ── prompt hygiene (A1: zero history leak) ──────────────────────────────

def test_skeptic_prompt_is_clean_of_actor_material():
    prompt = build_skeptic_prompt(_proposal(), facts="server is up",
                                  mode="checklist")
    assert "hostile auditor" in prompt.lower()
    assert "fs_read" in prompt
    assert "server is up" in prompt
    # nothing from the actor side may leak into the audit call
    assert "volition of a persistent agent" not in prompt
    assert "Active goal" not in prompt


def test_skeptic_call_carries_no_actor_history():
    adapter = MockAdapter(script=["A1: NO\nA2: NO\nA3: YES"])
    sk = Skeptic(adapter)
    sk.audit(_proposal(), goal_text="tidy notes")
    sent = adapter.calls[0]["prompt"]
    assert "volition" not in sent.lower()


# ── checklist mode (deterministic aggregation) ──────────────────────────

def test_checklist_all_expected_passes():
    v = parse_checklist("A1: NO\nA2: NO\nA3: YES")
    assert v.passed and v.confidence == 1.0


def test_checklist_any_bad_answer_fails():
    v = parse_checklist("A1: YES\nA2: NO\nA3: YES")
    assert not v.passed
    assert any("Q1" in r for r in v.reasons)


def test_checklist_unparseable_fails_closed():
    v = parse_checklist("I think it is probably fine.")
    assert not v.passed
    assert "unparseable" in v.reasons[0]


def test_audit_checklist_end_to_end():
    sk = Skeptic(MockAdapter(script=["A1: no\nA2: NO\nA3: yes"]))
    assert sk.audit(_proposal()).passed          # case-insensitive answers


# ── open mode (frontier critique) ───────────────────────────────────────

def test_audit_open_mode_parses_json_verdict():
    raw = ('{"verdict": "fail", "reasons": ["touches unrelated file"],'
           ' "risk_flags": ["scope"]}')
    sk = Skeptic(MockAdapter(script=[raw]), mode="open")
    v = sk.audit(_proposal())
    assert not v.passed                          # verdict normalized to upper
    assert v.reasons == ["touches unrelated file"]


def test_audit_open_mode_garbage_fails_closed():
    sk = Skeptic(MockAdapter(script=["sure, looks good to me!"]), mode="open")
    assert not sk.audit(_proposal()).passed


def test_audit_adapter_error_fails_closed():
    sk = Skeptic(MockAdapter(script=[]))         # exhausted -> AdapterError
    v = sk.audit(_proposal())
    assert not v.passed
    assert "audit call failed" in v.reasons[0]


# ── mixed-cortex ────────────────────────────────────────────────────────

def test_mixed_cortex_uses_own_adapter():
    auditor = MockAdapter(script=["A1: NO\nA2: NO\nA3: YES"],
                          caps=AdapterCaps(model_name="auditor-8b"))
    sk = Skeptic(auditor)
    sk.audit(_proposal())
    assert len(auditor.calls) == 1               # the audit ran HERE


# ── facts injection ─────────────────────────────────────────────────────

def test_facts_fn_receives_goal_text():
    seen = []
    adapter = MockAdapter(script=["A1: NO\nA2: NO\nA3: YES"])
    sk = Skeptic(adapter, facts_fn=lambda q: seen.append(q) or "fact-x")
    sk.audit(_proposal(), goal_text="organize the sandbox")
    assert seen == ["organize the sandbox"]
    assert "fact-x" in adapter.calls[0]["prompt"]
