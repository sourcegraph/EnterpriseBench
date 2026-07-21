#!/usr/bin/env bash
# check_cross_repo_usage.sh — checkpoint "cross_repo_usage"
#
# Re-anchored to ground_truth.json:scoring_evidence[cross_repo_usage]
# (EnterpriseBench-jn73.2.7.3.1.4). The gen-1 body credited the literal
# strings 'public_api.ts', 'components', 'material', 'cdk' — all handed over
# by the prompt — so the structured prompt echo scored 0.6. Credit now splits
# 50/50 between the two repos' concrete surfaces: a full framework
# public_api.ts path at angular/angular@18.0.0, and concrete
# angular/components paths (src/cdk/ or src/material/ trees — the prompt only
# names the npm packages, never these paths). The category vocabulary
# (truly_dead/components_only/test_only) is prompt text and earns nothing.
# Env: WORKSPACE, TASK_DIR
set -euo pipefail

CHECKPOINT="cross_repo_usage"
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
framework_anchors = [t.lower() for t in evidence if not t.startswith("src/")]
components_anchors = [t.lower() for t in evidence if t.startswith("src/")]
if not framework_anchors or not components_anchors:
    verdict(0.0, "VERIFIER_INFRA_ERROR: incomplete scoring_evidence for " + os.environ["CHECKPOINT"])

with open(os.environ["REPORT"], encoding="utf-8", errors="replace") as fh:
    text = fh.read().lower()

has_framework = any(a in text for a in framework_anchors)
has_components = any(a in text for a in components_anchors)
score = 0.5 * has_framework + 0.5 * has_components
verdict(score, "framework surface anchored: %s, components tree anchored: %s"
        % (has_framework, has_components))
'
