# Shared by setup.sh, start.sh, stop.sh. Source it; do not run it.
# Works in macOS/Linux bash and in Git Bash on Windows.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VIEWER="$ROOT/blueprint/viewer"
PUBLIC="$VIEWER/public"
DEMO_OUT="$ROOT/out/demo"
SERVE_PORT="${INTERVIEW_PORT:-8771}"  # macOS sharingd owns 8770; vite proxies /interview here
VITE_PORT="${VITE_PORT:-5173}"

# The venv interpreter: bin/python on macOS/Linux, Scripts/python.exe on Windows.
venv_python() {
  if [ -x "$ROOT/.venv/bin/python" ]; then echo "$ROOT/.venv/bin/python"
  elif [ -x "$ROOT/.venv/Scripts/python.exe" ]; then echo "$ROOT/.venv/Scripts/python.exe"
  fi
}

# A Python to create the venv with: 3.11 preferred (what the team runs), then any python3.
system_python() {
  for cand in python3.11 python3.12 python3.13 python3 python; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
      echo "$cand"; return
    fi
  done
  if command -v py >/dev/null 2>&1; then echo "py -3"; fi
}

say() { printf '\n== %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }

# Open a URL in the default browser if one is available.
open_url() {
  if command -v open >/dev/null 2>&1; then open "$1"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" >/dev/null 2>&1
  elif command -v cmd.exe >/dev/null 2>&1; then cmd.exe //c start "" "$1"
  fi
}

# Wait until an HTTP URL answers (any status), up to $2 seconds.
wait_http() {
  local url="$1" limit="${2:-60}" i=0
  while [ "$i" -lt "$limit" ]; do
    if curl -s -o /dev/null --max-time 2 "$url"; then return 0; fi
    sleep 1; i=$((i + 1))
  done
  return 1
}

# PIDs listening on a TCP port (Windows netstat or lsof), one per line.
port_pids() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -ti "tcp:$port" -sTCP:LISTEN 2>/dev/null
  elif command -v netstat >/dev/null 2>&1; then
    netstat -ano 2>/dev/null | awk -v p=":$port" '$2 ~ p"$" && $4 == "LISTENING" {print $5}' | sort -u
  fi
}

kill_pid() {
  local pid="$1"
  if command -v taskkill >/dev/null 2>&1; then
    taskkill //F //T //PID "$pid" >/dev/null 2>&1 || kill "$pid" 2>/dev/null
  else
    kill "$pid" 2>/dev/null
  fi
}
