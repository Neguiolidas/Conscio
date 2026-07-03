"""
Conscio — A self-awareness framework for AI agents.

Enables emergent consciousness through context-aware memory, introspection,
and goal generation. Adapts behavior based on the underlying model's
context window size.
"""

__version__ = "2.11.1"
__author__ = "Neguiolidas / Neguitech"

from .engine import ConsciousnessEngine
from .context_manager import ContextManager, ContextMode
from .models import ModelRegistry
from .content_store import ContentStore
from .event_bus import EventBus
from .output_filter import FilterPipeline, build_pipeline_from_dict
from .token_tracker import TokenTracker
from .migrate import Migrator
from .session_lifecycle import SessionSummary, record_session_lifecycle
from .metabolic import MetabolicContext, MetabolicState
from .dreaming import DreamCycle, DreamReport
from .agency import MockAdapter, OllamaAdapter, LlamaCppAdapter, \
    OpenAICompatAdapter, OpenAIAdapter, AnthropicAdapter, GeminiAdapter  # noqa: F401
from .risk import Risk
from .perception import SensorAdapter, PerceptionFrame, MockSensor, \
    HostSensor, AgentSensor  # noqa: F401
from .workspace import Workspace, WorkspaceContext, EnvClass  # noqa: F401
from .structural import StructuralDistiller, StructuralSignal, \
    Hyperedge, CommunitySummary, GraphNode, StructuralError  # noqa: F401
from .structural_consent import ConsentScope, StructuralConsent, \
    sync_structure  # noqa: F401
from .structural_drift import StructuralDigest, StructuralDelta, \
    StructuralFreshness, StructuralDriftStore, compute_delta, \
    compute_freshness, read_head_commit, drift_path  # noqa: F401
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
]
