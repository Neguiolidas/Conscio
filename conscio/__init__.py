"""
Conscio — A self-awareness framework for AI agents.

Enables emergent consciousness through context-aware memory, introspection,
and goal generation. Adapts behavior based on the underlying model's
context window size.
"""

__version__ = "3.9.2"
__author__ = "Neguiolidas / Neguitech"

from .agency import (
    AnthropicAdapter,
    GeminiAdapter,
    LlamaCppAdapter,
    MockAdapter,
    OllamaAdapter,
    OpenAIAdapter,
    OpenAICompatAdapter,
)
from .content_store import ContentStore
from .context_manager import ContextManager, ContextMode
from .dedup import Deduplicator
from .diagnostics import (  # noqa: F401
    EVAL_BENCHMARK,
    EVAL_CAPABILITY,
    EVAL_REGRESSION,
    context_budget,
    eval_harness,
    rules_distill,
)
from .dreaming import DreamCycle, DreamReport
from .embedding import EmbeddingProvider
from .engine import ConsciousnessEngine
from .entity_detector import EntityDetector
from .evaluation import AxisScore, EvaluationReport, evaluate
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
from .hallways import Hallways

# ── v3.2 Memory modules ──
from .kg import KnowledgeGraph
from .metabolic import MetabolicContext, MetabolicState
from .migrate import Migrator
from .migration import (
    export_archive,
    import_archive,
    import_format_mempalace,
)
from .miner import Miner
from .models import ModelRegistry
from .output_filter import FilterPipeline, build_pipeline_from_dict
from .perception import (
    AgentSensor,
    HostSensor,
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
    Hyperedge,
    StructuralDistiller,
    StructuralError,
    StructuralSignal,
)
from .structural_consent import (
    ConsentScope,
    StructuralConsent,
    sync_structure,
)
from .structural_drift import (
    StructuralDelta,
    StructuralDigest,
    StructuralDriftStore,
    StructuralFreshness,
    compute_delta,
    compute_freshness,
    drift_path,
    read_head_commit,
)
from .token_tracker import TokenTracker
from .vector_backend import VectorBackend
from .wings import WingManager
from .workspace import EnvClass, Workspace, WorkspaceContext

# Plugin discovery lives under `conscio.plugins` (discover_adapters/sensors/tools)
# — kept out of the top-level namespace to keep this import light.

# Note: SessionRAG is intentionally NOT imported here — it depends on numpy
# and probes Ollama. Use the shared factory (`from conscio.session_rag_factory
# import create_session_rag`) for lazy, graceful construction, or import
# SessionRAG directly when you know it's available.

__all__ = [
    "AgentSensor",
    "AnthropicAdapter",
    "AxisScore",
    "CommunitySummary",
    "ConsciousnessEngine",
    "ConsentScope",
    "ContentStore",
    "ContextManager",
    "ContextMode",
    "Deduplicator",
    "DreamCycle",
    "DreamReport",
    "EmbeddingProvider",
    "EntityDetector",
    "EnvClass",
    "EvaluationReport",
    "EventBus",
    "FilterPipeline",
    "GeminiAdapter",
    "GraphNode",
    "Hallways",
    "HostSensor",
    "Hyperedge",
    # v3.2 — memory modules (autocontido)
    "KnowledgeGraph",
    "LlamaCppAdapter",
    "MetabolicContext",
    "MetabolicState",
    "Migrator",
    "Miner",
    "MockAdapter",
    "MockSensor",
    "ModelRegistry",
    "OllamaAdapter",
    "OpenAIAdapter",
    "OpenAICompatAdapter",
    "PerceptionFrame",
    "Risk",
    "SensorAdapter",
    "SessionSummary",
    "StructuralConsent",
    "StructuralDelta",
    "StructuralDigest",
    "StructuralDistiller",
    "StructuralDriftStore",
    "StructuralError",
    "StructuralFreshness",
    "StructuralSignal",
    "TokenTracker",
    "VectorBackend",
    "WingManager",
    "Workspace",
    "WorkspaceContext",
    "build_pipeline_from_dict",
    "compute_delta",
    "compute_freshness",
    "drift_path",
    # v2.15 — self-evaluation rubric (ECC agent-self-evaluation mapping)
    "evaluate",
    "export_archive",
    "import_archive",
    "import_format_mempalace",
    "read_head_commit",
    "record_session_lifecycle",
    "sync_structure",
]
