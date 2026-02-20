#!/usr/bin/env bash
set -euo pipefail

LOG_FILE=/harness-state/opencode-server.log
STATE_DIR=/run/opencode
PID_FILE="$STATE_DIR/server.pid"
READY_FILE="$STATE_DIR/server.ready"
PORT="${OPENCODE_SERVER_PORT:-4096}"
HOSTNAME=127.0.0.1
INSTALL_BIN=/opt/opencode/bin/opencode
CONFIG_PATH=/opt/opencode/opencode-permissive.json

mkdir -p /harness-state "$STATE_DIR"
: >"$LOG_FILE"
chown forklift:opencode "$LOG_FILE"
chmod 660 "$LOG_FILE"
rm -f "$PID_FILE" "$READY_FILE"

# TODO(https://github.com/anomalyco/opencode/issues/8502): Restore passwords
# when fixed
unset OPENCODE_SERVER_USERNAME
unset OPENCODE_SERVER_PASSWORD
#: "${OPENCODE_SERVER_PASSWORD:?OPENCODE_SERVER_PASSWORD is required}"

if [[ ! -x "$INSTALL_BIN" ]]; then
  echo "$(date --iso-8601=seconds) Missing OpenCode binary at $INSTALL_BIN" >>"$LOG_FILE"
  exit 1
fi

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "$(date --iso-8601=seconds) Missing OpenCode config at $CONFIG_PATH" >>"$LOG_FILE"
  exit 1
fi

export OPENCODE_CONFIG="$CONFIG_PATH"

if [[ -n "${OPENCODE_API_KEY:-}" ]]; then
  export OPENCODE_API_KEY
fi
#export OPENCODE_SERVER_PASSWORD
if [[ -n "${OPENCODE_ORG:-}" ]]; then
  export OPENCODE_ORG
fi

nohup stdbuf -oL -eL "$INSTALL_BIN" serve \
  --hostname "$HOSTNAME" \
  --port "$PORT" \
	--log-level DEBUG >>"$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" >"$PID_FILE"
disown "$SERVER_PID"

authority_url="http://$HOSTNAME:$PORT/status"
for _ in {1..60}; do
	## TODO(https://github.com/anomalyco/opencode/issues/8502): Uncomment when fixed
	# -u "opencode:$OPENCODE_SERVER_PASSWORD"
  if curl -fsS "$authority_url" >/dev/null 2>>"$LOG_FILE"; then
    printf '%s Server reported healthy on %s\n' "$(date --iso-8601=seconds)" "$authority_url" >>"$LOG_FILE"
    touch "$READY_FILE"
    exit 0
  fi
  sleep 1
  if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    printf '%s OpenCode server exited before readiness; see logs\n' "$(date --iso-8601=seconds)" >>"$LOG_FILE"
    exit 1
  fi
  printf '%s Waiting for OpenCode server health check...\n' "$(date --iso-8601=seconds)" >>"$LOG_FILE"
done

echo "$(date --iso-8601=seconds) OpenCode server did not become healthy within timeout" >>"$LOG_FILE"
kill "$SERVER_PID" >/dev/null 2>&1 || true
rm -f "$PID_FILE"
exit 1
