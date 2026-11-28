#!/bin/bash
# A look: one wording's documents for the few people of scripts/cvlab/looks/<look>.json, written by one of the
# writers a person can write with, as production runs it, then printed for the session to read (look.py).
#
#   scripts/cvlab/look.sh <look> <variant[,variant]> <haiku|sol> [more run.py arguments, e.g. --dry-run --every-look]
#
# haiku: Claude Haiku 5.5 on this machine's own Claude Code sign-in (run.py --own-claude: the same call production
#        makes, on nobody's linked account; its usage cannot be read, so keep a round to a look or two).
# sol:   ChatGPT's Sol, lean, on the ChatGPT sign-in linked to the first admin of certificates/dev.env (needs the
#        local mongod, where that sign-in is kept).
# One run a writer at a time: a stored sign-in is used by one process (memory: provider CLI sign-in rotation).
# LOOK_OUT is where documents are kept (scripts/cvlab/out/look); a document already there is not written again.
set -euo pipefail
cd "$(dirname "$0")/../.."
look=$1; variants=$2; writer=$3; shift 3
case $writer in
  haiku) with=(--own-claude) ;;
  sol)   with=(--provider codex --model gpt-6.2-sol) ;;
  *)     echo "writer is haiku or sol, not $writer" >&2; exit 2 ;;
esac
export CLAUDE_CLI=${CLAUDE_CLI:-$HOME/.local/share/job-search-app/ai-tools/node_modules/.bin/claude}
export CODEX_CLI=${CODEX_CLI:-$HOME/.local/share/job-search-app/ai-tools/node_modules/.bin/codex}
set -a; . certificates/dev.env; set +a
out=${LOOK_OUT:-scripts/cvlab/out/look}
name=$out/$look-${variants//,/-}-$writer
kind=$(.venv/bin/python -c "import json,sys; print(json.load(open(sys.argv[1]))['kind'])" "scripts/cvlab/looks/$look.json")
overlay=(); [ "$kind" = cover ] && overlay=(--overlay scripts/cvlab/panel/letters)
mkdir -p "$out"
.venv/bin/python scripts/cvlab/look.py jobs "$look" "$out/$look-jobs"
.venv/bin/python scripts/cvlab/run.py --kind "$kind" --candidates-dir scripts/cvlab/panel \
  --candidates "$(.venv/bin/python scripts/cvlab/look.py people "$look")" "${overlay[@]}" \
  --jobs-dir "$out/$look-jobs" --variants "$variants" "${with[@]}" \
  --account "$(grep -E '^ADMIN_EMAILS=' certificates/dev.env | cut -d= -f2- | cut -d, -f1)" \
  --samples 1 --concurrency 6 --out "$out" --results "$name.results.json" "$@" > "$name.log" 2>&1 \
  || { tail -5 "$name.log"; exit 1; }
# The first paid document, and anything that failed or ran into a plan's limit: read these before the letters.
grep -E "first document|failed|hit your|limit" "$name.log" || true
for variant in ${variants//,/ }; do
  .venv/bin/python scripts/cvlab/look.py read "$look" "$name.results.json" "$variant" --out "$out"
done
