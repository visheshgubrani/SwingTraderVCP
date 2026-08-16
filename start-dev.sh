#!/usr/bin/env bash
# Start the local Swing Trader development stack from the repository root.
#
# Starts: Docker dependencies (Postgres and Redis), FastAPI, the arq worker,
# the tick-ingestion worker, the live order gateway when double-armed, and the
# Vite UI. Ctrl-C stops local application processes; Docker data services are
# intentionally left up.

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$ROOT_DIR/server"
CLIENT_DIR="$ROOT_DIR/client"
RUNTIME_DIR="$ROOT_DIR/.dev-runtime"

API_PORT=8000
UI_PORT=5173

API_PID_FILE="$RUNTIME_DIR/api.pid"
WORKER_PID_FILE="$RUNTIME_DIR/worker.pid"
TICK_WORKER_PID_FILE="$RUNTIME_DIR/tick-worker.pid"
ORDER_GATEWAY_PID_FILE="$RUNTIME_DIR/order-gateway.pid"
POSITION_MONITOR_PID_FILE="$RUNTIME_DIR/position-monitor.pid"
PROPOSAL_WORKER_PID_FILE="$RUNTIME_DIR/proposal-worker.pid"
ENTRY_SUPERVISOR_PID_FILE="$RUNTIME_DIR/entry-supervisor.pid"
UI_PID_FILE="$RUNTIME_DIR/ui.pid"

API_LOG="$RUNTIME_DIR/api.log"
WORKER_LOG="$RUNTIME_DIR/worker.log"
TICK_WORKER_LOG="$RUNTIME_DIR/tick-worker.log"
ORDER_GATEWAY_LOG="$RUNTIME_DIR/order-gateway.log"
POSITION_MONITOR_LOG="$RUNTIME_DIR/position-monitor.log"
PROPOSAL_WORKER_LOG="$RUNTIME_DIR/proposal-worker.log"
ENTRY_SUPERVISOR_LOG="$RUNTIME_DIR/entry-supervisor.log"
UI_LOG="$RUNTIME_DIR/ui.log"

declare -a PID_FILES=(
  "$API_PID_FILE"
  "$WORKER_PID_FILE"
  "$TICK_WORKER_PID_FILE"
  "$ORDER_GATEWAY_PID_FILE"
  "$POSITION_MONITOR_PID_FILE"
  "$PROPOSAL_WORKER_PID_FILE"
  "$ENTRY_SUPERVISOR_PID_FILE"
  "$UI_PID_FILE"
)

die() {
  echo "Error: $*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is required but was not found in PATH."
}

listener_pids() {
  lsof -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
}

terminate_pid() {
  local pid="$1"
  local label="$2"

  [[ "$pid" =~ ^[0-9]+$ ]] || return 0
  kill -0 "$pid" 2>/dev/null || return 0

  echo "Stopping $label (PID $pid)..."
  # Every process launched by this script gets its own session.  Terminating
  # the group also removes child processes such as Vite/uvicorn reloaders.
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true

  for _ in 1 2 3 4 5; do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 1
  done

  echo "$label did not stop gracefully; force stopping it."
  kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
}

stop_recorded_process() {
  local pid_file="$1"
  local label="$2"

  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(<"$pid_file")"
    terminate_pid "$pid" "$label"
    rm -f "$pid_file"
  fi
}

free_port() {
  local port="$1"
  local pids
  pids="$(listener_pids "$port")"

  [[ -n "$pids" ]] || return 0

  echo "Port $port is in use. Clearing existing listener(s): $pids"
  local pid
  while IFS= read -r pid; do
    terminate_pid "$pid" "port $port listener"
  done <<< "$pids"

  sleep 1
  pids="$(listener_pids "$port")"
  [[ -z "$pids" ]] || die "Port $port is still held by PID(s): $pids"
}

start_service() {
  local label="$1"
  local pid_file="$2"
  local log_file="$3"
  shift 3

  : > "$log_file"
  setsid "$@" >>"$log_file" 2>&1 &
  local pid=$!
  echo "$pid" > "$pid_file"
  echo "Started $label (PID $pid; log: ${log_file#$ROOT_DIR/})"
}

stop_services() {
  stop_recorded_process "$UI_PID_FILE" "UI"
  stop_recorded_process "$ORDER_GATEWAY_PID_FILE" "order gateway"
  stop_recorded_process "$POSITION_MONITOR_PID_FILE" "position monitor"
  stop_recorded_process "$ENTRY_SUPERVISOR_PID_FILE" "entry supervisor"
  stop_recorded_process "$PROPOSAL_WORKER_PID_FILE" "proposal worker"
  stop_recorded_process "$TICK_WORKER_PID_FILE" "tick worker"
  stop_recorded_process "$WORKER_PID_FILE" "arq worker"
  stop_recorded_process "$API_PID_FILE" "API"
}

