#!/usr/bin/env bash
# test_runner.sh — Cross-repo test runner for EnterpriseBench tasks.
# Executes checkpoint verifiers and produces structured JSON output.
#
# Usage: /workspace/test.sh [checkpoint_name]
#
# Output: JSON object with per-checkpoint results and aggregate score.
# Exit code: 0 if all checkpoints pass, 1 otherwise.
set -uo pipefail

WORKSPACE="/workspace"
VERIFIER_DIR="$WORKSPACE/.verifiers"
RESULTS_FILE="$WORKSPACE/.results.json"

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

# Run a single verifier, capture JSON output and exit code.
# Applies timeout if TIMEOUT_SECONDS is set.
# Returns: sets VERIFIER_EXIT, VERIFIER_JSON, VERIFIER_DURATION_MS
run_verifier() {
    local verifier_path="$1"
    local timeout_sec="${2:-120}"
    local start end

    start=$(now_ms)

    # Run with timeout; capture stdout (expected JSON), stderr for diagnostics
    local raw_stdout raw_stderr
    raw_stderr=$(mktemp)

    # Capture exit code before || true to avoid masking real failures
    local raw_stdout_file
    raw_stdout_file=$(mktemp)

    if command -v timeout >/dev/null 2>&1; then
        timeout "$timeout_sec" bash "$verifier_path" >"$raw_stdout_file" 2>"$raw_stderr"
        VERIFIER_EXIT=$?
    else
        bash "$verifier_path" >"$raw_stdout_file" 2>"$raw_stderr"
        VERIFIER_EXIT=$?
    fi

    raw_stdout=$(cat "$raw_stdout_file")
    rm -f "$raw_stdout_file"

    end=$(now_ms)
    VERIFIER_DURATION_MS=$(( end - start ))

    # Timeout returns exit code 124
    if [ "$VERIFIER_EXIT" -eq 124 ]; then
        VERIFIER_JSON="{\"score\": 0.0, \"passed\": false, \"detail\": \"Timed out after ${timeout_sec}s\"}"
        rm -f "$raw_stderr"
        return
    fi

    # Try to parse stdout as JSON (check for opening brace)
    if printf '%s' "$raw_stdout" | grep -q '^{'; then
        VERIFIER_JSON="$raw_stdout"
    else
        # Fallback: exit 0 = pass, nonzero = fail (per schema spec)
        if [ "$VERIFIER_EXIT" -eq 0 ]; then
            VERIFIER_JSON="{\"score\": 1.0, \"passed\": true, \"detail\": \"$(json_escape "${raw_stdout:-ok}")\"}"
        else
            local stderr_content
            stderr_content=$(cat "$raw_stderr" 2>/dev/null || true)
            local detail="${raw_stdout:-$stderr_content}"
            VERIFIER_JSON="{\"score\": 0.0, \"passed\": false, \"detail\": \"$(json_escape "${detail:-exit code $VERIFIER_EXIT}")\"}"
        fi
    fi

    rm -f "$raw_stderr"
}

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

TOTAL=0
PASSED=0
CHECKPOINT_RESULTS=""
WEIGHTED_SCORE="0"

for verifier in "$VERIFIER_DIR"/*.sh; do
    [ -f "$verifier" ] || continue
    name=$(basename "$verifier" .sh)
    TOTAL=$((TOTAL + 1))

    # Read weight from companion .meta file if present, else default 1.0.
    # .verifiers/ is agent-writable (chowned before the agent's session
    # starts), so validate the value is a plain number before trusting it —
    # it is interpolated unquoted into both the result JSON and an awk
    # expression below, and either is an injection point for anything else.
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

    if [ "$checkpoint_passed" = "true" ]; then
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
    entry=$(printf '{"name": "%s", "weight": %s, "score": %s, "passed": %s, "detail": %s, "duration_ms": %d, "exit_code": %d}' \
        "$(json_escape "$name")" "$weight" "$checkpoint_score" "$checkpoint_passed" "$checkpoint_detail" "$VERIFIER_DURATION_MS" "$VERIFIER_EXIT")

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
