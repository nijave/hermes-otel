"""The plugin must be discovered and registered through the REAL PluginManager."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("yaml")

def test_plugin_discovered_and_registered(plugin_env):
    from hermes_cli.plugins import PluginManager

    pm = PluginManager()
    pm.discover_and_load()
    discovered = {entry["name"]: entry for entry in pm.list_plugins()}
    assert "hermes-otel" in discovered

    mod = sys.modules.get("hermes_plugins.hermes_otel")
    assert mod is not None, "plugin module should be imported as namespaced module"
    assert mod._REGISTERED is True
