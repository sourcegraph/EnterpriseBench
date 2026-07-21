#!/usr/bin/env bash
# check_drift_points.sh — checkpoint "identify_drift_points"
#
# Grades answer.json against ground_truth.json:drift_points (sealed root-only),
# verified against pallets/click at the pinned tag 8.2.1.
#
# What changed and why: this check used to credit any answer whose text
# contained the ground-truth key labels, and its sibling paid for prompt
# vocabulary — a schema-shaped answer fabricated from instruction.md alone
# earned partial credit with zero repo access
# (EnterpriseBench-jn73.2.7.3.1.4). Identification now requires locating each
# drift point in the real resolution machinery: the point must name
# src/click/core.py. The prompt says only "core.py" — the src/click/ layout is
# readable only in the repo.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPORT="$WORKSPACE/agent_output/answer.json"
GT="${TASK_DIR:-}/ground_truth.json"
MAX_REPORT_BYTES=1048576

verdict() { printf '{"score": %s, "passed": %s, "detail": "%s"}\n' "$1" "$2" "$3"; exit 0; }

if [[ ! -f "$GT" ]]; then
  verdict 0.0 false "VERIFIER_INFRA_ERROR: ground_truth.json not found at $GT"
fi
if [[ -L "$REPORT" ]]; then
  verdict 0.0 false "answer.json is a symlink, not a regular file"
fi
if [[ ! -f "$REPORT" ]]; then
  verdict 0.0 false "answer.json not found"
fi
if [[ "$(wc -c <"$REPORT")" -gt "$MAX_REPORT_BYTES" ]]; then
  verdict 0.0 false "answer.json exceeds ${MAX_REPORT_BYTES} bytes"
fi

export REPORT GT
python3 -c '
import json, os, re

def verdict(score, detail):
    print(json.dumps({"score": round(score, 2), "passed": score >= 0.5, "detail": detail}))
    raise SystemExit(0)

with open(os.environ["GT"]) as fh:
    expected = json.load(fh).get("drift_points") or []
if not expected:
    verdict(0.0, "VERIFIER_INFRA_ERROR: no drift_points in ground_truth.json")

try:
    with open(os.environ["REPORT"], encoding="utf-8", errors="replace") as fh:
        report = json.load(fh)
except (json.JSONDecodeError, UnicodeDecodeError) as exc:
    verdict(0.0, f"answer.json is not valid JSON: {exc}")
if not isinstance(report, dict):
    verdict(0.0, "answer.json is not a JSON object")

claimed = report.get("drift_points")
if not isinstance(claimed, list) or not claimed:
    verdict(0.0, "answer.json has no drift_points array")

def norm(value):
    """Flatten a field to comparable text. An agent may hand back a string, a
    nested dict, or a list; compare content, not the shape it chose."""
    return re.sub(r"\s+", "", json.dumps(value) if not isinstance(value, str) else value).lower()

found, detail = 0, []
for want in expected:
    tokens = want.get("evidence_tokens") or {}
    ident = [t.lower() for t in tokens.get("identifies", [])]
    located = [t.lower() for t in tokens.get("located", [])]
    hit = False
    for point in claimed:
        if not isinstance(point, dict):
            continue
        whole = norm(point)
        if not all(t in whole for t in ident):
            continue
        # Naming the parameter source is not evidence: instruction.md supplies
        # every source name. Credit only a point that also locates the drift
        # in the real file, whose src/ path is readable only in the repo.
        if all(t in norm(point.get("file", "")) for t in located):
            hit = True
            break
    found += hit
    detail.append(str(want.get("key")) + ("=hit" if hit else "=miss"))

verdict(found / len(expected),
        "Identified %d/%d drift points located in the real resolution source file: %s"
        % (found, len(expected), ", ".join(detail)))
'
