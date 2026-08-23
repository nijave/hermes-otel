#!/usr/bin/env bash
# Symlink this repo into the Hermes user plugins directory (~/.hermes/plugins/).
# Idempotent: an existing correct symlink is left alone; a real directory or a
# wrong symlink at the target path is reported and left untouched.
set -euo pipefail

PLUGIN_SRC="$(cd "$(dirname "$0")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
TARGET="$HERMES_HOME/plugins/hermes-otel"

if [ ! -f "$PLUGIN_SRC/plugin.yaml" ] || [ ! -f "$PLUGIN_SRC/__init__.py" ]; then
    echo "error: $PLUGIN_SRC does not look like a hermes plugin directory" >&2
    exit 1
fi

if [ -L "$TARGET" ]; then
    current="$(readlink "$TARGET")"
    if [ "$current" = "$PLUGIN_SRC" ]; then
        echo "already installed: $TARGET -> $PLUGIN_SRC"
        exit 0
    fi
    echo "error: $TARGET is a symlink to $current (expected $PLUGIN_SRC); remove it and re-run" >&2
    exit 1
fi

if [ -e "$TARGET" ]; then
    echo "error: $TARGET already exists and is not a symlink; refusing to overwrite" >&2
    exit 1
fi

mkdir -p "$HERMES_HOME/plugins"
ln -s "$PLUGIN_SRC" "$TARGET"
echo "installed: $TARGET -> $PLUGIN_SRC"
echo "next: add 'hermes-otel' to plugins.enabled in your Hermes config.yaml"
