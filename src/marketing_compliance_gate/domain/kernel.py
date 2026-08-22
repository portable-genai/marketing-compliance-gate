"""Vertical-neutral domain primitives shared by models, ports and adapters."""

from hex_service_kit import StrEnum


class ThinkingLevel(StrEnum):
    """Provider-neutral reasoning-effort vocabulary."""

    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


__all__ = ["ThinkingLevel"]
