# DRAFT — Generic Observer Hooks Around Auxiliary LLM Calls

> **Status:** DRAFT — not submitted upstream. Written 2026-08-23. Maintained here as the
> motivating-consumer artifact; sections below are copy-paste ready for a GitHub issue if
> agreed with maintainers.
>
> **Motivating consumer:** this plugin (`hermes-otel`) and any future observability
> plugin wanting side-task visibility.

## Summary

Hermes' observer-hook contract (`docs/observability/README.md`,
`telemetry_schema_version="hermes.observer.v1"`) exposes every **agent-loop** LLM call via
`pre_api_request` / `post_api_request` / `api_request_error`. However, all **auxiliary
side-task** inference funnels through `agent/auxiliary_client.py::_call_llm_impl` and emits
no hooks at all (~5% of call volume). Telemetry consumers therefore cannot see:

| Aux consumer | Call site |
|---|---|
| Title generation | `agent/title_generator.py:403` |
| Vision enrichment | `tools/vision_tools.py`, gateway `_enrich_message_with_vision` |
| Compression digests / summaries / micro-summaries | `agent/context_compressor.py:4462/:4937/:6603` |
| MoA advisor fan-outs | `agent/moa_loop.py:583` |
| Approval auto-judge | `tools/approval.py:3391` |
| Browser page-read/vision | `tools/browser_tool.py:3204/:4825` |
| TTS text prep | `tools/tts_tool.py:2081/:2562` |
| MCP sampling | `tools/mcp_tool.py:2037/:2094` |
| Plugin `llm` facade | `agent/plugin_llm.py:1113/:1162` |
| Goals judge / kanban decompose/specify / cron classify | `hermes_cli/goals.py`, `kanban_decompose.py`, `cron/scripts/classify_items.py` |

## Proposal

Add one **observer-only** hook pair mirroring the existing request-scoped seam, fired from
the shared implementation so *every* listed consumer inherits it with zero per-consumer
changes.

### `pre_aux_llm_call`

Fired before dispatch inside `_call_llm_impl` (gated on `has_hook("pre_aux_llm_call")`).

```python
invoke_hook(
    "pre_aux_llm_call",
    session_id=...,          # owning agent session, if reachable
    turn_id=...,             # current turn, if mid-turn
    task="title_generation", # the existing task label ("vision", "compression", ...)
    model=..., provider=..., base_url=..., api_mode=...,
    request={"messages_meta": {"count": n, "approx_chars": c}},  # metadata only
    started_at=<time.time()>,
)
```

**Return value ignored.** Unlike `pre_llm_call`, this hook has NO context-injection
semantics — pure observation, cache-safe by construction.

### `post_aux_llm_call`

Fired after dispatch settles — success or failure — in one hook (outcome-carrying, keeping
surface minimal):

```python
invoke_hook(
    "post_aux_llm_call",
    session_id=..., turn_id=..., task=...,
    model=..., provider=..., base_url=..., api_mode=...,
    usage={                  # same canonical shape as _usage_summary_for_api_request_hook
        "input_tokens": ..., "output_tokens": ...,
        "cache_read_tokens": ..., "cache_write_tokens": ..., "reasoning_tokens": ...,
    },
    api_duration=<float seconds>, started_at=..., ended_at=...,
    ok=True,
    response_model=..., finish_reason=...,
)
# on failure: ok=False, error={"type": ..., "message": ...}  (message re-redactable;
# documented as potentially unredacted, matching VALID_HOOKS comment conventions)
```

## Design constraints honored

- **Footprint ladder rung 1** — extends an existing code path; two hook names, no new tool,
  config, or env surface. Zero cost when no plugin registers them (`has_hook` gate).
- **Compat contract** — payloads are additive keyword fields; callbacks signature-inspect
  (`**kwargs` gets everything, narrow signatures get declared names only);
  `telemetry_schema_version` auto-injected by `invoke_hook`.
- **Cache safety** — read-only; nothing mutates messages, toolsets, or system prompt.
- **Hot path** — aux calls already do validation/usage extraction
  (`_validate_llm_response`:8803); hook invocation is comparable overhead and skipped
  entirely when no subscriber exists.
- **No secrets** — payload carries metadata and canonical usage counts only; message bodies
  deliberately excluded (consumers wanting content can propose a follow-up opt-in field).

## Implementation sketch

Wrap the four physical relay paths so both sync/async/stream variants emit exactly once per
request: `_relay_sync_completion` (:3369), `_relay_sync_stream` (:3422),
`_relay_async_completion` (:3395), async branch of `_call_llm_impl` (:~10380) — or a single
wrapper inside `_call_llm_impl` (:9366) covering all relays, preferred (single choke point;
estimated diff small, contained to `agent/auxiliary_client.py`).

## Testing expectations

Behavior-contract tests through real plugin discovery: frozen fixture plugin registering the
hooks, driving `call_llm(task="title_generation", ...)` against a mock client in a temp
`HERMES_HOME`, asserting payload fields (task label, usage parity with canonical summary,
durations present, `ok` flag on injected error). No source greps, no count snapshots.

## Alternatives considered

1. **Runtime monkeypatching from plugins** — fragile against fast-moving core; rejected.
2. **Per-consumer hooks** — N surfaces instead of 1 choke point; rejected (footprint).
3. **Do nothing; plugins use contrib SDK instrumentors** — double-instruments agent-loop
   calls that already emit `pre/post_api_request`; rejected for duplication.
