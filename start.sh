#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import fastapi, uvicorn, imageio_ffmpeg' 2>/dev/null; then
  .venv/bin/python -m pip install -r requirements.txt
fi
if [ ! -f web/dist/index.html ]; then
  (cd web && npm install && npm run build)
fi
exec .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port "${VIDEO_AGENT_PORT:-8765}"
