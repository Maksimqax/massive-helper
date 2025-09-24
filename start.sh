#!/usr/bin/env bash
set -e
# Render launches this script
exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}
