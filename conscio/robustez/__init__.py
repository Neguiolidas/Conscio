"""Robustez: the ported patterns library (v4.9.1+fixes).

22 módulos Python portados dos padrões CopilotKit, openjev, hindsight e foreman,
mais pipeline.py (a cola que conecta os 21 em 5 fluxos padrão).
Stdlib apenas, docstrings citando a origem.
"""

from . import (
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
    pipeline,
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

__all__ = [
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
    "pipeline",
    "retractions",
    "retry",
    "secrets",
    "singleflight",
    "ssrf",
    "stateledger",
    "toolwrap",
    "untrusted",
    "verifier",
]
