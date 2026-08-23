# hermes-otel

OpenTelemetry OTLP tracing plugin for [Hermes Agent](../hermes-agent). It reconstructs
one canonical trace per agent session — `invoke_agent` root → turn spans →
`chat`/`execute_tool` children — from Hermes' observer hooks and exports them to any
OTLP/HTTP collector (Jaeger, Tempo, HyperDX, the OTel Collector, …).

No core changes. The plugin is inert until enabled in `config.yaml`, and stays in
metadata-only mode until you opt into content capture.

## What it traces

Each session becomes a span tree using official GenAI semantic conventions:

```text
Trace = session
invoke_agent hermes                          ← session root, created lazily on first event
└─ invoke_agent {model}                      ← per turn, keyed turn_id
   ├─ chat {model}                           ← pre_api_request opens, post_api_request closes
   │    keyed api_request_id                 ← api_request_error closes with ERROR status
   ├─ execute_tool {name}                    ← pre_tool_call opens, post_tool_call closes
   │    keyed tool_call_id
   └─ compact                                ← future: arrives when upstream aux hook lands
subagent_start/subagent_stop → nested invoke_agent child spans
on_session_finalize / on_session_end → close tree, flush
```

Key attributes per span:

| Span | Name | Key attributes |
|---|---|---|
| Session root | `invoke_agent hermes` | `gen_ai.agent.name=hermes`, `gen_ai.conversation.id=<session_id>`, `hermes.platform` |
| Turn | `invoke_agent {model}` | `gen_ai.operation.name=invoke_agent`, `gen_ai.request.model`, `hermes.turn_id`, `hermes.parent_session_id` |
| LLM call | `chat {model}` | `gen_ai.operation.name=chat`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.response.finish_reasons[]`, token usage attrs, `hermes.api_request_id` |
| Tool call | `execute_tool {name}` | `gen_ai.tool.name`, `gen_ai.tool.call.id`, `hermes.duration_ms` |
| Error leaf | same name as its opener | `status=ERROR`, re-redacted `error.type` / `error.message` |

Model names come from the wire payload (`request.body.model` / `response_model`),
never `agent.model`. Timestamps come from payload `started_at`/`ended_at`, not callback
wall-clock. Subagent handlers are v1 no-ops: child sessions emit their own session-scoped
trees through their own hooks.

## Install

The plugin ships as a directory drop — no pip install required at runtime.
Symlink (or copy) this repo into your Hermes plugins directory:

```bash
ln -s /path/to/hermes-otel ~/.hermes/plugins/hermes-otel
```

Or run the helper script, which does exactly that:

```bash
./scripts/dev_install.sh
```

Then add the plugin id to `plugins.enabled` in your `config.yaml` (see below).
Verify with:

```bash
hermes plugins list        # hermes-otel should appear as discovered/enabled
```

> **Note:** the `[project.entry-points."hermes_agent.plugins"]` declaration in
> `pyproject.toml` is forward-looking only. Installing via `pip install .` does **not**
> wire the plugin into discovery today — directory drop into `~/.hermes/plugins/` is
> the supported install path.

## Configure

Settings live under `plugins.entries.hermes-otel.settings` in your Hermes
`config.yaml`:

```yaml
plugins:
  enabled: [hermes-otel]              # consent boundary — plugin exports nothing without this
  entries:
    hermes-otel:
      settings:
        endpoint: http://localhost:4318/v1/traces    # required for export; empty = collect-nothing
        headers_env:                  # env-var NAMES, values resolved at export time
          authorization: OTEL_EXPORT_TOKEN
        capture_mode: none            # none | sanitized | full
        sample_rate: 1.0              # parent-based TraceIdRatioBased
        service_name: hermes-agent
        resource_attributes: {}       # extra OTel resource attrs passthrough
```

- `headers_env` maps header name → environment variable **name**; the value is read at
  export time and unset variables are skipped silently. Nothing sensitive lives in YAML.
  The manifest declares `OTEL_EXPORT_TOKEN` as a secret `requires_env` entry so
  `hermes plugins install` can prompt for it.
- Empty or missing `endpoint` means collect-nothing mode: hooks still fire but the
  plugin builds no exporter and stays quiet.

### Capture modes

| Mode | Behavior |
|---|---|
| `none` (default) | Metadata only: models, token counts, durations, finish reasons, tool names. No prompts, completions, or tool args/results ever leave the process. |
| `sanitized` | Additionally attaches the already-sanitized `request.body` messages and `response.assistant_message` from hook payloads (Hermes sanitizer: secret keys redacted, length/count caps applied). |
| `full` | Same sources, bypassing length caps — explicit opt-in for debugging. |

Regardless of mode, `error.message` payloads are re-redacted before entering any span,
and content is only ever read from the sanitized payload dicts.

## Verifying

Unit + integration tests (integration tests load the plugin through the real
`PluginManager` into a temp `HERMES_HOME`):

```bash
.venv/bin/python -m pytest -q     # or any python with pytest>=8 and pyyaml
```

End-to-end wire verification against a real OTel Collector with stdout assertions
(spins up docker compose; asserts exported spans carry the canonical
`invoke_agent`/`chat`/`execute_tool` names):

```bash
./scripts/e2e.sh
```

## Troubleshooting

- **Plugin doesn't load / nothing traced:** set `HERMES_PLUGINS_DEBUG=1` before starting
  Hermes to tee verbose plugin-discovery logs (at DEBUG) to stderr, then check that
  `~/.hermes/plugins/hermes-otel` resolves and that `plugins.enabled` contains
  `hermes-otel`.
- **`evicted incomplete session trace <id> (N unclosed spans)` warnings:** the registry
  keeps at most ~256 concurrent session trees (LRU). When a tree is evicted it is
  force-ended first, and each eviction logs this warning once. Seeing one occasionally
  means heavy session churn past the cap; seeing it repeatedly may indicate a missed
  end-event for a long-lived session. Either way the partial trace was still exported —
  memory stays bounded regardless.
- **Langfuse (or other OTel SDK users) coexisting:** this plugin creates a *private*
  `TracerProvider` and never calls `trace.set_tracer_provider`, so it does not touch the
  global provider. Langfuse's instrumentation and hermes-otel export independently
  through their own providers — no interference either direction.

## Development

```bash
python -m pytest -q              # full suite (29 tests)
./scripts/e2e.sh                 # docker-compose E2E against a real collector
```

Manifest validation via the Hermes plugin doctor (run from the sibling hermes-agent
checkout with its venv active):

```bash
PYTHONPATH=../hermes-agent python -c \
  "from hermes_cli.plugin_dev import doctor_plugin; doctor_plugin('/path/to/hermes-otel')"
```

Design spec: `../hermes-agent/docs/superpowers/specs/2026-08-23-hermes-otel-plugin-design.md`.
