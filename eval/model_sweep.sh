#!/usr/bin/env bash
# Model A/B sweep for a curunir eval suite.
# For each entry: (re)launch the SUT with MODEL=<m> (and OPENROUTER_PROVIDER=<p>
# when given), wait until :PORT is ready, run the eval suite, kill the SUT, move
# on. Reports auto-label by model, so results land in separate files to compare.
#
# Model list resolution (first match wins):
#   1. positional args:   model_sweep.sh <modelA> <modelB> ...   (model ids only)
#   2. MODELS_FILE=<path>: one entry per line -> "<model-id>  [provider]"
#   3. eval/model_sweep_models.txt (if present), same two-column format
#   4. built-in fallback list below
# In the file, '#' comments and blank lines are ignored; the optional second
# column is an OpenRouter provider slug (same as .env's OPENROUTER_PROVIDER).
#
# Usage:
#   eval/model_sweep.sh
#   eval/model_sweep.sh openrouter/z-ai/glm-5.2 anthropic/claude-sonnet-4-20250514
#   MODELS_FILE=eval/my_models.txt eval/model_sweep.sh
#   SUITE=eval/finance/run_finance_evals.py EVAL_ARGS="--id WS3" eval/model_sweep.sh
#
# Notes:
#   - MODEL/OPENROUTER_PROVIDER are passed ONLY to the server subprocess (inline
#     prefix), never exported to this shell — so the eval's llm_judge keeps using
#     its own JUDGE_MODEL (default Claude), fixed across the sweep.
#   - A file entry with NO provider column launches with OPENROUTER_PROVIDER=""
#     so it does NOT inherit the provider pinned in .env.
#   - Whatever web-search backend is staged (Brave or Gemini SKILL.md) is held
#     constant across all models. Pick one before sweeping.
#   - The sweep restarts the SUT repeatedly and leaves NO server running when it
#     finishes — relaunch your own dev instance afterward.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-$REPO/.venv/bin/python}"
PORT="${WS_PORT:-8765}"
SUITE="${SUITE:-eval/web_search/run_web_search_evals.py}"
EVAL_ARGS="${EVAL_ARGS:-}"
LOGDIR="${LOGDIR:-/tmp/curunir_model_sweep_logs}"
MODELS_FILE="${MODELS_FILE:-$REPO/eval/model_sweep_models.txt}"

# --- resolve the model list into parallel arrays (id + optional provider) -----
MODEL_IDS=(); PROVIDERS=()
if [ "$#" -gt 0 ]; then
  for a in "$@"; do MODEL_IDS+=("$a"); PROVIDERS+=(""); done
  echo "models: ${#MODEL_IDS[@]} from args"
elif [ -f "$MODELS_FILE" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"                              # strip trailing comment
    line="${line#"${line%%[![:space:]]*}"}"         # ltrim
    line="${line%"${line##*[![:space:]]}"}"         # rtrim
    [ -z "$line" ] && continue
    read -r mid prov _ <<<"$line"                   # split on whitespace
    MODEL_IDS+=("$mid"); PROVIDERS+=("${prov:-}")
  done < "$MODELS_FILE"
  echo "models: ${#MODEL_IDS[@]} from $MODELS_FILE"
else
  MODEL_IDS=("openrouter/z-ai/glm-5.2" "anthropic/claude-sonnet-4-20250514")
  PROVIDERS=("" "")
  echo "models: built-in fallback (${#MODEL_IDS[@]}); pass args or create $MODELS_FILE to override"
fi
[ "${#MODEL_IDS[@]}" -eq 0 ] && { echo "no models to run (empty list) — aborting"; exit 1; }

mkdir -p "$LOGDIR"
cd "$REPO" || exit 1

port_up() { (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null && { exec 3>&- 3<&-; return 0; }; return 1; }
kill_port() { local p; p=$(lsof -ti "tcp:$PORT" 2>/dev/null || true); [ -n "$p" ] && kill $p 2>/dev/null; sleep 1; return 0; }

echo "sweep: ${#MODEL_IDS[@]} run(s) | suite=$SUITE ${EVAL_ARGS:+args=$EVAL_ARGS}"
SUMMARY=()
for i in "${!MODEL_IDS[@]}"; do
  m="${MODEL_IDS[$i]}"; p="${PROVIDERS[$i]}"
  safe=$(printf '%s' "${m}${p:+__$p}" | tr '/:' '__')
  log="$LOGDIR/server-$safe.log"
  echo; echo "=========== MODEL: $m ${p:+(provider: $p)} ==========="
  kill_port
  echo "launching SUT (log: $log) ..."
  # MODEL + OPENROUTER_PROVIDER scoped to this subprocess only. Empty provider is
  # passed explicitly so .env's OPENROUTER_PROVIDER is NOT inherited.
  MODEL="$m" OPENROUTER_PROVIDER="$p" "$PY" run.py > "$log" 2>&1 &
  srv=$!
  for _ in $(seq 1 90); do port_up && break; kill -0 "$srv" 2>/dev/null || { echo "SUT died on boot — see $log"; break; }; sleep 1; done
  if ! port_up; then
    echo "!! SUT not ready on :$PORT for $m — skipping (see $log)"
    kill "$srv" 2>/dev/null; wait "$srv" 2>/dev/null
    SUMMARY+=("SKIP  $m ${p:+[$p]}  (boot failed)")
    continue
  fi
  sleep 5   # grace for skill-manifest build + welcome frame
  echo "running eval ..."
  # No MODEL prefix here: this shell never had MODEL set, so the eval's own
  # load_dotenv() supplies the judge model — fixed across the sweep.
  if "$PY" $SUITE $EVAL_ARGS; then rc="ok"; else rc="eval-nonzero"; echo "!! eval returned non-zero for $m (continuing)"; fi
  echo "stopping SUT ..."
  kill "$srv" 2>/dev/null; wait "$srv" 2>/dev/null
  kill_port
  SUMMARY+=("RAN   $m ${p:+[$p]}  ($rc)")
done

echo; echo "=========== sweep summary ==========="
printf '%s\n' "${SUMMARY[@]}"
echo "reports (per-model) in the suite's results/ dir; SUT logs in $LOGDIR"
echo "note: same model with different providers produces same-named reports —"
echo "      distinguish them by run timestamp (newest = last in this summary)."
