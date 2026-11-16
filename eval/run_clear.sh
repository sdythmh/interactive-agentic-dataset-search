#!/bin/bash
# Run a command in one corpus's evaluation environment.
#
#   bash eval/run_clear.sh CORPUS COMMAND [ARGS...]      CORPUS: au, uk or us
#
# Unsets every variable that appears in the three v4.env files, CORPUS,
# INDEX_NAME, ASK_*, EVAL_* and USER_SIM that the calling shell may carry,
# then loads that corpus's env file and runs the command. Variables that must
# survive the clearing are passed after it with `env`, e.g.
#
#   bash eval/run_clear.sh uk env RERANK_MODE=off python eval/run_eval.py ...
set -u
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT" || exit 1
if [ "$#" -lt 2 ]; then
  echo "usage: eval/run_clear.sh {au|uk|us} COMMAND [ARGS...]" >&2
  exit 2
fi
case "$1" in
  au) ENVFILE=eval/v4.env ;;
  uk) ENVFILE=eval/uk/v4.env ;;
  us) ENVFILE=eval/us/v4.env ;;
  *) echo "unknown corpus: $1" >&2; exit 2 ;;
esac
shift
KEYS=$(sed -E 's/^export +//' eval/v4.env eval/uk/v4.env eval/us/v4.env \
       | grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' | tr -d '=' | sort -u)
EXTRA=$(compgen -e | grep -E '^(ASK_|EVAL_)')
for k in $KEYS CORPUS INDEX_NAME ASK_MODE ASK_STYLE USER_SIM $EXTRA; do unset "$k"; done
set -a
. "$ENVFILE"
set +a
exec "$@"
