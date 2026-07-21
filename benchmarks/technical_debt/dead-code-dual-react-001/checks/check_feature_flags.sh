#!/usr/bin/env bash
# check_feature_flags.sh — checkpoint "feature_flags"
#
# Re-anchored to ground_truth.json:scoring_evidence[feature_flags]
# (EnterpriseBench-jn73.2.7.3.1.4). The gen-1 body was a byte-copy of
# dead-code-001's React check; this task's ground_truth.json has no
# "feature_flags" key, so the copy awarded an unconditional 1.0 to ANY
# deliverable — including the structured prompt echo. This task has no
# feature flags; the third graded axis is the cross-repo LINK the task is
# about: full credit only when a single report entry pairs an anchored
# react-side module (react_module) with an anchored create-react-app
# reference (cra_references) — values that require reading both pinned
# repos. Structure alone earns 0.
# Env: WORKSPACE, TASK_DIR
set -euo pipefail

CHECKPOINT="feature_flags"
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
react_anchors = [t.lower() for t in evidence if not t.startswith("packages/react-scripts")]
cra_anchors = [t.lower() for t in evidence if t.startswith("packages/react-scripts")]
if not react_anchors or not cra_anchors:
    verdict(0.0, "VERIFIER_INFRA_ERROR: incomplete scoring_evidence for " + os.environ["CHECKPOINT"])

try:
    with open(os.environ["REPORT"], encoding="utf-8", errors="replace") as fh:
        data = json.load(fh)
except (ValueError, UnicodeDecodeError):
    verdict(0.0, "deliverable is not valid JSON")

entries = data if isinstance(data, list) else data.get("dead_code", []) if isinstance(data, dict) else []
paired = False
react_seen = False
cra_seen = False
for e in entries:
    if not isinstance(e, dict):
        continue
    module = str(e.get("react_module", "")).lower()
    refs = e.get("cra_references", [])
    refs = [str(r).lower() for r in refs] if isinstance(refs, list) else []
    e_react = any(a in module for a in react_anchors)
    e_cra = any(a in r for a in cra_anchors for r in refs)
    react_seen = react_seen or e_react
    cra_seen = cra_seen or e_cra
    if e_react and e_cra:
        paired = True

if paired:
    verdict(1.0, "An entry pairs an anchored react module with an anchored CRA reference")
if react_seen and cra_seen:
    verdict(0.5, "Anchored react and CRA values present but never paired in one entry")
verdict(0.0, "No anchored cross-repo link (react_module + cra_references)")
'
