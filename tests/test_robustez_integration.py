"""Sanity test verifying that all 21 robustez modules are available and cleanly exportable.

From the v4.9 audit:
from robustez import approval, arbiter, consolidation, crash, decision,
deltaops, history, ingest, llmretry, logitjev, normalize, outcomes_ext,
retractions, retry, secrets, singleflight, ssrf, stateledger, toolwrap,
untrusted, verifier
"""

from conscio import robustez
from conscio.robustez import (
    approval,
    arbiter,
    consolidation,
    crash,
    decision,
    deltaops,
    history,
    ingest,
    llmretry,
    logitjev,
    normalize,
    outcomes_ext,
    retractions,
    retry,
    secrets,
    singleflight,
    ssrf,
    stateledger,
    toolwrap,
    untrusted,
    verifier,
)


def test_robustez_all_21_modules_present():
    expected_modules = {
        "approval",
        "arbiter",
        "consolidation",
        "crash",
        "decision",
        "deltaops",
        "history",
        "ingest",
        "llmretry",
        "logitjev",
        "normalize",
        "outcomes_ext",
        "retractions",
        "retry",
        "secrets",
        "singleflight",
        "ssrf",
        "stateledger",
        "toolwrap",
        "untrusted",
        "verifier",
    }
    assert set(robustez.__all__) == expected_modules
    assert len(expected_modules) == 21

    # Verify each module object is present
    for mod_name in expected_modules:
        mod = getattr(robustez, mod_name)
        assert mod is not None, f"Module {mod_name} was None"
        assert hasattr(mod, "__doc__"), f"Module {mod_name} missing docstring"


def test_robustez_direct_imports():
    mods = [
        approval,
        arbiter,
        consolidation,
        crash,
        decision,
        deltaops,
        history,
        ingest,
        llmretry,
        logitjev,
        normalize,
        outcomes_ext,
        retractions,
        retry,
        secrets,
        singleflight,
        ssrf,
        stateledger,
        toolwrap,
        untrusted,
        verifier,
    ]
    assert len(mods) == 21
    assert all(m is not None for m in mods)
