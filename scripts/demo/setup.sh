#!/usr/bin/env bash
# One-command demo setup from a clean clone. Builds every demo asset into
# blueprint/viewer/public/ (gitignored). No LLM calls: replays use the rule policy,
# recorded LLM runs are copied from out/loop/ when present.
#
#   scripts/demo/setup.sh [--cv] [--skip-install]
#     --cv            also install requirements-cv.txt (OWLv2; large)
#     --skip-install  reuse the existing .venv and node_modules; only rebuild assets
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
cd "$ROOT"

CV=0; INSTALL=1
for arg in "$@"; do
  case "$arg" in
    --cv) CV=1 ;;
    --skip-install) INSTALL=0 ;;
    -h|--help) sed -n 2,9p "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

FAILED=()
fail() { warn "$1"; FAILED+=("$1"); }

# ---------- 1. Python and Node dependencies ----------
if [ "$INSTALL" = 1 ]; then
  if [ -z "$(venv_python)" ]; then
    SYS_PY="$(system_python)"
    [ -n "$SYS_PY" ] || { echo "No Python >= 3.11 found; install python3.11 first." >&2; exit 1; }
    say "Creating .venv with $SYS_PY"
    $SYS_PY -m venv .venv || { echo "venv creation failed" >&2; exit 1; }
  fi
  PY="$(venv_python)"
  say "Installing requirements.txt"
  "$PY" -m pip install -q -r requirements.txt || fail "pip install -r requirements.txt failed (see output above; the team runs Python 3.11)"
  if [ "$CV" = 1 ]; then
    say "Installing requirements-cv.txt"
    "$PY" -m pip install -q -r requirements-cv.txt || fail "pip install -r requirements-cv.txt failed"
  fi
  say "npm ci in blueprint/viewer"
  (cd "$VIEWER" && npm ci --no-audit --no-fund) || fail "npm ci failed"
fi
PY="$(venv_python)"
[ -n "$PY" ] || { echo "No .venv; run without --skip-install." >&2; exit 1; }

mkdir -p "$PUBLIC/replays" "$DEMO_OUT"

# ---------- 2. Scenes ----------
say "Scenes"
SCENES=()   # public dirs that hold a scene.json, for the analyses
if [ -f samples/cmu/tepper/scene.json ]; then
  mkdir -p "$PUBLIC/tepper"
  cp samples/cmu/tepper/scene.json "$PUBLIC/tepper/"
  [ -f samples/cmu/tepper/display.glb ] && cp samples/cmu/tepper/display.glb "$PUBLIC/tepper/"
  SCENES+=(tepper); echo "tepper: copied"
else
  fail "samples/cmu/tepper/scene.json missing (pull main)"
fi

# Mudge House (dorm). Not added to SCENES: its full analysis takes over 10 minutes.
if [ -f samples/cmu/mudge/scene.json ]; then
  mkdir -p "$PUBLIC/mudge"
  cp samples/cmu/mudge/scene.json "$PUBLIC/mudge/"
  [ -f samples/cmu/mudge/display.glb ] && cp samples/cmu/mudge/display.glb "$PUBLIC/mudge/"
  echo "mudge: copied"
else
  fail "samples/cmu/mudge/scene.json missing (pull main)"
fi

mkdir -p "$PUBLIC/fixtures"
cp fixtures/tiny_office.scene.json fixtures/two_floor_school.scene.json "$PUBLIC/fixtures/"
echo "fixtures: copied"

if [ -f samples/scans/hall_mesh/mesh.ply ]; then
  echo "hall: ingesting samples/scans/hall_mesh/mesh.ply"
  if "$PY" -m ingest_lidar samples/scans/hall_mesh/mesh.ply -o out/hall --no-llm; then
    mkdir -p "$PUBLIC/hall"
    cp out/hall/scene.json out/hall/display.glb "$PUBLIC/hall/"
    SCENES+=(hall)
  else
    fail "hall ingest failed"
  fi
else
  echo "hall: SKIP (samples/scans/hall_mesh/mesh.ply is local to the scanning laptop; copy it there to build the hall)"
fi

echo "office: ingesting blueprint/samples/blueprints/office_vector/plan.pdf"
if (cd blueprint && "$PY" -m ingest_blueprint samples/blueprints/office_vector/plan.pdf -o out/office --no-llm); then
  mkdir -p "$PUBLIC/office"
  cp blueprint/out/office/scene.json blueprint/out/office/display.glb "$PUBLIC/office/"
  SCENES+=(office)
else
  fail "office blueprint ingest failed"
fi

# ---------- 3. Analyses ----------
say "Analyses"
analyse() {  # $1 scene.json, $2 output dir
  "$PY" -m analysis "$1" -o "$2/analysis.json" >/dev/null && echo "analysis: $2/analysis.json" || fail "analysis failed for $1"
}
for s in "${SCENES[@]}"; do analyse "$PUBLIC/$s/scene.json" "$PUBLIC/$s"; done
for f in tiny_office two_floor_school; do
  mkdir -p "$PUBLIC/fixtures/$f"
  analyse "$PUBLIC/fixtures/$f.scene.json" "$PUBLIC/fixtures/$f"
done

