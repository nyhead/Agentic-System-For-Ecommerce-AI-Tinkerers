#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
uv run --no-project --python 3.12 scripts/check-project.py
uv sync --locked --group dev
uv run --frozen ruff check .
uv run --frozen mypy app
uv run --frozen pytest -q
docker build --tag commercecrew:local .
bash scripts/smoke-container.sh commercecrew:local
