"""Plugin settings read from plugins.entries.hermes-otel.settings via ctx.get_config."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

_VALID_MODES = ("none", "sanitized", "full")


@dataclass
class Settings:
    endpoint: str = ""
    headers_env: dict[str, str] = field(default_factory=dict)
    capture_mode: str = "none"
    sample_rate: float = 1.0
    service_name: str = "hermes-agent"
    resource_attributes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_config(cls, get_config: Callable[[str, Any], Any]) -> "Settings":
        mode = str(get_config("capture_mode", "none") or "none")
        if mode not in _VALID_MODES:
            mode = "none"
        rate_raw = get_config("sample_rate", 1.0)
        try:
            rate = max(0.0, min(1.0, float(rate_raw)))
        except (TypeError, ValueError):
            rate = 1.0
        return cls(
            endpoint=str(get_config("endpoint", "") or ""),
            headers_env=dict(get_config("headers_env", {}) or {}),
            capture_mode=mode,
            sample_rate=rate,
            service_name=str(get_config("service_name", "hermes-agent") or "hermes-agent"),
            resource_attributes={
                str(k): str(v)
                for k, v in dict(get_config("resource_attributes", {}) or {}).items()
            },
        )

    def resolve_headers(self) -> dict[str, str]:
        """Map header name -> env var VALUE. Unset env vars are skipped silently."""
        out: dict[str, str] = {}
        for header, env_var in self.headers_env.items():
            val = os.environ.get(env_var)
            if val:
                out[header] = val
        return out
