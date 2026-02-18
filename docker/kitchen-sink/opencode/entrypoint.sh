#!/usr/bin/env bash
set -euo pipefail

STATE_DIR=/run/opencode
PID_FILE="$STATE_DIR/server.pid"
READY_FILE="$STATE_DIR/server.ready"
SERVER_LOG=/harness-state/opencode-server.log
CLIENT_LOG=/harness-state/opencode-client.log
CLIENT_ENV_VARS=(
  OPENCODE_MODEL
  OPENCODE_VARIANT
  OPENCODE_AGENT
  OPENCODE_SERVER_PORT
  OPENCODE_SERVER_PASSWORD
  OPENCODE_TIMEOUT
)

mkdir -p /harness-state "$STATE_DIR"
: >"$CLIENT_LOG"
chown root:opencode "$CLIENT_LOG"
chmod 660 "$CLIENT_LOG"

cleanup() {
  if [[ -f "$PID_FILE" ]]; then
    SERVER_PID=$(cat "$PID_FILE")
    if command -v /opt/opencode/bin/opencode >/dev/null 2>&1; then
      /opt/opencode/bin/opencode session stop --all >>"$SERVER_LOG" 2>&1 || true
    fi
    if kill -0 "$SERVER_PID" >/dev/null 2>&1; then
      kill "$SERVER_PID" >/dev/null 2>&1 || true
      wait "$SERVER_PID" >/dev/null 2>&1 || true
    fi
  fi
  rm -f "$PID_FILE" "$READY_FILE"
}
trap cleanup EXIT
trap 'cleanup; exit 0' SIGTERM SIGINT

if ! /opt/opencode/start_server.sh; then
  echo "OpenCode server failed to start; inspect $SERVER_LOG" >&2
  exit 1
fi

if [[ ! -f "$PID_FILE" ]]; then
  echo "Missing OpenCode server PID file at $PID_FILE" >>"$SERVER_LOG"
  exit 1
fi

wait_for_ready_marker() {
  for _ in {1..30}; do
    if [[ -f "$READY_FILE" ]]; then
      return 0
    fi
    sleep 1
  done
  echo "Server readiness marker $READY_FILE missing" >>"$SERVER_LOG"
  return 1
}

wait_for_ready_marker

run_harness() {
  env_args=()
  for var in "${CLIENT_ENV_VARS[@]}"; do
    value=${!var-}
    if [[ -n "$value" ]]; then
      env_args+=("${var}=${value}")
    fi
  done
  env_args+=("OPENCODE_SERVER_USERNAME=opencode")
  env_args+=("OPENCODE_CLIENT_LOG=$CLIENT_LOG")
  runuser -u forklift -- env "${env_args[@]}" /opt/forklift/harness/run.sh
}

if ! run_harness; then
  exit_code=$?
  exit $exit_code
fi
