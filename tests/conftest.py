import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Sibling hermes-agent checkout. Local dev uses the true sibling; CI checks
# it out inside the workspace (actions/checkout resolves `path:` against
# $GITHUB_WORKSPACE). Try both, env var first.
_HERMES_AGENT_CANDIDATES = [
    os.environ.get("HERMES_AGENT_SIBLING"),
    str(REPO_ROOT.parent / "hermes-agent"),
    str(REPO_ROOT / "hermes-agent"),
]
HERMES_AGENT_CHECKOUT = next(
    (Path(c) for c in _HERMES_AGENT_CANDIDATES if c and Path(c).is_dir()),
    REPO_ROOT.parent / "hermes-agent",
)


@pytest.fixture(scope="session", autouse=True)
def _hermes_agent_on_path():
    """Make `import hermes_cli` resolve without per-test path surgery."""
    if HERMES_AGENT_CHECKOUT.is_dir():
        sys.path.insert(0, str(HERMES_AGENT_CHECKOUT))
    yield


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
