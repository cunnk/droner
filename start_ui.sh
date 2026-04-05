#!/usr/bin/env bash
# Start the drone fleet UI (backend + frontend)
# Run from the drones/ directory.

set -e

# ── Backend ───────────────────────────────────────────────────────────
echo "Starting FastAPI backend on :8000 ..."
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

# ── Frontend ──────────────────────────────────────────────────────────
echo "Installing npm deps (first run only) ..."
cd ui && npm install

echo "Starting Vite dev server on :5173 ..."
npm run dev &
FRONTEND_PID=$!

echo ""
echo "  Backend:  http://localhost:8000"
echo "  Frontend: http://localhost:5173"
echo ""
echo "Press Ctrl+C to stop both."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM
wait
