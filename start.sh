#!/usr/bin/env bash
# start.sh — Start the Junie Orchestrator server
# Activates the virtual environment and launches uvicorn on the port from .env.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Check that the virtual environment exists ─────────────────────────────────

if [ ! -d ".venv" ]; then
    echo "Error: Virtual environment (.venv) not found."
    echo "Run ./setup.sh first to create it."
    exit 1
fi

# ── Activate the virtual environment ──────────────────────────────────────────

source .venv/bin/activate

# ── Load .env to get PORT ─────────────────────────────────────────────────────

if [ -f ".env" ]; then
    # Source the .env file so variables like PORT are available
    set -a
    source .env
    set +a
fi

PORT="${PORT:-8000}"

echo "Starting JunieHub on port $PORT ..."
echo "Press Ctrl+C to stop."

# ── Start uvicorn ─────────────────────────────────────────────────────────────

exec uvicorn orchestrator:app --host 0.0.0.0 --port "$PORT"
