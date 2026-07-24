"""
Conscio — A self-awareness framework for AI agents.

Enables emergent consciousness through context-aware memory, introspection,
and goal generation. Adapts behavior based on the underlying model's
context window size.
"""

__version__ = "3.3.1"
__author__ = "Neguiolidas / Neguitech"

from .agency import (
    AnthropicAdapter,
    GeminiAdapter,
    LlamaCppAdapter,
    MockAdapter,
    OllamaAdapter,
    OpenAIAdapter,
    OpenAICompatAdapter,  # noqa: F401
)
from .content_store import ContentStore
from .context_manager import ContextManager, ContextMode
from .dedup import Deduplicator  # noqa: F401
from .diagnostics import (  # noqa: F401
    EVAL_BENCHMARK,
    EVAL_CAPABILITY,
    EVAL_REGRESSION,
    context_budget,
    eval_harness,
    rules_distill,
)
from .dreaming import DreamCycle, DreamReport
from .embedding import EmbeddingProvider  # noqa: F401
from .engine import ConsciousnessEngine
from .entity_detector import EntityDetector  # noqa: F401
from .evaluation import AxisScore, EvaluationReport, evaluate  # noqa: F401
from .event_bus import EventBus
from .gates import (  # noqa: F401
    ADR_VALID_STATUSES,
    COUNCIL_ROLES,
    COUNCIL_VOTES,
    council,
    decide,
    delivery_check,
    investigate,
    loop_gate,
)
from .hallways import Hallways  # noqa: F401

# ── v3.2 Memory modules ──
from .kg import KnowledgeGraph  # noqa: F401
from .metabolic import MetabolicContext, MetabolicState
from .migrate import Migrator
from .migration import (  # noqa: F401
    export_archive,
    import_archive,
    import_format_mempalace,
)
from .miner import Miner  # noqa: F401
from .models import ModelRegistry
from .output_filter import FilterPipeline, build_pipeline_from_dict
from .perception import (
    AgentSensor,
    HostSensor,  # noqa: F401
    MockSensor,
    PerceptionFrame,
    SensorAdapter,
)
from .pipelines import (  # noqa: F401
    LOOP_PATTERNS,
    PROMOTION_GATES,
    acceptance_criteria,
    continuous_loop,
    ledger,
    strategic_compact,
    verify,
)
from .risk import Risk
from .session_lifecycle import SessionSummary, record_session_lifecycle
from .structural import (
    CommunitySummary,
    GraphNode,
    Hyperedge,  # noqa: F401
    StructuralDistiller,
    StructuralError,
    StructuralSignal,
)
from .structural_consent import (
    ConsentScope,
    StructuralConsent,
    sync_structure,  # noqa: F401
)
from .structural_drift import (
    StructuralDelta,
    StructuralDigest,
    StructuralDriftStore,
    StructuralFreshness,
    compute_delta,
    compute_freshness,  # noqa: F401
    drift_path,
    read_head_commit,
)
from .token_tracker import TokenTracker
from .vector_backend import VectorBackend  # noqa: F401
from .wings import WingManager  # noqa: F401
from .workspace import EnvClass, Workspace, WorkspaceContext  # noqa: F401

# Plugin discovery lives under `conscio.plugins` (discover_adapters/sensors/tools)
# — kept out of the top-level namespace to keep this import light.

# Note: SessionRAG is intentionally NOT imported here — it depends on numpy
# and probes Ollama. Use the shared factory (`from conscio.session_rag_factory
# import create_session_rag`) for lazy, graceful construction, or import
# SessionRAG directly when you know it's available.

__all__ = [
 "ConsciousnessEngine",
 "ContextManager",
 "ContextMode",
 "ModelRegistry",
 "ContentStore",
 "EventBus",
 "FilterPipeline",
 "build_pipeline_from_dict",
 "TokenTracker",
 "Migrator",
 "SessionSummary",
 "record_session_lifecycle",
 "MetabolicContext",
 "MetabolicState",
 "DreamCycle",
 "DreamReport",
 "MockAdapter",
 "OllamaAdapter",
 "LlamaCppAdapter",
 "OpenAICompatAdapter",
 "OpenAIAdapter",
 "AnthropicAdapter",
 "GeminiAdapter",
 "Risk",
 "SensorAdapter",
 "HostSensor",
 "AgentSensor",
 "Workspace",
 "WorkspaceContext",
 "EnvClass",
 "PerceptionFrame",
 "MockSensor",
 "StructuralDistiller",
 "StructuralSignal",
 "Hyperedge",
 "CommunitySummary",
 "GraphNode",
 "StructuralError",
 "ConsentScope",
 "StructuralConsent",
 "sync_structure",
 "StructuralDigest",
 "StructuralDelta",
 "StructuralFreshness",
 "StructuralDriftStore",
 "compute_delta",
 "compute_freshness",
 "read_head_commit",
 "drift_path",
 # v2.15 — self-evaluation rubric (ECC agent-self-evaluation mapping)
 "evaluate",
 "EvaluationReport",
 "AxisScore",
 # v3.2 — memory modules (autocontido)
 "KnowledgeGraph",
 "Hallways",
 "WingManager",
 "VectorBackend",
 "Deduplicator",
 "EntityDetector",
 "EmbeddingProvider",
 "Miner",
 "export_archive",
 "import_archive",
 "import_format_mempalace",
]
