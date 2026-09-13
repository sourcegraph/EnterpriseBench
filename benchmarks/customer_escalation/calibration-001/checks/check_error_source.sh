#!/usr/bin/env bash
# check_error_source.sh — verify agent identified the correct source file + function.
#
# Canonical template for required_files matching (EnterpriseBench-6py4v).
# Scoring is precision-aware and path-shape-validated via the shared
# eb_verify.plugins.path_match primitive (which reuses the canonical
# eb_verify.scorers.file_extraction matcher): a guess/listing shotgun or a
# one-big-string file dump scores < 0.5, a targeted answer scores 1.0. Do NOT
# reintroduce the recall-only `gt in claimed` substring pattern here.
#
# Hardening mirrors check_root_cause.sh: reads only agent_output/, refuses a
# symlinked answer.json, caps its size before parsing, wraps every parse so a
# malformed agent answer scores a real 0.0 (never a verifier_infra_error free
# re-run), and routes a missing/broken ground truth through VERIFIER_INFRA_ERROR.
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

# The answer key is ours, not the agent's: a missing key means the verifier did
# not really run, so route it to re-run rather than book it as an agent 0.0.
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

# Locate the repo lib/ (4 levels up from this checks/ dir).
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

export ANSWER_FILE GT_FILE

python3 - "$LIB_DIR" <<'PYEOF'
import json, os, sys

def verdict(score, passed, detail):
    print(json.dumps({"score": score, "passed": passed, "detail": detail}))
    raise SystemExit(0)

lib_dir = sys.argv[1]
if lib_dir:
    sys.path.append(lib_dir)

try:
    from eb_verify.plugins.path_match import extract_claimed_paths, path_match_score
except Exception as exc:  # import miss => our infra, not the agent's fault
    verdict(0.0, False, f"VERIFIER_INFRA_ERROR: cannot import path_match: {exc}")

try:
    with open(os.environ["GT_FILE"]) as fh:
        gt = json.load(fh)
except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
    verdict(0.0, False, f"VERIFIER_INFRA_ERROR: ground_truth.json unreadable: {exc}")

try:
    with open(os.environ["ANSWER_FILE"]) as fh:
        answer = json.load(fh)
except (json.JSONDecodeError, UnicodeDecodeError) as exc:
    verdict(0.0, False, f"answer.json is not valid JSON: {exc}")
if not isinstance(answer, dict):
    verdict(0.0, False, "answer.json is not a JSON object")


def _paths(entries):
    out = []
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict):
            p = entry.get("path", entry.get("file", ""))
        elif isinstance(entry, str):
            p = entry
        else:
            p = ""
        if isinstance(p, str) and p.strip():
            out.append(p.strip())
    return out


required = _paths(gt.get("required_files", []))
sufficient = _paths(gt.get("sufficient_files", []))
if not required:
    verdict(0.0, False, "VERIFIER_INFRA_ERROR: no required_files in ground_truth.json")

# Claimed paths: STRUCTURED list unioned across the fields agents actually emit
# (source_files/files/code_paths/error_source.files) via the shared extractor,
# so this script and the answer plugin cannot drift. No free-text tokenisation.
claimed = extract_claimed_paths(answer)

if not claimed:
    verdict(0.0, False, "Agent provided no files")

score = round(path_match_score(claimed, required, sufficient=sufficient), 2)
verdict(
    score,
    score >= 0.5,
    f"Matched {score:.2f} of {len(required)} required source files (precision-aware)",
)
PYEOF
