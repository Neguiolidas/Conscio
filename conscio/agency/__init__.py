"""
Conscio Agency — the volition layer (v1.0.0 "Spine" + "Immunity" +
"Volition", v1.1 "Procedural").

Stateless LLM orchestration downstream of engine.reflect():
contracts -> adapter -> gateway (T1 GBNF/T2 JSON/T3 KV) -> tools ->
skeptic/trust -> ledger -> breaker -> act pipeline -> arbiter/loop.
Capabilities are measured (ProbeSuite), never assumed.
Core stays zero-deps (stdlib + sqlite3); HTTP adapters use urllib only.
"""
from .act import ActPipeline, ActReport, ActStatus
from .adapter import (
                      AdapterCaps,
                      AdapterError,
                      InferenceAdapter,
                      InferenceResult,
                      Meter,
                      MeteredAdapter,
                      MockAdapter,
)
from .adapters import (
                      AnthropicAdapter,
                      GeminiAdapter,
                      LlamaCppAdapter,
                      OllamaAdapter,
                      OpenAIAdapter,
                      OpenAICompatAdapter,
)
from .breaker import DEFAULT_MAX_RETRIES, CircuitBreaker
from .contracts import ActionProposal, AuditVerdict, ToolResult, validate
from .fingerprint import goal_fingerprint
from .gateway import GatewayError, OutputGateway
from .grammar import compile_proposal_grammar, compile_schema_grammar
from .ledger import ActionLedger
from .loop import DISSONANCE_HINTS, ActBudget, AutonomyLoop, GoalArbiter, RunReport
from .profiles import (
                      ModelProfile,
                      ProbeSuite,
                      choose_tier,
                      max_visible_tools,
                      skeptic_mode,
)
from .skeptic import Skeptic
from .skills import SkillLibrary
from .tools import Risk, ToolRegistry, make_default_registry
from .trust import TrustMatrix

__all__ = [
                      "DEFAULT_MAX_RETRIES",
                      "DISSONANCE_HINTS",
                      "ActBudget",
                      "ActPipeline",
                      "ActReport",
                      "ActStatus",
                      "ActionLedger",
                      "ActionProposal",
                      "AdapterCaps",
                      "AdapterError",
                      "AnthropicAdapter",
                      "AuditVerdict",
                      "AutonomyLoop",
                      "CircuitBreaker",
                      "GatewayError",
                      "GeminiAdapter",
                      "GoalArbiter",
                      "InferenceAdapter",
                      "InferenceResult",
                      "LlamaCppAdapter",
                      "Meter",
                      "MeteredAdapter",
                      "MockAdapter",
                      "ModelProfile",
                      "OllamaAdapter",
                      "OpenAIAdapter",
                      "OpenAICompatAdapter",
                      "OutputGateway",
                      "ProbeSuite",
                      "Risk",
                      "RunReport",
                      "Skeptic",
                      "SkillLibrary",
                      "ToolRegistry",
                      "ToolResult",
                      "TrustMatrix",
                      "choose_tier",
                      "compile_proposal_grammar",
                      "compile_schema_grammar",
                      "goal_fingerprint",
                      "make_default_registry",
                      "max_visible_tools",
                      "skeptic_mode",
                      "validate",
]
