"""
Conscio — A self-awareness framework for AI agents.

Enables emergent consciousness through context-aware memory, introspection,
and goal generation. Adapts behavior based on the underlying model's
context window size.
"""

__version__ = "1.4.0"
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
    OpenAICompatAdapter, AnthropicAdapter, GeminiAdapter  # noqa: F401
from .risk import Risk
from .perception import SensorAdapter, PerceptionFrame, MockSensor
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
 "AnthropicAdapter",
 "GeminiAdapter",
 "Risk",
 "SensorAdapter",
 "PerceptionFrame",
 "MockSensor",
]
