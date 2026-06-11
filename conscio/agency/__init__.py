"""
Conscio Agency — the volition layer (v1.0.0 "Spine").

Stateless LLM orchestration downstream of engine.reflect():
contracts -> adapter -> gateway -> tools -> ledger -> breaker -> act pipeline.
Core stays zero-deps (stdlib + sqlite3); HTTP adapters use urllib only.
"""
from .act import ActPipeline, ActReport, ActStatus
from .adapter import (AdapterCaps, AdapterError, InferenceAdapter,
                      InferenceResult, MockAdapter)
from .adapters import LlamaCppAdapter, OllamaAdapter, OpenAICompatAdapter
from .breaker import DEFAULT_MAX_RETRIES, CircuitBreaker
from .contracts import ActionProposal, ToolResult, validate
from .gateway import GatewayError, OutputGateway
from .ledger import ActionLedger
from .tools import Risk, ToolRegistry, make_default_registry

__all__ = [
    "ActPipeline", "ActReport", "ActStatus", "AdapterCaps", "AdapterError",
    "InferenceAdapter", "InferenceResult", "MockAdapter", "LlamaCppAdapter",
    "OllamaAdapter", "OpenAICompatAdapter", "DEFAULT_MAX_RETRIES",
    "CircuitBreaker", "ActionProposal", "ToolResult", "validate",
    "GatewayError", "OutputGateway", "ActionLedger", "Risk", "ToolRegistry",
    "make_default_registry",
]
