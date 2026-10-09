"""Robustez: the ported patterns library (v4.9, Jade catalog item 13).

Openjev + hindsight half (this package): decision, logitjev,
retractions, deltaops, consolidation. The CopilotKit/foreman half
(secrets, history, retry, ingest, untrusted, normalize, singleflight,
approval, ssrf, outcomes_ext, toolwrap, crash, verifier, arbiter,
stateledger, llmretry) lives in the same package when the other
executor lands it. Stdlib only, docstrings cite the origin.
"""
from . import consolidation, decision, deltaops, logitjev, retractions

__all__ = ["consolidation", "decision", "deltaops", "logitjev", "retractions"]
