#!/usr/bin/env bash
# check_evidence.sh — checkpoint "evidence"
#
# Re-anchored to ground_truth.json:scoring_evidence[evidence]
# (EnterpriseBench-jn73.2.7.3.1.4). The gen-1 body was a byte-copy of
# dead-code-001's check grading ${WORKSPACE}/react/dead_code_report.json — not
# this task's instructed /workspace/dead_code_report.json — on concept
# keywords the prompt hands over. Credit now requires citing the
# create-react-app files that still reference the deprecated internals:
# full react-scripts paths at v5.0.1 the instruction never gives ("webpack
# configuration" and "build scripts" are prompt vocabulary; the paths are
# not). Structure alone earns 0.
# Env: WORKSPACE, TASK_DIR
set -euo pipefail

CHECKPOINT="evidence"
WORKSPACE="${WORKSPACE:-/workspace}"
REPORT="$WORKSPACE/dead_code_report.json"
if [[ ! -f "$REPORT" && -f "$WORKSPACE/agent_output/answer.json" ]]; then
  REPORT="$WORKSPACE/agent_output/answer.json"
fi
GT="${TASK_DIR:-}/ground_truth.json"
MAX_REPORT_BYTES=1048576

verdict() { printf '{"score": %s, "passed": %s, "detail": "%s"}\n' "$1" "$2" "$3"; exit 0; }

if [[ ! -f "$GT" ]]; then
  verdict 0.0 false "VERIFIER_INFRA_ERROR: ground_truth.json not found at $GT"
fi
if [[ -L "$REPORT" ]]; then
  verdict 0.0 false "dead_code_report.json is a symlink, not a regular file"
fi
if [[ ! -f "$REPORT" ]]; then
  verdict 0.0 false "No dead_code_report.json found"
fi
if [[ "$(wc -c <"$REPORT")" -gt "$MAX_REPORT_BYTES" ]]; then
  verdict 0.0 false "dead_code_report.json exceeds ${MAX_REPORT_BYTES} bytes"
fi

export REPORT GT CHECKPOINT
python3 -c '
import json, os

def verdict(score, detail):
    print(json.dumps({"score": round(score, 4), "passed": score >= 0.5, "detail": detail}))
    raise SystemExit(0)

with open(os.environ["GT"]) as fh:
    gt = json.load(fh)
evidence = (gt.get("scoring_evidence") or {}).get(os.environ["CHECKPOINT"]) or []
if not evidence:
    verdict(0.0, "VERIFIER_INFRA_ERROR: no scoring_evidence for " + os.environ["CHECKPOINT"])

with open(os.environ["REPORT"], encoding="utf-8", errors="replace") as fh:
    text = fh.read().lower()

found = sum(1 for token in evidence if token.lower() in text)
verdict(found / len(evidence),
        "Cited %d/%d create-react-app reference anchors" % (found, len(evidence)))
'
