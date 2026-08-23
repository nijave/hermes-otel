"""Session->turn->leaf span tree reconstruction keyed by hook correlation ids.

Explicit contexts everywhere (no ambient contextvars): hooks may fire on
different worker threads. Timestamps derive from payload started_at/ended_at.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import trace as otel_trace

from otel_core.attributes import (
    chat_error_attrs,
    chat_request_attrs,
    chat_response_attrs,
    session_root_attrs,
    tool_end_attrs,
    tool_start_attrs,
    turn_attrs,
)

logger = logging.getLogger("hermes_plugins.hermes_otel")

MAX_TREES = 256


def _isolated_context() -> Any:
    """Context with no current span: roots never attach to foreign traces."""
    return otel_trace.set_span_in_context(otel_trace.INVALID_SPAN)


def _ns(ts: Any) -> int | None:
    try:
        return int(float(ts) * 1e9)
    except (TypeError, ValueError):
        return None


def _now_ns() -> int:
    return time.time_ns()


@dataclass
class _Tree:
    root: Any
    context: Any
    turns: dict[str, Any] = field(default_factory=dict)
    chats: dict[str, tuple[Any, dict[str, Any]]] = field(default_factory=dict)
    tools: dict[str, Any] = field(default_factory=dict)
    last_seen: float = field(default_factory=time.time)


class TraceState:
    # Documented seam: plugin layer assigns instance attr from settings
    # (state._capture_mode = settings.capture_mode); tests may set directly.
    _capture_mode: str = "none"

    def __init__(self, tracer) -> None:
        self._tracer = tracer
        self._trees: OrderedDict[str, _Tree] = OrderedDict()
        self._lock = threading.Lock()
        self.evicted_warnings = 0

    # -- internal ---------------------------------------------------------
    def _tree(self, session_id: str, platform: str | None = None) -> _Tree:
        evicted: list[tuple[str, _Tree]] = []
        with self._lock:
            tree = self._trees.get(session_id)
            if tree is None:
                span = self._tracer.start_span(
                    "invoke_agent hermes",
                    context=_isolated_context(),
                    attributes=session_root_attrs(
                        {"session_id": session_id, "platform": platform}
                    ),
                    start_time=_now_ns(),
                )
                ctx = otel_trace.set_span_in_context(span)
                tree = _Tree(root=span, context=ctx)
                tree.last_seen = time.time()
                self._trees[session_id] = tree
                while len(self._trees) > MAX_TREES:
                    evicted.append(self._trees.popitem(last=False))
            else:
                tree.last_seen = time.time()
                self._trees.move_to_end(session_id)
        for old_sid, old_tree in evicted:
            n_unclosed = (
                1 + len(old_tree.turns) + len(old_tree.chats) + len(old_tree.tools)
            )
            end = _now_ns()
            for chat_span, _kw in old_tree.chats.values():
                chat_span.end(end_time=end)
            for turn_span in old_tree.turns.values():
                turn_span.end(end_time=end)
            for tool_span in old_tree.tools.values():
                tool_span.end(end_time=end)
            old_tree.root.end(end_time=end)
            self.evicted_warnings += 1
            logger.warning(
                "hermes-otel: evicted incomplete session trace %s (%d unclosed spans)",
                old_sid,
                n_unclosed,
            )
        return tree

    def _turn(self, tree: _Tree, kw: dict[str, Any]) -> Any:
        turn_id = kw.get("turn_id") or "unknown-turn"
        span = tree.turns.get(turn_id)
        if span is None:
            span = self._tracer.start_span(
                f"invoke_agent {kw.get('model') or 'unknown'}",
                context=tree.context,
                attributes=turn_attrs(kw),
                start_time=_ns(kw.get("started_at")) or _now_ns(),
            )
            tree.turns[turn_id] = span
        return span

    def _emit_orphan_tool_span(self, kw: dict[str, Any], tree: _Tree | None) -> None:
        start = _ns(kw.get("started_at")) or _now_ns()
        end = _ns(kw.get("ended_at"))
        if end is None:
            duration_ms = kw.get("duration_ms")
            end = start + int(float(duration_ms) * 1e6) if duration_ms else _now_ns()
        span = self._tracer.start_span(
            f"execute_tool {kw.get('tool_name') or 'unknown'}",
            context=tree.context if tree else _isolated_context(),
            attributes={
                **tool_start_attrs(kw, self._capture_mode),
                **tool_end_attrs(kw, self._capture_mode),
            },
            start_time=start,
        )
        if kw.get("error_type"):
            span.set_status(otel_trace.Status(otel_trace.StatusCode.ERROR))
        span.end(end_time=end)

    # -- hook handlers -----------------------------------------------------
    def ensure_root(self, session_id: str, platform: str | None = None):
        """Lazily create (or fetch) the invoke_agent hermes root; returns the Span."""
        return self._tree(session_id, platform).root

    def on_pre_llm_call(self, kw: dict[str, Any]) -> None:
        self._tree(kw.get("session_id") or "", kw.get("platform"))

    def on_pre_api_request(self, kw: dict[str, Any]) -> None:
        tree = self._tree(kw.get("session_id") or "", kw.get("platform"))
        parent = self._turn(tree, kw)
        span = self._tracer.start_span(
            f"chat {kw.get('model') or 'unknown'}",
            context=otel_trace.set_span_in_context(parent),
            attributes=chat_request_attrs(kw, self._capture_mode),
            start_time=_ns(kw.get("started_at")) or _now_ns(),
        )
        tree.chats[kw.get("api_request_id") or ""] = (span, kw)

    def on_post_api_request(self, kw: dict[str, Any]) -> None:
        with self._lock:
            tree = self._trees.get(kw.get("session_id") or "")
            entry = tree.chats.pop(kw.get("api_request_id") or "", None) if tree else None
        if entry is None:
            return  # closed elsewhere (evicted/error) or never opened here
        span, open_kw = entry
        merged = {**open_kw, **{k: v for k, v in kw.items() if v is not None}}
        for k, v in chat_response_attrs(merged, self._capture_mode).items():
            span.set_attribute(k, v)
        span.end(end_time=_ns(kw.get("ended_at")) or _now_ns())

    def on_api_request_error(self, kw: dict[str, Any]) -> None:
        with self._lock:
            tree = self._trees.get(kw.get("session_id") or "")
            entry = tree.chats.pop(kw.get("api_request_id") or "", None) if tree else None
        if entry is None:
            return
        span, open_kw = entry
        merged = {**open_kw, **{k: v for k, v in kw.items() if v is not None}}
        for k, v in chat_error_attrs(merged, self._capture_mode).items():
            span.set_attribute(k, v)
        error = kw.get("error") or {}
        span.record_exception(Exception(str(error.get("type") or "error")))
        span.set_status(otel_trace.Status(otel_trace.StatusCode.ERROR))
        span.end(end_time=_ns(kw.get("ended_at")) or _now_ns())

    def on_pre_tool_call(self, kw: dict[str, Any]) -> None:
        tree = self._trees.get(kw.get("session_id") or "")
        if tree is None:
            return
        parent = tree.turns.get(kw.get("turn_id") or "") or tree.root
        span = self._tracer.start_span(
            f"execute_tool {kw.get('tool_name') or 'unknown'}",
            context=otel_trace.set_span_in_context(parent),
            attributes=tool_start_attrs(kw, self._capture_mode),
            start_time=_now_ns(),
        )
        tree.tools[kw.get("tool_call_id") or ""] = span

    def on_post_tool_call(self, kw: dict[str, Any]) -> None:
        with self._lock:
            tree = self._trees.get(kw.get("session_id") or "")
            span = tree.tools.pop(kw.get("tool_call_id") or "", None) if tree else None
        if span is None:
            # Late result after eviction/finalize, or pre-call never seen here:
            # emit a standalone completed span so the activity is not lost.
            self._emit_orphan_tool_span(kw, tree)
            return
        for k, v in tool_end_attrs(kw, self._capture_mode).items():
            span.set_attribute(k, v)
        if kw.get("error_type"):
            span.set_status(otel_trace.Status(otel_trace.StatusCode.ERROR))
        span.end(end_time=_now_ns())

    def on_subagent_start(self, kw: dict[str, Any]) -> None:
        pass  # v1: child activity emits its own hooks under the child session id

    def on_subagent_stop(self, kw: dict[str, Any]) -> None:
        pass

    def finalize_session(
        self, session_id: str, completed: bool = True, reason: str = ""
    ) -> None:
        with self._lock:
            tree = self._trees.pop(session_id, None)
        if tree is None:
            return
        end = _now_ns()
        for chat_span, _kw in tree.chats.values():  # dangling requests (interrupted turns)
            chat_span.end(end_time=end)
        for turn_span in tree.turns.values():
            turn_span.end(end_time=end)
        for tool_span in tree.tools.values():
            tool_span.end(end_time=end)
        tree.root.set_attribute("hermes.completed", bool(completed))
        if reason:
            tree.root.set_attribute("hermes.session_end_reason", reason)
        tree.root.end(end_time=end)
