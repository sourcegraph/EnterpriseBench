#!/usr/bin/env bash
# check_root_cause.sh — verify agent identified the tar extraction fragility
#
# Evidence is the agent's own answer.json, matched against the answer key's
# required_files. Two properties this check must keep:
#
#   * It reads nothing but agent_output/. run_task.py plants
#     /workspace/instruction.md and chowns it to the agent, so a scan of
#     $WORKSPACE grades the question as the answer — a no-op agent scored 1.00.
#   * It matches answer-key paths, never prompt vocabulary. instruction.md
#     describes the bug in prose and names no files, and ground_truth.json is
#     sealed root-only (run_task.py GRADING_PATHS), so the paths below are
#     reachable only by an agent that actually read the repo. Grepping for
#     tokens the prompt supplies would credit an echo of the prompt.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
ANSWER_FILE="$WORKSPACE/agent_output/answer.json"
GT_FILE="${TASK_DIR:-}/ground_truth.json"

# A findings file is a few KB. Anything larger is a grader-DoS, not an answer.
MAX_ANSWER_BYTES=1048576

verdict() {
    printf '{"score": %s, "passed": %s, "detail": "%s"}\n' "$1" "$2" "$3"
    exit 0
}

# The answer key is ours, not the agent's: if it is missing the verifier did not
# really run, and reporting that as a legitimate 0.0 would bury a broken grader
# under an agent failure. Route it to re-run instead.
if [[ ! -f "$GT_FILE" ]]; then
    verdict 0.0 false "VERIFIER_INFRA_ERROR: ground_truth.json not found at $GT_FILE"
fi

# agent_output/ is agent-owned, so answer.json is agent-controlled: refuse a
# symlink rather than read whatever it aims at.
if [[ -L "$ANSWER_FILE" ]]; then
    verdict 0.0 false "answer.json is a symlink, not a regular file"
fi
if [[ ! -f "$ANSWER_FILE" ]]; then
    verdict 0.0 false "No answer.json found"
fi
if [[ "$(wc -c <"$ANSWER_FILE")" -gt "$MAX_ANSWER_BYTES" ]]; then
    verdict 0.0 false "answer.json exceeds ${MAX_ANSWER_BYTES} bytes"
fi

export ANSWER_FILE GT_FILE

# Locate the repo lib/ (4 levels up from this checks/ dir) so scoring uses the
# shared precision-aware eb_verify.plugins.path_match primitive
# (EnterpriseBench-6py4v) instead of the recall-only substring pattern.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LIB_DIR="$(cd "$SCRIPT_DIR/../../../../lib" 2>/dev/null && pwd || echo "")"
# Trust the fallback only if it carries the module we import: a stale
# eb_verify four levels up (a -d check would wave through) must not shadow
# the PYTHONPATH the harness exports. See EnterpriseBench-4du6t.
[[ -f "$LIB_DIR/eb_verify/plugins/path_match.py" ]] || LIB_DIR=""
# ...and only when the harness told us where lib/ is at all. The -f guard above
# authenticates PRESENCE, not CONTENT: with PYTHONPATH set, the guessed dir is
# appended BEHIND the authoritative lib and cannot win. With PYTHONPATH empty it
# IS the import source, so an eb_verify planted four levels up would be imported
# unchallenged and could forge a passing verdict. Fail closed there; a human
# running a check by hand outside the harness opts back in explicitly.
# See EnterpriseBench-0idmk.
if [[ -z "${PYTHONPATH:-}" && "${EB_ALLOW_GUESSED_LIB:-}" != "1" ]]; then
    LIB_DIR=""
fi

python3 - "$LIB_DIR" <<'PYEOF'
import json, os, sys

def verdict(score, detail):
    print(json.dumps({"score": round(score, 2), "passed": score >= 0.5, "detail": detail}))
    raise SystemExit(0)

lib_dir = sys.argv[1]
if lib_dir:
    sys.path.append(lib_dir)

try:
    from eb_verify.plugins.path_match import extract_claimed_paths, path_match_score
except Exception as exc:  # import miss => our infra, not the agent's fault
    verdict(0.0, f"VERIFIER_INFRA_ERROR: cannot import path_match: {exc}")

with open(os.environ["GT_FILE"]) as fh:
    gt = json.load(fh)
required = [f["path"] for f in gt.get("required_files", []) if f.get("path")]
# sufficient_files are GT-blessed supporting evidence: neutral for scoring
# (excluded from the precision denominator), so a thorough answer that cites
# them is not penalised.
sufficient = [f["path"] for f in gt.get("sufficient_files", []) if isinstance(f, dict) and f.get("path")]
if not required:
    verdict(0.0, "VERIFIER_INFRA_ERROR: no required_files in ground_truth.json")

try:
    with open(os.environ["ANSWER_FILE"]) as fh:
        answer = json.load(fh)
except (json.JSONDecodeError, UnicodeDecodeError) as exc:
    verdict(0.0, f"answer.json is not valid JSON: {exc}")
if not isinstance(answer, dict):
    verdict(0.0, "answer.json is not a JSON object")

# Claimed paths come from the shared extractor: it unions the fields agents
# actually emit (source_files/files/code_paths/error_source.files), accepts bare
# strings or {"path"|"file": ...} objects, and treats a non-str path value as no
# path — so this script and the answer plugin cannot drift.
claimed = extract_claimed_paths(answer)

if not claimed:
    verdict(0.0, "answer.json names no source files")

score = path_match_score(claimed, required, sufficient=sufficient)
verdict(score, f"Matched {score:.2f} of {len(required)} required source files (precision-aware)")
PYEOF
