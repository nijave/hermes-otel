"""hermes-otel: OpenTelemetry tracing plugin for Hermes Agent."""
from typing import Any

_REGISTERED = False


def register(ctx: Any) -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    _REGISTERED = True
