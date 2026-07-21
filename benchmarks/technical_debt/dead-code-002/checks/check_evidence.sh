#!/usr/bin/env bash
# check_evidence.sh — checkpoint "check_evidence"
#
# Re-anchored to ground_truth.json:scoring_evidence[check_evidence]
# (EnterpriseBench-jn73.2.7.3.1.4). The gen-1 body credited any non-empty
# `evidence` string plus concept keywords the prompt already hands the agent
# ("dead", "unused", "permanently"), so a schema-valid deliverable whose only
# content was instruction.md scored 0.95. Credit now requires repo-specific
# investigation anchors — the reconciler/server/client files carrying the dead
# REACT_PROVIDER_TYPE branches at the pinned rev, none of which instruction.md
# names. Structure alone earns 0.
# Env: WORKSPACE, TASK_DIR
set -euo pipefail

CHECKPOINT="check_evidence"
WORKSPACE="${WORKSPACE:-/workspace}"
REPORT="$WORKSPACE/react/dead_code_report.json"
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
        "Cited %d/%d non-prompt evidence anchors" % (found, len(evidence)))
'
