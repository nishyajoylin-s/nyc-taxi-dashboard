#!/bin/bash
# Start NYC Taxi Dashboard — API (+ simulator thread) + Streamlit
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f ".venv/bin/activate" ]; then
  echo "ERROR: venv not found. Run: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

if [ ! -f "db/taxi.db" ]; then
  echo "ERROR: taxi.db not found. Run: python scripts/download_data.py && python scripts/load_data.py"
  exit 1
fi

source .venv/bin/activate

echo "Starting FastAPI + simulator on :8001 ..."
uvicorn api.main:app --port 8001 --log-level warning &
API_PID=$!

echo "Starting Streamlit dashboard on :8502 ..."
streamlit run dashboard/app.py --server.port 8502 --server.headless false &
DASH_PID=$!

cleanup() {
  echo ""
  echo "Shutting down ..."
  kill "$API_PID" "$DASH_PID" 2>/dev/null
  exit 0
}
trap cleanup SIGINT SIGTERM

echo ""
echo "  API:       http://localhost:8001"
echo "  Dashboard: http://localhost:8502"
echo ""
echo "Press Ctrl+C to stop."
wait
