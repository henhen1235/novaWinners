#!/usr/bin/env bash
# Start the interview API (cognition.serve, port 8771) and the viewer (vite, port 5173),
# wait until both answer, then print and open the demo URLs. Stop with scripts/demo/stop.sh.
#   scripts/demo/start.sh [--no-open]
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
cd "$ROOT"

OPEN=1; [ "${1:-}" = "--no-open" ] && OPEN=0
PY="$(venv_python)"
[ -n "$PY" ] || { echo "No .venv; run scripts/demo/setup.sh first." >&2; exit 1; }
[ -d "$VIEWER/node_modules" ] || { echo "No node_modules; run scripts/demo/setup.sh first." >&2; exit 1; }
[ -d "$PUBLIC/replays" ] || warn "no $PUBLIC/replays; run scripts/demo/setup.sh first"
mkdir -p "$DEMO_OUT"

for port in "$SERVE_PORT" "$VITE_PORT"; do
  if [ -n "$(port_pids "$port")" ]; then warn "port $port is already in use; run scripts/demo/stop.sh first"; fi
done

# Interview server, from the repo root so replay meta.scene paths resolve.
INTERVIEW_PORT="$SERVE_PORT" "$PY" -m cognition.serve --runs blueprint/viewer/public/replays --port "$SERVE_PORT" \
  >"$DEMO_OUT/serve.log" 2>&1 &
echo $! >"$DEMO_OUT/serve.pid"

# Vite via node directly so the PID is the server itself.
(cd "$VIEWER" && INTERVIEW_PORT="$SERVE_PORT" exec node node_modules/vite/bin/vite.js --port "$VITE_PORT" --strictPort) \
  >"$DEMO_OUT/vite.log" 2>&1 &
echo $! >"$DEMO_OUT/vite.pid"

ok=1
wait_http "http://127.0.0.1:$SERVE_PORT/health" 60 || { warn "interview server did not answer; see $DEMO_OUT/serve.log"; ok=0; }
wait_http "http://localhost:$VITE_PORT/" 60 || { warn "viewer did not answer; see $DEMO_OUT/vite.log"; ok=0; }

B="http://localhost:$VITE_PORT"
# Moment 3 uses the recorded LLM hall loop when present, else the offline tiny_office loop.
if [ -f "$PUBLIC/replays/loop_hall_r1_fixed/replay.json" ]; then
  LOOP_SCENE="/hall/scene.json&glb=/hall/display.glb"; LOOP=hall; REPORT="$B/loop/demo_hall/report.html"
else
  LOOP_SCENE="/fixtures/tiny_office.scene.json&glb="; LOOP=tiny; REPORT="$B/loop/offline_tiny/report.html"
fi
if [ -f "$PUBLIC/hall/scene.json" ]; then
  M1="$B/?glb=/hall/display.glb&scene=/hall/scene.json&analysis=/hall/analysis.json"
else
  M1="$B/?glb=/office/display.glb&scene=/office/scene.json&analysis=/office/analysis.json"
fi
T="glb=/tepper/display.glb&scene=/tepper/scene.json"
M2a="$B/?$T&replay=/replays/tepper_fire_noplan/replay.json&view=side"
M2b="$B/?$T&replay=/replays/tepper_fire_noplan/replay.json&compare=/replays/tepper_fire_plan/replay.json&names=No%20plan,Assisted%20plan&view=side"
M3="$B/?scene=$LOOP_SCENE&replay=/replays/loop_${LOOP}_r1_attack/replay.json&compare=/replays/loop_${LOOP}_r1_fixed/replay.json&names=Attacked,Fixed"
M4="$B/?scene=$LOOP_SCENE&replay=/replays/loop_${LOOP}_r1_fixed/replay.json"

cat >"$DEMO_OUT/urls.txt" <<EOF
1  Scan in, model out:        $M1
2a Tepper, floors side by side: $M2a
2b Tepper, no plan vs plan:     $M2b
3  Attacked vs fixed:          $M3
3  Loop report:                $REPORT
4  Interview (click a person): $M4
EOF
echo; cat "$DEMO_OUT/urls.txt"; echo
echo "Logs: $DEMO_OUT/serve.log, $DEMO_OUT/vite.log. Stop: scripts/demo/stop.sh"
[ "$OPEN" = 1 ] && [ "$ok" = 1 ] && open_url "$M1"
[ "$ok" = 1 ]
