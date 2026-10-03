#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if command -v uv >/dev/null && [[ "$(uv --version)" == "uv 0.12.19"* ]]; then
  JUDICIAL_UV="$(command -v uv)"
else
  python3 -m venv .bootstrap
  .bootstrap/bin/python -m pip install --disable-pip-version-check 'uv==0.12.19'
  JUDICIAL_UV="$PWD/.bootstrap/bin/uv"
fi

# Preserve the managed environment's proxy and system CA trust.
export UV_SYSTEM_CERTS=true
# Select the project Python without replacing the user's system interpreter.
# uv downloads Python 3.12 if no compatible interpreter is available locally.
"$JUDICIAL_UV" sync --frozen --python 3.12
mkdir -p data/raw data/derived artifacts local
.venv/bin/judicial-kg doctor
