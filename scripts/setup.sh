#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

python3 -c 'import sys; assert sys.version_info[:2] == (3, 12), "Python 3.12 is required"'
if command -v uv >/dev/null && [[ "$(uv --version)" == "uv 0.12.19"* ]]; then
  JUDICIAL_UV="$(command -v uv)"
else
  python3 -m venv .bootstrap
  .bootstrap/bin/python -m pip install --disable-pip-version-check 'uv==0.12.19'
  JUDICIAL_UV="$PWD/.bootstrap/bin/uv"
fi

# Preserve the managed environment's proxy and system CA trust.
export UV_SYSTEM_CERTS=true
"$JUDICIAL_UV" sync --frozen --python "$(command -v python3)"
mkdir -p data/raw data/derived artifacts local
.venv/bin/judicial-kg doctor
