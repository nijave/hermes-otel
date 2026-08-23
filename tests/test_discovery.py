"""The plugin must be discovered and registered through the REAL PluginManager."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[1]
HERMES_AGENT_CHECKOUT = REPO_ROOT.parent / "hermes-agent"


def test_plugin_discovered_and_registered(plugin_env):
    sys.path.insert(0, str(HERMES_AGENT_CHECKOUT))
    from hermes_cli.plugins import PluginManager

    pm = PluginManager()
    pm.discover_and_load()
    discovered = {entry["name"]: entry for entry in pm.list_plugins()}
    assert "hermes-otel" in discovered

    mod = sys.modules.get("hermes_plugins.hermes_otel")
    assert mod is not None, "plugin module should be imported as namespaced module"
    assert mod._REGISTERED is True
