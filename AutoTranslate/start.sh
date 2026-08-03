#!/usr/bin/env bash
# Launch AutoTranslate. Any arguments are passed straight through, e.g.
#   ./start.sh --cli --folder ~/pages --model llama3.2-vision
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

if [ -x ".venv/bin/python" ]; then
    PY="./.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
else
    echo "Python 3 was not found. Run ./install.sh first." >&2
    exit 1
fi

exec "$PY" main.py "$@"
