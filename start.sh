#!/usr/bin/env bash
# Render/Heroku style launcher
# Exposes FastAPI on $PORT (Render sets it). Defaults to 10000 for local.
export PORT="${PORT:-10000}"
# Run the app
python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
