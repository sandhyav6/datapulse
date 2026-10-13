#!/usr/bin/env bash
# Empty every object version in the pipeline bucket, then print the sam delete / IAM cleanup
# commands. Usage: scripts/teardown.sh [--yes] [--stack NAME] [--profile NAME]
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"
[ -x .venv/bin/python ] && PYTHON=.venv/bin/python
[ -x .venv/Scripts/python.exe ] && PYTHON=.venv/Scripts/python.exe
exec "$PYTHON" scripts/teardown.py "$@"