show_status() {
  local pid_file label pid
  for pid_file in "${PID_FILES[@]}"; do
    case "$pid_file" in
      "$API_PID_FILE") label="API" ;;
      "$WORKER_PID_FILE") label="arq worker" ;;
      "$TICK_WORKER_PID_FILE") label="tick worker" ;;
      "$ORDER_GATEWAY_PID_FILE") label="order gateway" ;;
      "$POSITION_MONITOR_PID_FILE") label="position monitor" ;;
      "$PROPOSAL_WORKER_PID_FILE") label="proposal worker" ;;
      "$ENTRY_SUPERVISOR_PID_FILE") label="entry supervisor" ;;
      "$UI_PID_FILE") label="UI" ;;
    esac

    if [[ -f "$pid_file" ]]; then
      pid="$(<"$pid_file")"
      if kill -0 "$pid" 2>/dev/null; then
        echo "$label: running (PID $pid)"
      else
        echo "$label: stopped (stale PID file)"
      fi
    else
      echo "$label: stopped"
    fi
  done
}

cleanup_and_exit() {
  echo
  echo "Stopping local development services..."
  stop_services
  exit 0
}

main() {
  case "${1:-start}" in
    start)
      ;;
    stop)
      [[ -d "$RUNTIME_DIR" ]] || exit 0
      stop_services
      exit 0
      ;;
    status)
      [[ -d "$RUNTIME_DIR" ]] || {
        echo "No local development services are recorded as running."
        exit 0
      }
      show_status
      exit 0
      ;;
    *)
      die "Usage: ./start-dev.sh [start|stop|status]"
      ;;
  esac

  require_command docker
  require_command lsof
  require_command setsid
  require_command uv
  require_command pnpm

  mkdir -p "$RUNTIME_DIR"

  # First stop processes started by an earlier launcher run.  This prevents
  # duplicate no-port workers after an interrupted terminal session.
  stop_services

  # Deliberately clear only the two HTTP development ports.  Postgres (5480)
  # and Redis (6380) are data services and should never be killed blindly.
  free_port "$API_PORT"
  free_port "$UI_PORT"

  echo "Ensuring Postgres and Redis are running..."
  (cd "$ROOT_DIR" && docker compose -f docker-compose.dev.yml up -d --wait)

  start_service "API" "$API_PID_FILE" "$API_LOG" \
    bash -c 'cd "$1" && exec uv run uvicorn main:app --host 127.0.0.1 --port 8000 --reload' _ "$SERVER_DIR"
  start_service "arq worker" "$WORKER_PID_FILE" "$WORKER_LOG" \
    bash -c 'cd "$1" && exec uv run python run_worker.py' _ "$SERVER_DIR"
  start_service "tick worker" "$TICK_WORKER_PID_FILE" "$TICK_WORKER_LOG" \
    bash -c 'cd "$1" && exec uv run python -m app.workers.tick_worker' _ "$SERVER_DIR"
  start_service "position monitor" "$POSITION_MONITOR_PID_FILE" "$POSITION_MONITOR_LOG" \
    bash -c 'cd "$1" && exec uv run python -m app.workers.position_monitor' _ "$SERVER_DIR"
  start_service "proposal worker" "$PROPOSAL_WORKER_PID_FILE" "$PROPOSAL_WORKER_LOG" \
    bash -c 'cd "$1" && exec uv run python -m app.workers.proposal_worker' _ "$SERVER_DIR"
  start_service "entry supervisor" "$ENTRY_SUPERVISOR_PID_FILE" "$ENTRY_SUPERVISOR_LOG" \
    bash -c 'cd "$1" && exec uv run python -m app.workers.entry_supervisor' _ "$SERVER_DIR"
  ORDER_GATEWAY_ENABLED="$(
    cd "$SERVER_DIR"
    uv run python -c \
      'from app.config import settings; print(int(settings.execution_mode == "live" and settings.live_order_placement_enabled))'
  )"
  if [[ "$ORDER_GATEWAY_ENABLED" == "1" ]]; then
    start_service "order gateway" "$ORDER_GATEWAY_PID_FILE" "$ORDER_GATEWAY_LOG" \
      bash -c 'cd "$1" && exec uv run python -m app.workers.order_gateway' _ "$SERVER_DIR"
  else
    echo "Order gateway not started (live execution is not double-armed)."
  fi
  start_service "UI" "$UI_PID_FILE" "$UI_LOG" \
    bash -c 'cd "$1" && exec pnpm dev' _ "$CLIENT_DIR"

  trap cleanup_and_exit INT TERM

  echo
  echo "Swing Trader is running:"
  echo "  UI:  http://localhost:$UI_PORT"
  echo "  API: http://localhost:$API_PORT"
  echo "  Logs: $RUNTIME_DIR"
  echo "Press Ctrl-C to stop the local application processes."

  # Keep the launcher attached so Ctrl-C reliably cleans up every process.
  while true; do
    sleep 60
  done
}

main "$@"
