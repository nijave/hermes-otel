import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def plugin_env(tmp_path, monkeypatch):
    """Temp HERMES_HOME with this repo symlinked in as an enabled plugin."""
    home = tmp_path / "hermes"
    (home / "plugins").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    target = home / "plugins" / "hermes-otel"
    try:
        target.symlink_to(REPO_ROOT, target_is_directory=True)
    except OSError:
        import shutil

        shutil.copytree(
            REPO_ROOT,
            target,
            ignore=shutil.ignore_patterns(
                ".git", ".venv", "__pycache__", ".pytest_cache"
            ),
        )
    (home / "config.yaml").write_text(
        "plugins:\n  enabled:\n    - hermes-otel\n", encoding="utf-8"
    )
    yield home


@pytest.fixture(autouse=True)
def _reset_plugin_state():
    yield
    mod = sys.modules.get("hermes_plugins.hermes_otel")
    if mod is not None:
        mod._REGISTERED = False
    for name in [
        n
        for n in sys.modules
        if n == "hermes_plugins" or n.startswith("hermes_plugins.")
    ]:
        del sys.modules[name]
    plugins_mod = sys.modules.get("hermes_cli.plugins")
    bare_scope = getattr(plugins_mod, "_BARE_MODULE_SCOPE", None)
    if bare_scope is not None:
        bare_scope.clear()
