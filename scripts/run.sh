#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# Explicit checkout imports also work when macOS marks editable-install .pth files hidden.
export PYTHONPATH="$PWD/src"
exec .venv/bin/judicial-kg "$@"
