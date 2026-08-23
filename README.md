# hermes-otel

OpenTelemetry OTLP tracing plugin for [Hermes Agent](../hermes-agent): traces agent
sessions and LLM calls and exports them over the OTLP/HTTP protocol. The authoritative
design and implementation spec lives at
`../hermes-agent/.superpowers/sdd/2026-08-23-hermes-otel-plugin/task-1-brief.md`
(same `.superpowers/sdd/2026-08-23-hermes-otel-plugin/` directory holds the per-task briefs).

## Status

Work in progress — repo scaffold, manifest, and discovery smoke test (Task 1 of 9).
The plugin registers no hooks yet; later tasks add OTel tracer setup, hook handlers,
and export configuration.
