#!/usr/bin/env bash
# check_error_source.sh — verify agent identified the correct source file(s)
# Reads agent output from $WORKSPACE/agent_output/answer.json
# Compares identified files against ground truth required_files.
# Safe template (bead 0rv.23): agent-controlled answer.json flows only through
# the environment/JSON file and is never interpolated into Python source.
set -euo pipefail

export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"
export GT_FILE="$TASK_DIR/ground_truth.json"

# Missing ground_truth.json is an infrastructure failure (non-zero exit).
if [[ ! -f "$GT_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "VERIFIER_INFRA_ERROR: no ground_truth.json"}'
    exit 1
fi

# Missing answer.json is an agent failure (score 0, exit 0).
if [[ ! -f "$ANSWER_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 0
fi

python3 -c '
import json, os, sys


def _load(path):
    with open(path) as fh:
        return json.load(fh)


def _paths(items):
    out = []
    for f in items:
        if isinstance(f, dict):
            p = f.get("path", f.get("file", ""))
        elif isinstance(f, str):
            p = f
        else:
            p = ""
        if isinstance(p, str) and p.strip():
            out.append(p.strip())
    return out


# --- Ground truth (infra-owned) ---
try:
    gt = _load(os.environ["GT_FILE"])
except (json.JSONDecodeError, OSError, ValueError) as e:
    print(json.dumps({"score": 0.0, "passed": False,
                      "detail": "VERIFIER_INFRA_ERROR: unreadable ground_truth.json (%s)" % e}))
    sys.exit(1)

req = gt.get("required_files") if isinstance(gt, dict) else None
if not isinstance(req, list):
    print(json.dumps({"score": 0.0, "passed": False,
                      "detail": "VERIFIER_INFRA_ERROR: required_files is not a list"}))
    sys.exit(1)
# Accepts list-of-dicts ({"path": ...}) OR a flat list of strings.
gt_files = _paths(req)

# --- Agent answer (agent-owned) ---
try:
    answer = _load(os.environ["ANSWER_FILE"])
except Exception:
    # answer.json is agent-controlled and untrusted. Catch-all (not the
    # narrow GT tuple above) is deliberate: a pathological payload raises
    # RecursionError/MemoryError (a RuntimeError, NOT a ValueError) which a
    # narrow except would leak, re-crashing this check to empty stdout --
    # the exact no-verdict failure this template exists to prevent.
    print(json.dumps({"score": 0.0, "passed": False, "detail": "Malformed answer.json: not a JSON object"}))
    sys.exit(0)
if not isinstance(answer, dict):
    answer = {}

files = answer.get("source_files")
if files is None:
    files = answer.get("files")
if files is None:
    es = answer.get("error_source")
    files = es.get("files") if isinstance(es, dict) else None

# A bare-string source_files is normalized to a one-element list so _paths
# stays the single source of the strip/drop-empty rule for both shapes.
if isinstance(files, str):
    files = [files]
agent_files = _paths(files) if isinstance(files, list) else []

if not gt_files:
    print(json.dumps({"score": 0.0, "passed": False, "detail": "No GT files"}))
    sys.exit(0)

# Anchored match only: exact, or a suffix on a path boundary. An unanchored
# substring ("gt is a substring of af") would let path-spam soft-cheat.
found = sum(1 for gt_f in gt_files
            if any(af == gt_f or af.endswith("/" + gt_f) for af in agent_files))
score = found / len(gt_files)
print(json.dumps({"score": round(score, 2), "passed": score >= 0.5,
                  "detail": "Found %d/%d required source files" % (found, len(gt_files))}))
'
