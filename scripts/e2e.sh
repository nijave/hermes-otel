#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p out
exec docker compose -f docker-compose.e2e.yml up --build --abort-on-container-exit --exit-code-from e2e