# ---------- 4. Replays (rule policy, no LLM, seed 42) ----------
say "Replays"
sim() {  # $1 mode, $2 scenario, $3 scene, $4 replay name
  if [ ! -f "$3" ]; then echo "replay $4: SKIP (no scene $3)"; return; fi
  if "$PY" -m sim "$1" --scenario "$2" --scene "$3" -o "$PUBLIC/replays/$4" --seed 42 --no-llm; then :; else fail "sim $1 $4 failed"; fi
}
sim run      sim/examples/tiny_office_fire.json "$PUBLIC/fixtures/tiny_office.scene.json"      tiny_office_fire
sim baseline sim/examples/tiny_office_fire.json "$PUBLIC/fixtures/tiny_office.scene.json"      tiny_office_fire_baseline
sim run      sim/examples/school_stairs.json    "$PUBLIC/fixtures/two_floor_school.scene.json" school_stairs
sim run      sim/examples/office_fire.json      "$PUBLIC/office/scene.json"                    office_fire
sim run      sim/examples/hall_alarm.json       "$PUBLIC/hall/scene.json"                      hall_alarm
sim baseline sim/examples/hall_alarm.json       "$PUBLIC/hall/scene.json"                      hall_alarm_baseline
sim run      sim/examples/tepper_fire.json      "$PUBLIC/tepper/scene.json"                    tepper_fire_noplan

# Tepper with the assisted-evacuation plan: same scenario plus one intervention.
"$PY" - <<'EOF' || fail "could not write the assisted-plan scenario"
import json, pathlib
s = json.loads(pathlib.Path("sim/examples/tepper_fire.json").read_text())
s["interventions"] = list(s.get("interventions", [])) + [{"type": "assisted_evacuation_plan"}]
s["assumptions"] = list(s.get("assumptions", [])) + ["Assisted evacuation plan: trained staff carry people who cannot use stairs."]
pathlib.Path("out/demo/tepper_fire_plan.json").write_text(json.dumps(s, indent=1))
EOF
sim run out/demo/tepper_fire_plan.json "$PUBLIC/tepper/scene.json" tepper_fire_plan
# Full-occupancy runs: Tepper weekday (1200 people, about 3 min) and Mudge at night (305 people).
sim run      sim/examples/tepper_weekday_fire.json "$PUBLIC/tepper/scene.json"                 tepper_weekday_fire
sim run      sim/examples/mudge_night_fire.json    "$PUBLIC/mudge/scene.json"                  mudge_night_fire

# ---------- 5. Adversary vs optimizer runs ----------
say "Adversary vs optimizer"
# Recorded real-LLM runs from out/loop/ (copied, never re-run here, so no LLM spend).
copy_loop() {  # $1 loop dir name, $2 short name, $3 scene.json for report links
  local d="out/loop/$1" stage n=0
  if [ ! -d "$d" ]; then echo "loop $2: SKIP ($d not present; copy it from the laptop that ran it)"; return 1; fi
  for stage in r0_baseline r1_attack r1_fixed; do
    if [ -f "$d/$stage/s42/replay.json" ]; then
      rm -rf "$PUBLIC/replays/loop_$2_$stage"
      cp -R "$d/$stage/s42" "$PUBLIC/replays/loop_$2_$stage"; n=$((n + 1))
    fi
  done
  # The whole loop dir too, so report.html's own viewer links (/loop/<name>/...) resolve.
  mkdir -p "$PUBLIC/loop"; rm -rf "$PUBLIC/loop/$1"; cp -R "$d" "$PUBLIC/loop/$1"
  [ -f "$PUBLIC/loop/$1/scene.json" ] || [ ! -f "${3:-}" ] || cp "$3" "$PUBLIC/loop/$1/scene.json"
  echo "loop $2: copied $n stages and $d/report.html"
}
HAVE_LLM_LOOP=0
copy_loop demo_hall hall "$PUBLIC/hall/scene.json" && HAVE_LLM_LOOP=1
copy_loop demo_tepper tepper "$PUBLIC/tepper/scene.json" && HAVE_LLM_LOOP=1

# Always build one free offline loop (heuristic adversary/optimizer, rule-policy occupants)
# so moment 3 works on any machine. Labeled offline in docs/DEMO.md.
if "$PY" -m loop run --scene fixtures/tiny_office.scene.json --scenario loop/examples/tiny_office_drill.json \
     -o out/loop/offline_tiny --rounds 1 --no-llm --seeds 42 >/dev/null; then
  copy_loop offline_tiny tiny fixtures/tiny_office.scene.json >/dev/null && echo "loop tiny (offline, no LLM): built"
else
  fail "offline loop run on tiny_office failed"
fi

# Building and scenario lists for the viewer header (public/replays/index.json).
(cd blueprint/viewer && node scripts/replay_index.ts) || fail "replay index failed"

# ---------- Summary ----------
say "Done"
echo "Assets in $PUBLIC:"
ls "$PUBLIC" "$PUBLIC/replays" 2>/dev/null | sed 's/^/  /'
[ "$HAVE_LLM_LOOP" = 1 ] || echo "Note: no recorded LLM loop runs (out/loop/demo_hall, demo_tepper); moment 3 uses the offline tiny_office loop."
if [ "${#FAILED[@]}" -gt 0 ]; then
  echo; echo "Problems (${#FAILED[@]}):"; printf '  - %s\n' "${FAILED[@]}"; exit 1
fi
echo "Next: scripts/demo/start.sh"
