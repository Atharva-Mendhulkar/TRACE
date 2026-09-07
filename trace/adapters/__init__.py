"""
TRACE Adapters Package.
"""

from trace.adapters.base import (
    FrameworkAdapter,
    RawTraceEvent,
    get_adapter,
    list_adapters,
    register_adapter,
)
import trace.adapters.mcp
import trace.adapters.langgraph
import trace.adapters.crewai
import trace.adapters.openai_agents
import trace.adapters.semantic_kernel
import trace.adapters.google_adk
import trace.adapters.autogen
import trace.adapters.swebench
import trace.adapters.osworld

__all__ = [
    "FrameworkAdapter",
    "RawTraceEvent",
    "get_adapter",
    "list_adapters",
    "register_adapter",
]
