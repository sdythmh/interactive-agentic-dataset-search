#!/usr/bin/env bash
# Start the three corpus workers and the gateway.
#
#     bash ui/run_ui.sh --demo     the configuration described in the paper:
#                                  the same as --compare --ask-style grounded
#                                  --entity-resolve link_path, then waits until
#                                  every worker is ready and runs ui/warmup.py
#                                  once; its last line is "ready for demo".
#
#     ui/run_ui.sh                 start everything, return at once
#     UI_WAIT=1 ui/run_ui.sh       wait until every worker answers /api/health
#     UI_CORPORA=au,uk ui/run_ui.sh   only these corpora
#     ui/run_ui.sh --compare       the comparison view (UI_COMPARE=1). Without
#                                  it UI_COMPARE is unset, whatever the calling
#                                  shell says. A running worker is not
#                                  restarted: run ui/stop_ui.sh first.
#     ui/run_ui.sh --ask-style grounded   clarifying questions by the grounded
#                                  rule, with answer options taken from the
#                                  publishers in the results (the default).
#     ui/run_ui.sh --entity-resolve link_path   publisher linking: the model
#                                  picks one of the candidate publishers
#                                  collected by the rule, or none.
#     The options can be combined, in any order.
#
# Each worker starts from a clean environment: every variable that any of the
# three env files (eval/v4.env, eval/uk/v4.env, eval/us/v4.env) or config.py
# mentions, and every ASK_* /
# EVAL_* variable, is unset first; then that corpus's env file is loaded; then
# the UI settings are added. The worker checks the result again before it
# opens its port (ui/selfcheck.py).
#
# Python: $UI_PYTHON, default `python` on the PATH.
# Logs go to ui/logs/, pid and status files to ui/run/ (neither is committed).
set -u
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT" || exit 1
PY=${UI_PYTHON:-python}
RUN_DIR=${UI_RUN_DIR:-$ROOT/ui/run}
LOG_DIR=${UI_LOG_DIR:-$ROOT/ui/logs}
PORT=${UI_PORT:-8000}

env_file() {
  case "$1" in
    au) echo "eval/v4.env" ;;
    uk) echo "eval/uk/v4.env" ;;
    us) echo "eval/us/v4.env" ;;
    *) echo "unknown corpus: $1" >&2; return 1 ;;
  esac
}

# Runs in the current shell: call it inside a subshell.
load_corpus_env() {
  local corpus=$1 file keys cfg p3 k
  file=$(env_file "$corpus") || return 1
  keys=$(sed -E 's/^export +//' eval/v4.env eval/uk/v4.env eval/us/v4.env \
         | grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' | tr -d '=' | sort -u)
  cfg=$("$PY" ui/envkeys.py)
  p3=$(compgen -e | grep -E '^(ASK_|EVAL_)' || true)
  for k in $keys $cfg CORPUS INDEX_NAME USER_SIM $p3; do
    # The Neo4j password is a connection secret, not a switch.
    [ "$k" = "NEO4J_PASSWORD" ] && continue
    unset "$k"
  done
  set -a
  . "$file"
  set +a
  export ASK_MODE=on ASK_STYLE="$ASK_STYLE_UI" CITE_CHECK=lenient
  # Publisher linking: a system switch, cleared above with the others; set
  # only when asked for.
  [ -n "$ENTITY_RESOLVE_UI" ] && export ENTITY_RESOLVE="$ENTITY_RESOLVE_UI"
  # A UI setting, not a system switch (ui/settings.py): off unless asked for.
  unset UI_COMPARE
  [ "$COMPARE" = 1 ] && export UI_COMPARE=1
  return 0
}

COMPARE=0
DEMO=0
ASK_STYLE_UI=grounded
ENTITY_RESOLVE_UI=
while :; do
  case "${1:-}" in
    --compare) COMPARE=1; shift ;;
    --demo) DEMO=1; COMPARE=1; ASK_STYLE_UI=grounded; ENTITY_RESOLVE_UI=link_path; shift ;;
    --ask-style)
      case "${2:-}" in
        grounded) ASK_STYLE_UI=$2 ;;
        *) echo "--ask-style must be grounded" >&2; exit 2 ;;
      esac
      shift 2 ;;
    --entity-resolve)
      case "${2:-}" in
        link_path) ENTITY_RESOLVE_UI=$2 ;;
        *) echo "--entity-resolve must be link_path" >&2; exit 2 ;;
      esac
      shift 2 ;;
    *) break ;;
  esac
done

mkdir -p "$RUN_DIR" "$LOG_DIR"
CORPORA=${UI_CORPORA:-au,uk,us}

for corpus in ${CORPORA//,/ }; do
  pidfile="$RUN_DIR/$corpus.pid"
  if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "worker $corpus already running (pid $(cat "$pidfile"))"
    continue
  fi
  rm -f "$RUN_DIR/$corpus.status.json"
  (
    load_corpus_env "$corpus" || exit 1
    exec "$PY" -m ui.worker --corpus "$corpus"
  ) >> "$LOG_DIR/worker_$corpus.log" 2>&1 &
  echo $! > "$pidfile"
  echo "started worker $corpus (pid $!), log $LOG_DIR/worker_$corpus.log"
done

pidfile="$RUN_DIR/gateway.pid"
if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
  echo "gateway already running (pid $(cat "$pidfile"))"
else
  UI_PORT=$PORT "$PY" -m ui.gateway >> "$LOG_DIR/gateway.log" 2>&1 &
  echo $! > "$pidfile"
  echo "started gateway (pid $!) on http://127.0.0.1:$PORT"
fi

READY=1
if [ "${UI_WAIT:-0}" = "1" ] || [ "$DEMO" = 1 ]; then
  echo "waiting for workers (models load on start; this takes a minute) ..."
  for _ in $(seq 1 180); do
    out=$(curl -s "http://127.0.0.1:$PORT/api/health" || true)
    pending=0
    for corpus in ${CORPORA//,/ }; do
      st=$(echo "$out" | "$PY" -c "import sys,json
try:
    print(json.load(sys.stdin)['corpora']['$corpus']['state'])
except Exception:
    print('starting')")
      case "$st" in ready|busy) ;; error) echo "$corpus: error (see $LOG_DIR/worker_$corpus.log)"; READY=0 ;; *) pending=1 ;; esac
    done
    [ "$pending" = 0 ] && break
    sleep 2
  done
  [ "$pending" = 1 ] && READY=0
  curl -s "http://127.0.0.1:$PORT/api/health" | "$PY" -m json.tool | head -80
fi

if [ "$DEMO" = 1 ]; then
  if [ "$READY" = 1 ]; then
    UI_PORT=$PORT "$PY" ui/warmup.py
  else
    echo "not every worker is ready: warm-up skipped, not ready for demo"
    exit 1
  fi
fi
