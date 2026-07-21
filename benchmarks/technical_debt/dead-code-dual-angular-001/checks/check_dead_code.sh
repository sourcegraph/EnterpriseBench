#!/usr/bin/env bash
# check_dead_code.sh — checkpoint "dead_code_identified"
#
# Re-anchored to ground_truth.json:scoring_evidence[dead_code_identified]
# (EnterpriseBench-jn73.2.7.3.1.4). The gen-1 body paid 0.6 for schema-valid
# keys existing (symbol/category — structure, not investigation) and 0.4 for
# the literal package names '@angular/core|common|forms' the prompt hands
# over; the structured prompt echo scored 0.4. Credit now requires resolving
# the actual public-surface files at angular/angular@18.0.0
# (packages/<pkg>/public_api.ts — full paths the prompt never gives; it only
# says "public_api.ts files"). Structure alone earns 0.
# Env: WORKSPACE, TASK_DIR
set -euo pipefail

CHECKPOINT="dead_code_identified"
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
evidence = (gt.get("scoring_evidence") or {}).get(os.environ["CHECKPOINT"]) or []
if not evidence:
    verdict(0.0, "VERIFIER_INFRA_ERROR: no scoring_evidence for " + os.environ["CHECKPOINT"])

with open(os.environ["REPORT"], encoding="utf-8", errors="replace") as fh:
    text = fh.read().lower()

found = sum(1 for token in evidence if token.lower() in text)
verdict(found / len(evidence),
        "Resolved %d/%d framework public-surface anchors" % (found, len(evidence)))
'
