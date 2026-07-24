#!/usr/bin/env bash
# Starts the FFB backend API and frontend dev server together.
set -e

cd "$(dirname "$0")/.."

cleanup() {
  echo "Stopping..."
  kill "$API_PID" "$WEB_PID" 2>/dev/null
}
trap cleanup EXIT INT TERM

(cd backend && uv run uvicorn ffb.api:app --reload --port 8010) &
API_PID=$!

(cd frontend && npm run dev) &
WEB_PID=$!

wait
