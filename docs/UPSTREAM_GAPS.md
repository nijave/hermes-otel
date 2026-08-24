# Upstream Hermes gaps affecting hermes-otel

This plugin consumes only the documented observer hooks
(`docs/observability/README.md`, schema `hermes.observer.v1`). The gaps below are
upstream limitations in hermes-agent that cap what any hook-based telemetry plugin
can see. Each item lists the gap, its effect on this plugin, and what would unlock it.

### 1. Auxiliary-funnel LLM calls emit no hooks

- **Gap:** all side-task inference funnels through `agent/auxiliary_client.py::_call_llm_impl`
  and fires no request-scoped hooks; `pre/post_api_request` + `api_request_error` fire only
  around the agent loop (`run_conversation`).
- **Effect on this plugin:** roughly 5% of call volume is invisible — title generation,
  vision enrichment, compression digests/summaries/micro-summaries, MoA advisor fan-outs,
  approval auto-judge, browser page-read/vision, TTS text prep, MCP sampling, plugin `llm`
  facade, goals judge, kanban decompose/specify, cron item classification.
- **Unlocks:** a generic observer pair around `_call_llm_impl`. A draft proposal is
  maintained in this repo at `docs/upstream-proposals/post-aux-llm-call-hook.md`.

### 2. No W3C trace-context propagation on outbound MCP HTTP calls

- **Gap:** the httpx client in `tools/mcp_tool.py` sends no traceparent headers, and there
  is no OTel SDK in the agent path.
- **Effect:** turns cannot be correlated with downstream MCP-server traces.
- **Refs:** NousResearch/hermes-agent issues #60177 and #52211.
- **Unlocks:** standard propagator injection at the MCP client; this plugin would then emit
  parent-linked child spans for MCP work automatically via context.

### 3. Plugin-doctor CLI ergonomics

- **Gap (verified 2026-08-23 against main):** `python -m hermes_cli.plugin_dev` has no
  `__main__` block (module invocation silently exits 0 without validating), and the doctor
  surface used by CI is the Python API `hermes_cli.plugin_dev.doctor_plugin()` returning a
  `DoctorReport` (`.ok`, `.findings`).
- **Effect:** docs elsewhere suggesting a `doctor` CLI form mislead; our CI runs the API
  directly.
- **Unlocks:** an actual CLI entry (`hermes plugins doctor <path> --ci`) that exits
  non-zero on findings.

### 4. Subagent hook payloads lack parent linkage depth

- **Gap:** `subagent_start`/`subagent_stop` carry child session id, role, goal, summary,
  duration_ms, tool history — but no parent api_request_id/span reference or propagated
  trace context.
- **Effect:** this plugin's subagent handlers are v1 no-ops; child activity still appears
  under its own session tree (children run their own AIAgent loop), but nested parent-child
  span trees cannot be drawn.
- **Unlocks:** a parent correlation id in the payload; the plugin maps children as spans
  under the parent root immediately.
