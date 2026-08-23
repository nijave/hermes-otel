"""Docker-compose E2E: real plugin discovery -> wired hooks -> real OTel Collector.

Asserts the collector's debug exporter prints the expected span tree to
/out/spans.log (piped from the collector's stdout via `tee`).
"""

import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PLUGIN_DIR = Path("/plugin")
HERMES_HOME = Path("/tmp/hermes-home")
ENDPOINT = os.environ.get("OTEL_ENDPOINT", "http://otelcol:4318/v1/traces")
LOG_PATH = Path("/out/spans.log")


def write_config_once() -> None:
    """Write HERMES_HOME config with plugin enabled AND settings, before discovery."""
    (HERMES_HOME / "plugins").mkdir(parents=True, exist_ok=True)
    target = HERMES_HOME / "plugins" / "hermes-otel"
    if not target.exists():
        target.symlink_to(PLUGIN_DIR, target_is_directory=True)
    (HERMES_HOME / "config.yaml").write_text(
        "plugins:\n"
        "  enabled:\n"
        "    - hermes-otel\n"
        "  entries:\n"
        "    hermes-otel:\n"
        "      settings:\n"
        f"        endpoint: {ENDPOINT}\n"
        "        capture_mode: sanitized\n",
        encoding="utf-8",
    )


def wait_for_collector(deadline_s: float = 30.0) -> None:
    """Bounded retry against the OTLP HTTP endpoint (no healthcheck in compose)."""
    deadline = time.time() + deadline_s
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            urllib.request.urlopen(ENDPOINT, timeout=2)
            return  # any response means the port is serving
        except urllib.error.HTTPError:
            return  # 405 on GET /v1/traces is fine: collector is up
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(1)
    print(f"E2E FAIL: collector never became reachable at {ENDPOINT}: {last_err}")
    sys.exit(1)


os.environ.setdefault("HERMES_HOME", str(HERMES_HOME))
write_config_once()

sys.path.insert(0, "/hermes")

from hermes_cli.plugins import get_plugin_manager  # noqa: E402
from hermes_cli.lifecycle import invoke_hook  # noqa: E402

wait_for_collector()

pm = get_plugin_manager()
pm.discover_and_load()

PRE = {
    "session_id": "e2e-sess",
    "turn_id": "e2e-turn",
    "api_request_id": "e2e-turn:api:1",
    "model": "e2e-model",
    "provider": "e2e-provider",
    "api_mode": "chat_completions",
    "api_call_count": 1,
    "started_at": 1.0,
    "request": {"body": {"model": "e2e-model", "messages": [{"role": "user", "content": "ping"}]}},
}
invoke_hook("pre_api_request", **PRE)
invoke_hook(
    "pre_tool_call",
    session_id="e2e-sess",
    turn_id="e2e-turn",
    tool_call_id="c1",
    tool_name="terminal",
    args={},
)
invoke_hook(
    "post_tool_call",
    session_id="e2e-sess",
    turn_id="e2e-turn",
    tool_call_id="c1",
    tool_name="terminal",
    duration_ms=3,
    status="success",
)
invoke_hook(
    "post_api_request",
    **{
        **PRE,
        "ended_at": 2.0,
        "finish_reason": "stop",
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "response": {},
    },
)
# reason="shutdown" makes the plugin force-flush before reset.
invoke_hook("on_session_finalize", session_id="e2e-sess", platform="cli", reason="shutdown")

EXPECTED = ["invoke_agent hermes", "chat e2e-model", "execute_tool terminal"]
deadline = time.time() + 30
while time.time() < deadline:
    text = LOG_PATH.read_text(errors="replace") if LOG_PATH.exists() else ""
    if all(name in text for name in EXPECTED):
        print("E2E OK: all expected spans found in collector stdout")
        sys.exit(0)
    time.sleep(1)

print("E2E FAIL: missing spans; collector log tail:")
print(LOG_PATH.read_text(errors="replace")[-4000:] if LOG_PATH.exists() else "<no log>")
sys.exit(1)
