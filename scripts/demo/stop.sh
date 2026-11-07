#!/usr/bin/env bash
# Stop the interview server and the viewer started by scripts/demo/start.sh.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

for name in serve vite; do
  f="$DEMO_OUT/$name.pid"
  if [ -f "$f" ]; then kill "$(cat "$f")" 2>/dev/null; rm -f "$f"; fi
done
# Anything still listening on the demo ports (Windows child processes outlive the bash PID).
for port in "$SERVE_PORT" "$VITE_PORT"; do
  for pid in $(port_pids "$port"); do kill_pid "$pid"; done
done
sleep 1
left=""
for port in "$SERVE_PORT" "$VITE_PORT"; do [ -n "$(port_pids "$port")" ] && left="$left $port"; done
if [ -n "$left" ]; then echo "Still listening on:$left" >&2; exit 1; fi
echo "Stopped (ports $SERVE_PORT, $VITE_PORT free)."
