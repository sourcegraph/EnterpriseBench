#!/usr/bin/env bash
# test_runner.sh — Cross-repo test runner for EnterpriseBench tasks.
# Executes checkpoint verifiers and produces structured JSON output.
#
# Usage: /workspace/test.sh [checkpoint_name]
#
# Output: JSON object with per-checkpoint results and aggregate score.
# Exit code: 0 if all checkpoints pass, 1 otherwise.
set -uo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
VERIFIER_DIR="$WORKSPACE/.verifiers"
# Scoring closes $WORKSPACE to everyone but root (the grader must not be able to
# plant an artifact for a later checkpoint), so the orchestrator redirects this
# file to a scorer-writable path. The default stays put for standalone runs.
RESULTS_FILE="${EB_RESULTS_FILE:-$WORKSPACE/.results.json}"

# --- helpers ---

json_escape() {
    # Escape a string for safe JSON embedding (all RFC 7159 control chars)
    printf '%s' "$1" \
      | sed 's/\\/\\\\/g; s/"/\\"/g; s/\t/\\t/g; s/\r/\\r/g; s/\x0c/\\f/g; s/\x08/\\b/g' \
      | tr '\n' ' '
}

now_ms() {
    date +%s%3N 2>/dev/null || date +%s000
}

# Discover repos in /workspace/ (directories with .git)
discover_repos() {
    local repos=()
    for dir in "$WORKSPACE"/*/; do
        [ -d "$dir/.git" ] && repos+=("$(basename "$dir")")
    done
    printf '%s\n' "${repos[@]}"
}

# Declares a verifier-harness failure rather than an agent failure. Must stay in
# lockstep with eb_verify.scorer_guard.INFRA_SENTINEL, which greps for it.
INFRA_SENTINEL="VERIFIER_INFRA_ERROR"

# Sourced (via BASH_ENV) by every verifier shell. Bash calls
# command_not_found_handle for any command it cannot find in ANY context —
# including inside `if python3 ... 2>/dev/null`, where the verifier's own
# control flow would otherwise swallow the failure. EB_INFRA_LOG is a side
# channel the verifier cannot redirect away. Returning 127 reproduces bash's
# native exit status for an unfindable command, so `set -e` and every exit-code
# test behave as they would without the handler.
INFRA_PREAMBLE=$(mktemp)
INFRA_LOG=$(mktemp)
trap 'rm -f "$INFRA_PREAMBLE" "$INFRA_LOG"' EXIT
cat >"$INFRA_PREAMBLE" <<'PREAMBLE'
command_not_found_handle() {
    printf '%s\n' "$1" >>"${EB_INFRA_LOG:-/dev/null}"
    printf '%s: command not found\n' "$1" >&2
    return 127
}
PREAMBLE

# Run a single verifier, capture JSON output and exit code.
# Returns: sets VERIFIER_EXIT, VERIFIER_JSON, VERIFIER_DURATION_MS, VERIFIER_RAN
#
# Never fabricates a score from an exit code: absence of a verdict is not a
# verdict of 0.0, nor of 1.0. VERIFIER_RAN attests whether the verifier actually
# reached one; the scorer refuses to score any checkpoint that did not.
run_verifier() {
    local verifier_path="$1"
    local timeout_sec="${2:-120}"
    local start end raw_stdout raw_stderr raw_stdout_file

    start=$(now_ms)
    raw_stderr=$(mktemp)
    raw_stdout_file=$(mktemp)
    : >"$INFRA_LOG"

    # Pass the workspace explicitly as $1: several checks resolve
    # WORKSPACE="${1:-.}", which shadows the exported env var. Scoring runs from
    # a cwd outside the workspace (the agent must not control the checks'
    # sys.path[0]), so the "." fallback would silently zero every such check.
    if command -v timeout >/dev/null 2>&1; then
        BASH_ENV="$INFRA_PREAMBLE" EB_INFRA_LOG="$INFRA_LOG" \
            timeout "$timeout_sec" bash "$verifier_path" "$WORKSPACE" >"$raw_stdout_file" 2>"$raw_stderr"
        VERIFIER_EXIT=$?
    else
        BASH_ENV="$INFRA_PREAMBLE" EB_INFRA_LOG="$INFRA_LOG" \
            bash "$verifier_path" "$WORKSPACE" >"$raw_stdout_file" 2>"$raw_stderr"
        VERIFIER_EXIT=$?
    fi

    raw_stdout=$(cat "$raw_stdout_file")
    rm -f "$raw_stdout_file"

    # awk, not `sort -u`: awk is already a hard dependency of this script (the
    # weighted-score math below), and a missing-command detector must not itself
    # depend on a command the image may lack.
    local missing_cmds=""
    [ -s "$INFRA_LOG" ] && missing_cmds=$(awk '!seen[$0]++ { printf "%s%s", (c++ ? " " : ""), $0 }' "$INFRA_LOG")

    end=$(now_ms)
    VERIFIER_DURATION_MS=$(( end - start ))
    VERIFIER_RAN="true"

    local infra_detail=""
    if [ -n "$missing_cmds" ]; then
        # Ahead of the timeout check: a command that does not exist is an infra
        # failure whether or not the verifier also ran out the clock. Fires even
        # when the verifier swallowed the failure and went on to print a
        # legitimate-looking score.
        infra_detail="$INFRA_SENTINEL: verifier shelled a command that does not exist: $missing_cmds"
    elif [ "$VERIFIER_EXIT" -eq 124 ]; then
        # A timeout means the verifier ran but did not finish, so it stays a
        # scored 0.0: a hang is as often the subject code's fault as the harness's.
        VERIFIER_JSON="{\"score\": 0.0, \"passed\": false, \"detail\": \"Timed out after ${timeout_sec}s\"}"
        rm -f "$raw_stderr"
        return
    elif [ "$VERIFIER_EXIT" -eq 127 ]; then
        # A not-found command raised by something other than the handler, e.g. a
        # non-bash interpreter in the chain.
        infra_detail="$INFRA_SENTINEL: verifier exited 127 (command not found)"
    elif ! printf '%s' "$raw_stdout" | grep -q '^{'; then
        local stderr_content
        stderr_content=$(cat "$raw_stderr" 2>/dev/null || true)

        if [ -n "$AGENT_OUTPUT_INVALID" ] && [ "$VERIFIER_EXIT" -ne 0 ]; then
            # The verifier RAN; the agent's own malformed artifact killed it
            # mid-flight (see AGENT_OUTPUT_INVALID). A bad answer is the agent's
            # failure, so it is scored 0.0 — routing it to the infra re-run
            # channel would let an agent escape a deserved 0.0 by emitting
            # garbage. Requires a nonzero exit: a verifier that exits 0 without
            # printing anything is broken irrespective of the answer.
            VERIFIER_JSON="{\"score\": 0.0, \"passed\": false, \"detail\": \"$(json_escape "$AGENT_OUTPUT_INVALID (exit $VERIFIER_EXIT): ${stderr_content:-no output}")\"}"
            rm -f "$raw_stderr"
            return
        fi

        # No JSON, and nothing about the agent's answer explains it: the verifier
        # died before reaching its verdict.
        infra_detail="$INFRA_SENTINEL: verifier produced no JSON verdict (exit $VERIFIER_EXIT): ${stderr_content:-no output}"
    fi

    if [ -n "$infra_detail" ]; then
        VERIFIER_RAN="false"
        VERIFIER_JSON="{\"score\": 0.0, \"passed\": false, \"verifier_ran\": false, \"detail\": \"$(json_escape "$infra_detail")\"}"
    else
        VERIFIER_JSON="$raw_stdout"
    fi

    rm -f "$raw_stderr"
}

# --- is the agent's own answer artifact structurally unusable? ----------------
#
# A check script that dies parsing a malformed answer.json produced no verdict,
# but it did not fail to RUN: it ran, and the agent's output killed it. Without
# this distinction the runner reads every garbage answer as "the verifier never
# ran" and routes a deserved 0.0 into the infra re-run channel — an agent could
# evade its score by emitting garbage. 104 of the 132 corpus check scripts that
# read answer.json abort under `set -e` on an answer that is not a JSON object,
# so this is the common case, not an edge one.
#
# Structural only, and deliberately narrow: an answer that IS a JSON object never
# trips this, however wrong its contents — wrongness is what the checks are for.
# Unparseable, and parseable-but-not-an-object, are the only two shapes the corpus
# cannot survive (measured: {}, wrong value types, and hostile nesting all still
# reach a verdict).
#
# Ahead of the mode split: run_verifier reads this, so under `set -u` every mode
# must cross the assignment, and both modes owe the agent the same attribution.
AGENT_OUTPUT_INVALID=""
AGENT_ANSWER="$WORKSPACE/agent_output/answer.json"
if [ -f "$AGENT_ANSWER" ] && command -v python3 >/dev/null 2>&1; then
    if ! python3 -c "
import json, sys
with open(sys.argv[1]) as fh:
    sys.exit(0 if isinstance(json.load(fh), dict) else 1)
" "$AGENT_ANSWER" >/dev/null 2>&1; then
        AGENT_OUTPUT_INVALID="agent_output/answer.json is not a JSON object, so no check could read it"
    fi
fi

# --- single checkpoint mode ---

if [ -n "${1:-}" ]; then
    CHECKPOINT="$1"
    # Validate checkpoint name (alphanumeric, hyphens, underscores only)
    if [[ "$CHECKPOINT" =~ [^a-zA-Z0-9_-] ]]; then
        printf '{"score": 0.0, "passed": false, "detail": "Invalid checkpoint name"}\n'
        exit 1
    fi
    VERIFIER="$VERIFIER_DIR/${CHECKPOINT}.sh"
    if [ ! -f "$VERIFIER" ]; then
        printf '{"score": 0.0, "passed": false, "detail": "No verifier found for checkpoint: %s"}\n' "$CHECKPOINT"
        exit 1
    fi
    run_verifier "$VERIFIER" 120
    printf '%s\n' "$VERIFIER_JSON"
    exit "$VERIFIER_EXIT"
fi

# --- full run: all checkpoints ---

echo "=== EnterpriseBench Cross-Repo Test Runner ===" >&2
echo "Workspace: $WORKSPACE" >&2

# List repos for diagnostics (stderr so it doesn't pollute JSON stdout)
mapfile -t REPOS < <(discover_repos)
echo "Repos:" >&2
for repo in "${REPOS[@]}"; do
    echo "  - $repo" >&2
done
echo "" >&2

# Verify cross-repo access: confirm we can cd into each repo
for repo in "${REPOS[@]}"; do
    if ! (cd "$WORKSPACE/$repo" && pwd >/dev/null); then
        printf '{"task_score": 0.0, "all_passed": false, "error": "Cannot access repo: %s"}\n' "$repo"
        exit 1
    fi
done

# Run all verifiers
if [ ! -d "$VERIFIER_DIR" ]; then
    printf '{"task_score": 0.0, "all_passed": false, "checkpoints": [], "error": "No .verifiers/ directory found"}\n'
    exit 1
fi

# --- preflight: an interpreter the check scripts need must actually exist -----
#
# command_not_found_handle only fires when BASH performs the PATH lookup itself,
# so it is blind to `env python3` and absolute-path invocations. No in-bash
# signal can see those, so detection alone cannot close them — but the
# precondition can: if a check script needs an interpreter the image lacks, no
# verifier depending on it can reach a verdict, whatever syntax it uses. Refuse
# to score the task at all rather than emit numbers that only look like
# measurements.
#
# Gated on the interpreter actually being referenced, so a task whose checks are
# pure bash is never failed for lacking one it does not use.
REQUIRED_INTERPRETERS="python3"
MISSING_INTERPRETERS=""
for interp in $REQUIRED_INTERPRETERS; do
    # `command -v` (a builtin) first: it short-circuits the grep, which forks and
    # reads every check script, in the common case where the interpreter is present.
    if ! command -v "$interp" >/dev/null 2>&1 \
       && grep -qF -- "$interp" "$VERIFIER_DIR"/*.sh 2>/dev/null; then
        MISSING_INTERPRETERS="$MISSING_INTERPRETERS $interp"
    fi
done
if [ -n "$MISSING_INTERPRETERS" ]; then
    printf '{"task_score": 0.0, "all_passed": false, "checkpoints": [], "error": "%s: check scripts require interpreter(s) not installed in this image:%s"}\n' \
        "$INFRA_SENTINEL" "$(json_escape "$MISSING_INTERPRETERS")"
    exit 1
fi

TOTAL=0
PASSED=0
CHECKPOINT_RESULTS=""
WEIGHTED_SCORE="0"

for verifier in "$VERIFIER_DIR"/*.sh; do
    [ -f "$verifier" ] || continue
    name=$(basename "$verifier" .sh)
    TOTAL=$((TOTAL + 1))

    # Read weight from companion .meta file if present, else default 1.0.
    # Validate it is a plain number: it is interpolated unquoted into both the
    # result JSON and an awk expression below, so a malformed weight is an
    # injection point. .verifiers/ is sealed root-only, but this check is the
    # layer that does not depend on the seal holding.
    weight="1.0"
    meta_file="$VERIFIER_DIR/${name}.meta"
    if [ -f "$meta_file" ]; then
        w=$(grep -oP '(?<=weight=)\S+' "$meta_file" 2>/dev/null || true)
        [[ "$w" =~ ^-?[0-9]+(\.[0-9]+)?$ ]] && weight="$w"
    fi

    # Read timeout from .meta file if present
    checkpoint_timeout=120
    if [ -f "$meta_file" ]; then
        t=$(grep -oP '(?<=timeout=)\S+' "$meta_file" 2>/dev/null || true)
        [ -n "$t" ] && checkpoint_timeout="$t"
    fi

    echo "--- Checkpoint: $name (weight=$weight, timeout=${checkpoint_timeout}s) ---" >&2
    run_verifier "$verifier" "$checkpoint_timeout"

    # Extract score from verifier JSON
    checkpoint_score=$(printf '%s' "$VERIFIER_JSON" | grep -oP '"score"\s*:\s*\K[0-9.]+' || echo "0.0")

    # Extract passed from verifier JSON
    checkpoint_passed=$(printf '%s' "$VERIFIER_JSON" | grep -oP '"passed"\s*:\s*\K(true|false)' || echo "false")

    # Extract the canonical "detail" string (verifier_output.schema.json) as a
    # JSON string token, including escapes, so it can be embedded verbatim.
    # Preserved per-checkpoint in output.json for debugging; empty when absent.
    checkpoint_detail=$(printf '%s' "$VERIFIER_JSON" | grep -oPm1 '"detail"\s*:\s*\K"(\\.|[^"\\])*"')
    [ -n "$checkpoint_detail" ] || checkpoint_detail='""'

    if [ "$VERIFIER_RAN" != "true" ]; then
        echo "  INFRA ERROR (verifier never reached a verdict — not scored)" >&2
    elif [ "$checkpoint_passed" = "true" ]; then
        PASSED=$((PASSED + 1))
        echo "  PASS (score=$checkpoint_score)" >&2
    else
        echo "  FAIL (score=$checkpoint_score)" >&2
    fi

    # Accumulate weighted score using awk for float math
    WEIGHTED_SCORE=$(awk "BEGIN { printf \"%.4f\", $WEIGHTED_SCORE + ($checkpoint_score * $weight) }")

    # Build checkpoint result JSON entry (detail is already a quoted JSON
    # string). name comes from an agent-writable filename in .verifiers/ —
    # escape it the same way repo basenames are escaped above.
    #
    # verifier_ran is the attestation the scorer gates on. It is emitted here
    # because this is the only component that can observe whether the verifier
    # reached a verdict.
    entry=$(printf '{"name": "%s", "weight": %s, "score": %s, "passed": %s, "verifier_ran": %s, "detail": %s, "duration_ms": %d, "exit_code": %d}' \
        "$(json_escape "$name")" "$weight" "$checkpoint_score" "$checkpoint_passed" "$VERIFIER_RAN" "$checkpoint_detail" "$VERIFIER_DURATION_MS" "$VERIFIER_EXIT")

    if [ -z "$CHECKPOINT_RESULTS" ]; then
        CHECKPOINT_RESULTS="$entry"
    else
        CHECKPOINT_RESULTS="$CHECKPOINT_RESULTS, $entry"
    fi
    echo "" >&2
done

echo "Results: $PASSED/$TOTAL checkpoints passed" >&2

ALL_PASSED="false"
EXIT_CODE=1
if [ "$PASSED" -eq "$TOTAL" ] && [ "$TOTAL" -gt 0 ]; then
    ALL_PASSED="true"
    EXIT_CODE=0
fi

# Render the repos array as a JSON list. Must expand the whole array —
# `echo "$REPOS"` would emit only ${REPOS[0]} and silently drop every other
# repo from the output. The ${arr[@]+...} guard keeps this safe under set -u
# when no repos were discovered. Repo names come from discover_repos(), which
# globs agent-owned /workspace/*/ — escape each one via json_escape() before
# embedding, the same way verifier detail strings already are, so an agent
# cannot break out of the JSON array via a crafted directory name.
REPOS_JSON=$(
    for repo in ${REPOS[@]+"${REPOS[@]}"}; do
        json_escape "$repo"
        printf '\n'
    done | awk 'NF{printf "%s\"%s\"", (c++?", ":""), $0}'
)

# Build the result JSON once, then emit to both stdout and the results file.
RESULT_JSON=$(cat <<RESULT_JSON
{
  "task_score": $WEIGHTED_SCORE,
  "all_passed": $ALL_PASSED,
  "checkpoints_passed": $PASSED,
  "checkpoints_total": $TOTAL,
  "repos": [$REPOS_JSON],
  "checkpoints": [$CHECKPOINT_RESULTS]
}
RESULT_JSON
)

printf '%s\n' "$RESULT_JSON"
printf '%s\n' "$RESULT_JSON" > "$RESULTS_FILE"

exit $EXIT_CODE
