"""
Conscio — A self-awareness framework for AI agents.

Enables emergent consciousness through context-aware memory, introspection,
and goal generation. Adapts behavior based on the underlying model's
context window size.
"""

__version__ = "0.1.0"
__author__ = "Neguiolidas / Neguitech"

from .engine import ConsciousnessEngine
from .context_manager import ContextManager, ContextMode
from .models import ModelRegistry

__all__ = [
    "ConsciousnessEngine",
    "ContextManager",
    "ContextMode",
    "ModelRegistry",
]
