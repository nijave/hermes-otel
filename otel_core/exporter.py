"""Private TracerProvider + OTLP/HTTP batching exporter. Fail-open, lazy, non-global."""
from __future__ import annotations

import logging
from typing import Any, Callable

from otel_core.settings import Settings

logger = logging.getLogger("hermes_plugins.hermes_otel")


def _build_sdk_provider(settings: Settings) -> tuple[Callable[..., Any], Any]:
    """Returns (make_otlp_exporter_fn, provider). Raises on missing SDK/bad config."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    resource = Resource.create(
        {"service.name": settings.service_name, **settings.resource_attributes}
    )
    provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(TraceIdRatioBased(settings.sample_rate)),
    )

    def make(endpoint: str, headers: dict[str, str]) -> OTLPSpanExporter:
        return OTLPSpanExporter(endpoint=endpoint, headers=headers)

    return make, provider


def _make_batch_processor(exporter_obj: Any) -> Any:
    """Wrap a span exporter in a BatchSpanProcessor with the pinned batch kwargs."""
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    return BatchSpanProcessor(
        exporter_obj,
        schedule_delay_millis=5000,
        max_queue_size=2048,
        max_export_batch_size=512,
        export_timeout_millis=30000,
    )


def build_tracer(state_holder: Any, settings: Settings) -> Any:
    if getattr(state_holder, "_init_failed", False):
        return None
    if getattr(state_holder, "tracer", None) is not None:
        return state_holder.tracer
    if not settings.endpoint:
        state_holder.tracer = None
        return None
    try:
        make_exporter, provider = _build_sdk_provider(settings)
        exporter_obj = make_exporter(settings.endpoint, settings.resolve_headers())
        provider.add_span_processor(_make_batch_processor(exporter_obj))
        tracer = provider.get_tracer("hermes-otel")
        state_holder.provider = provider
        state_holder.tracer = tracer
        return tracer
    except Exception as exc:  # noqa: BLE001 - fail-open contract
        logger.warning("hermes-otel: telemetry disabled (%s)", exc)
        state_holder._init_failed = True
        state_holder.tracer = None
        return None


def flush_shutdown(state_holder: Any, timeout_s: float = 5.0) -> None:
    provider = getattr(state_holder, "provider", None)
    if provider is None:
        return
    try:
        provider.force_flush(timeout_millis=int(timeout_s * 1000))
    except Exception as exc:  # noqa: BLE001
        logger.warning("hermes-otel: flush failed (%s)", exc)
    try:
        provider.shutdown()
    except Exception as exc:  # noqa: BLE001
        logger.warning("hermes-otel: shutdown failed (%s)", exc)
    state_holder.provider = None
    state_holder.tracer = None
