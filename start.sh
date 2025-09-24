#!/usr/bin/env bash
set -e
# Render provides PORT; default to 10000 for local
PORT=${PORT:-10000}
exec uvicorn app.main:app --host 0.0.0.0 --port $PORT
