"""
Conscio — A self-awareness framework for AI agents.

Enables emergent consciousness through context-aware memory, introspection,
and goal generation. Adapts behavior based on the underlying model's
context window size.
"""

__version__ = "0.7.0"
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

# Note: SessionRAG is intentionally NOT imported here — it depends on numpy
# and probes Ollama. Import it lazily (`from conscio.session_rag import
# SessionRAG`) so `import conscio` stays dependency-light and RAG stays optional.

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
]
