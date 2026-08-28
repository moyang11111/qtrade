"""QTrade-owned optional DeepSeek chat service."""

from .context import (
    APPROVED_OPPORTUNITY_CATEGORIES,
    CONTEXT_SCHEMA_VERSION,
    ContextError,
    ContextProvider,
    build_context,
    serialize_context,
    validate_context,
)
from .service import (
    DeepSeekChatError,
    DeepSeekChatService,
    DeepSeekTransport,
    TransportResponse,
    UrllibTransport,
)

__all__ = [
    "APPROVED_OPPORTUNITY_CATEGORIES",
    "CONTEXT_SCHEMA_VERSION",
    "ContextError",
    "ContextProvider",
    "DeepSeekChatError",
    "DeepSeekChatService",
    "DeepSeekTransport",
    "TransportResponse",
    "UrllibTransport",
    "build_context",
    "serialize_context",
    "validate_context",
]
