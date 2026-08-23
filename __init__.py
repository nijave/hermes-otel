"""hermes-otel: OpenTelemetry tracing plugin for Hermes Agent.

Thin hook callbacks -> otel_core.TraceState. O(1), never raises, no hot-path I/O.
"""
from __future__ import annotations

import atexit
import logging
import os
import sys
import threading
from typing import Any

# The plugin directory (repo root) must be importable so otel_core resolves
# when loaded via the PluginManager's namespaced module loader.
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from otel_core import exporter
from otel_core.settings import Settings
from otel_core.spans import TraceState

logger = logging.getLogger("hermes_plugins.hermes_otel")

_REGISTERED = False
_lock = threading.Lock()
_state: Any = None  # TraceState once initialized
_settings: Settings | None = None
_CONFIG_GETTER: Any = None  # ctx.get_config, assigned in register()
_init_failed = False
_atexit_registered = False

# Module holder state consumed by otel_core.exporter.build_tracer/flush_shutdown.
provider: Any = None
tracer: Any = None


def _module_self() -> Any:
    return sys.modules[__name__]


def _build_tracer(settings: Settings):
    """Single build seam; tests monkeypatch this to inject a tracer."""
    return exporter.build_tracer(_module_self(), settings)


def _atexit_flush() -> None:
    try:
        exporter.flush_shutdown(_module_self(), timeout_s=5.0)
    except Exception:  # noqa: BLE001 - shutdown must be silent
        pass


def _ensure_initialized() -> None:
    global _state, _settings, _init_failed, _atexit_registered
    if _state is not None or _init_failed or _CONFIG_GETTER is None:
        return
    with _lock:
        if _state is not None or _init_failed:
            return
        settings = Settings.from_config(_CONFIG_GETTER)
        # build_tracer() mutates this module's holder attrs:
        #   - success -> returns tracer, sets provider/tracer
        #   - no endpoint -> returns None, stays inert
        #   - SDK failure -> returns None, sets _init_failed
        tracer_obj = _build_tracer(settings)
        if tracer_obj is None:
            if settings.endpoint:
                _init_failed = True
            return  # collect-nothing mode: stay quiet and inert
        state = TraceState(tracer_obj)
        state._capture_mode = settings.capture_mode
        _state = state
        _settings = settings
        if not _atexit_registered:
            _atexit_registered = True
            atexit.register(_atexit_flush)


def _cb(fn):
    def inner(**kwargs: Any) -> None:
        try:
            _ensure_initialized()
            state = _state
            if state is not None:
                fn(state, kwargs)
        except Exception as exc:  # noqa: BLE001 - observer must never raise
            logger.debug("hermes-otel hook %s failed: %s", fn.__name__, exc)

    inner.__name__ = fn.__name__
    return inner


@_cb
def _pre_llm_call(state, kw):
    state.on_pre_llm_call(kw)


@_cb
def _pre_api_request(state, kw):
    state.on_pre_api_request(kw)


@_cb
def _post_api_request(state, kw):
    state.on_post_api_request(kw)


@_cb
def _api_request_error(state, kw):
    state.on_api_request_error(kw)


@_cb
def _pre_tool_call(state, kw):
    state.on_pre_tool_call(kw)


@_cb
def _post_tool_call(state, kw):
    state.on_post_tool_call(kw)


@_cb
def _subagent_start(state, kw):
    state.on_subagent_start(kw)


@_cb
def _subagent_stop(state, kw):
    state.on_subagent_stop(kw)


@_cb
def _on_session_finalize(state, kw):
    reason = str(kw.get("reason") or "")
    state.finalize_session(kw.get("session_id") or "", reason=reason)
    if reason == "shutdown":
        flush_and_reset()


@_cb
def _on_session_end(state, kw):
    state.finalize_session(
        kw.get("session_id") or "",
        completed=bool(kw.get("completed", True)),
        reason=str(kw.get("reason") or "end"),
    )


def flush_and_reset() -> None:
    """Flush + shutdown any live provider, then reset module state."""
    global _state, _settings, _init_failed, provider, tracer
    try:
        exporter.flush_shutdown(_module_self())
    except Exception:  # noqa: BLE001
        pass
    _state = None
    _settings = None
    _init_failed = False
    provider = None
    tracer = None


# Backwards-compatible alias used by the integration fixture teardown.
_shutdown_for_tests = flush_and_reset


def _reset_for_tests() -> None:
    global _state, _settings, _init_failed, provider, tracer
    _state = None
    _settings = None
    _init_failed = False
    provider = None
    tracer = None


def register(ctx: Any) -> None:
    global _REGISTERED, _CONFIG_GETTER
    if _REGISTERED:
        return
    _REGISTERED = True
    _CONFIG_GETTER = ctx.get_config
    for hook, cb in [
        ("pre_llm_call", _pre_llm_call),
        ("pre_api_request", _pre_api_request),
        ("post_api_request", _post_api_request),
        ("api_request_error", _api_request_error),
        ("pre_tool_call", _pre_tool_call),
        ("post_tool_call", _post_tool_call),
        ("subagent_start", _subagent_start),
        ("subagent_stop", _subagent_stop),
        ("on_session_finalize", _on_session_finalize),
        ("on_session_end", _on_session_end),
    ]:
        try:
            ctx.register_hook(hook, cb)
        except Exception:  # noqa: BLE001 - registration is best-effort
            logger.debug("register %s failed", hook)
