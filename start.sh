#!/usr/bin/env bash
set -e
export PYTHONUNBUFFERED=1
# Render sets PORT. Use it, default 10000.
export PORT="${PORT:-10000}"
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
