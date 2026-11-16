#!/usr/bin/env bash
# Stop the gateway and the workers that ui/run_ui.sh started.
# Only a pid whose command line is a ui.worker or ui.gateway process is signalled.
set -u
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RUN_DIR=${UI_RUN_DIR:-$ROOT/ui/run}
[ -d "$RUN_DIR" ] || { echo "nothing to stop"; exit 0; }

is_ours() {
  [ -r "/proc/$1/cmdline" ] && tr '\0' ' ' < "/proc/$1/cmdline" | grep -qE 'ui\.(worker|gateway)'
}

pids=()
for pidfile in "$RUN_DIR"/*.pid; do
  [ -f "$pidfile" ] || continue
  pid=$(cat "$pidfile")
  if is_ours "$pid"; then
    kill "$pid" 2>/dev/null && pids+=("$pid") && echo "stopping $(basename "$pidfile" .pid) (pid $pid)"
  else
    echo "$(basename "$pidfile" .pid): not running"
  fi
  rm -f "$pidfile"
done
for _ in $(seq 1 20); do
  alive=0
  for pid in "${pids[@]:-}"; do [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && alive=1; done
  [ "$alive" = 0 ] && break
  sleep 0.5
done
for pid in "${pids[@]:-}"; do
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && { echo "pid $pid did not exit, killing"; kill -9 "$pid"; }
done
rm -f "$RUN_DIR"/*.status.json
echo "stopped"
