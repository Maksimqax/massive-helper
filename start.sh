#!/usr/bin/env bash
set -e
export PYTHONUNBUFFERED=1
# Uvicorn will bind to $PORT provided by Render (default 10000)
exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}
