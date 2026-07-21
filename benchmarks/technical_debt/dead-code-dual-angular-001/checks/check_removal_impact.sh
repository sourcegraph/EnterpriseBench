#!/usr/bin/env bash
# check_removal_impact.sh — checkpoint "removal_impact"
#
# Re-anchored to ground_truth.json:scoring_evidence[removal_impact]
# (EnterpriseBench-jn73.2.7.3.1.4). The gen-1 body was pure key-presence
# credit: ANY removal_impact string longer than 5 chars plus ANY non-empty
# components_usage list scored 1.0 — schema-valid structure with garbage
# values earned full credit. Credit now requires values: a components_usage
# path inside the real angular/components trees (src/cdk/ or src/material/ —
# paths the prompt never gives), and the removal_impact prose only counts on
# an entry that carries such an anchored path. Structure alone earns 0.
# Env: WORKSPACE, TASK_DIR
set -euo pipefail

CHECKPOINT="removal_impact"
WORKSPACE="${WORKSPACE:-/workspace}"
REPORT="$WORKSPACE/agent_output/answer.json"
if [[ ! -f "$REPORT" && -f "$WORKSPACE/DEAD_CODE_REPORT.json" ]]; then
  REPORT="$WORKSPACE/DEAD_CODE_REPORT.json"
fi
GT="${TASK_DIR:-}/ground_truth.json"
MAX_REPORT_BYTES=1048576

verdict() { printf '{"score": %s, "passed": %s, "detail": "%s"}\n' "$1" "$2" "$3"; exit 0; }

if [[ ! -f "$GT" ]]; then
  verdict 0.0 false "VERIFIER_INFRA_ERROR: ground_truth.json not found at $GT"
fi
if [[ -L "$REPORT" ]]; then
  verdict 0.0 false "answer is a symlink, not a regular file"
fi
if [[ ! -f "$REPORT" ]]; then
  verdict 0.0 false "No answer.json found"
fi
if [[ "$(wc -c <"$REPORT")" -gt "$MAX_REPORT_BYTES" ]]; then
  verdict 0.0 false "answer exceeds ${MAX_REPORT_BYTES} bytes"
fi

export REPORT GT CHECKPOINT
python3 -c '
import json, os

def verdict(score, detail):
    print(json.dumps({"score": round(score, 4), "passed": score >= 0.5, "detail": detail}))
    raise SystemExit(0)

with open(os.environ["GT"]) as fh:
    gt = json.load(fh)
anchors = [t.lower() for t in
           (gt.get("scoring_evidence") or {}).get(os.environ["CHECKPOINT"]) or []]
if not anchors:
    verdict(0.0, "VERIFIER_INFRA_ERROR: no scoring_evidence for " + os.environ["CHECKPOINT"])

try:
    with open(os.environ["REPORT"], encoding="utf-8", errors="replace") as fh:
        data = json.load(fh)
except (ValueError, UnicodeDecodeError):
    verdict(0.0, "answer is not valid JSON")

exports = data.get("dead_exports", data.get("exports", data.get("symbols", []))) \
    if isinstance(data, dict) else []
anchored_usage = False
anchored_with_impact = False
for e in (exports if isinstance(exports, list) else []):
    if not isinstance(e, dict):
        continue
    usage = e.get("components_usage", [])
    usage = [str(u).lower() for u in usage] if isinstance(usage, list) else []
    e_anchored = any(a in u for a in anchors for u in usage)
    anchored_usage = anchored_usage or e_anchored
    if e_anchored and len(str(e.get("removal_impact", ""))) > 5:
        anchored_with_impact = True

if anchored_with_impact:
    verdict(1.0, "Anchored components_usage with removal_impact documented on the same entry")
if anchored_usage:
    verdict(0.5, "Anchored components_usage present but its entry lacks removal_impact")
verdict(0.0, "No components_usage path inside the real components trees")
'
