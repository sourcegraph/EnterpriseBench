#!/usr/bin/env bash
# check_affected_components.sh — checkpoint "affected_resources"
#
# Re-anchored to ground_truth.json:scoring_evidence[affected_resources]
# (EnterpriseBench-jn73.2.7.3.1). Credits ONLY non-prompt evidence — tokens
# absent from instruction.md and present in expected_solution.json — so a
# verbatim prompt copy scores 0. ground_truth.json is sealed root-only.
set -euo pipefail

CHECKPOINT="affected_resources"
WORKSPACE="${WORKSPACE:-/workspace}"
REPORT="$WORKSPACE/agent_output/INCIDENT_REPORT.md"
GT="${TASK_DIR:-}/ground_truth.json"
MAX_REPORT_BYTES=1048576

verdict() { printf '{"score": %s, "passed": %s, "detail": "%s"}\n' "$1" "$2" "$3"; exit 0; }

if [[ ! -f "$GT" ]]; then
  verdict 0.0 false "VERIFIER_INFRA_ERROR: ground_truth.json not found at $GT"
fi
if [[ -L "$REPORT" ]]; then
  verdict 0.0 false "INCIDENT_REPORT.md is a symlink, not a regular file"
fi
if [[ ! -f "$REPORT" ]]; then
  verdict 0.0 false "INCIDENT_REPORT.md not found"
fi
if [[ "$(wc -c <"$REPORT")" -gt "$MAX_REPORT_BYTES" ]]; then
  verdict 0.0 false "INCIDENT_REPORT.md exceeds ${MAX_REPORT_BYTES} bytes"
fi

export REPORT GT CHECKPOINT
python3 -c '
import json, os

def verdict(score, detail):
    print(json.dumps({"score": round(score, 2), "passed": score >= 0.5, "detail": detail}))
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
        "Cited %d/%d non-prompt evidence tokens for %s" % (found, len(evidence), os.environ["CHECKPOINT"]))
'
