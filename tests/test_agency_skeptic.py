"""Skeptic phase tests (F2) — clean call, fail-closed, two modes."""
from conscio.agency.adapter import AdapterCaps, MockAdapter
from conscio.agency.contracts import ActionProposal
from conscio.agency.skeptic import Skeptic, build_skeptic_prompt, parse_checklist


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


# ── the auditor must know the tool exists ───────────────────────────────
#
# Field report (v3.9.4, NVIDIA NIM): every maintenance action was refused with
# 'No evidence that world_prune is a valid or existing tool'. The persona
# orders the auditor to refuse invented tools and the prompt never said which
# tools exist, so it judged a project-specific name against its pretraining.
# 32 actions attempted, 0 executed. The pipeline resolves the name in the
# registry before the audit runs — an unknown tool fails earlier, with
# 'unknown tool'. So by this point existence is settled, and re-deciding it
# from a prior is how a correct proposal gets refused.


def test_the_prompt_states_that_the_tool_was_verified():
    prompt = build_skeptic_prompt(
        _proposal(tool="world_prune", args={}), facts="", mode="checklist",
        tool_doc="world_prune — prune entities the world model let go stale")
    assert "registry" in prompt
    assert "prune entities the world model let go stale" in prompt


def test_the_tool_doc_reaches_the_auditor():
    adapter = MockAdapter(script=["A1: NO\nA2: NO\nA3: YES"])
    sk = Skeptic(adapter)
    sk.audit(_proposal(tool="world_prune", args={}),
             tool_doc="world_prune — prune stale entities")
    assert "prune stale entities" in adapter.calls[0]["prompt"]


def test_an_unverified_tool_gets_no_endorsement():
    """propose_action audits a host-supplied intent against no registry.
    Claiming it was verified there would be a lie the auditor acts on."""
    prompt = build_skeptic_prompt(_proposal(), facts="", mode="checklist")
    assert "registry" not in prompt


def test_the_pipeline_hands_the_skeptic_the_resolved_spec(tmp_path):
    """The registry entry, not the auditor's memory, decides what a tool is.

    End-to-end because the defect was in the wiring: the Skeptic could accept
    a doc all along, and nothing gave it one.
    """
    from conscio.agency.act import ActPipeline
    from conscio.agency.breaker import CircuitBreaker
    from conscio.agency.ledger import ActionLedger
    from conscio.agency.tools import Risk, ToolRegistry
    from conscio.context_manager import ConsciousnessState

    reg = ToolRegistry()
    reg.register("world_prune", lambda: "pruned 0", params={},
                 risk=Risk.MEDIUM,
                 description="prune entities the world model has let go stale")

    class _Bus:
        def emit(self, **kw):
            return 1

    auditor = MockAdapter(script=["A1: NO\nA2: NO\nA3: YES"])
    actor = MockAdapter(script=[('{"tool": "world_prune", "args": {}, '
                                 '"rationale": "r", "expected_outcome": "e"}')])
    ledger = ActionLedger(tmp_path / "conscio.db")
    bus = _Bus()
    pipe = ActPipeline(adapter=actor, registry=reg, ledger=ledger,
                       breaker=CircuitBreaker(ledger, bus),
                       emit_fn=bus.emit, skeptic=Skeptic(auditor))
    pipe.act(ConsciousnessState(state_summary="s",
                                active_goals=["prune stale entities"],
                                coherence_note="epistemic"))

    assert auditor.calls, "the audit never ran"
    sent = auditor.calls[0]["prompt"]
    assert "prune entities the world model has let go stale" in sent
    assert "registry" in sent
