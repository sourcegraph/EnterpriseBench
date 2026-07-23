#!/usr/bin/env bash
# check_drift_points.sh — checkpoint "identify_drift_points"
#
# Grades DRIFT_REPORT.json against ground_truth.json:drift_points, which is
# sealed root-only and was curated from bitnami/charts PR #33114 and verified
# against the template at the pinned SHA.
#
# What changed and why: this check used to credit a drift point whose file/key
# merely mentioned "serflan" or "serfwan". instruction.md hands the agent both
# port names, the service name and the file list, so a DRIFT_REPORT.json
# fabricated from the prompt alone -- no repo access -- scored 1.0 here (and
# 1.0 on both siblings: the whole task was solvable from its own prompt). The
# md-echo gate could not see it: a `cp instruction.md` copy is not valid JSON,
# so json.load threw and the check "passed" at 0.0 (EnterpriseBench-jn73.2.7.3.1.4).
#
# So identification now requires the swap DIRECTION, which exists only in the
# template: each drift point must name the wrong expression it currently
# resolves to. Of the graded tokens, "containerports" and "serfwan-udp" appear
# ZERO times in instruction.md -- the prompt names only serflan-udp, so an agent
# that has not read the template does not know the second UDP entry drifted at
# all, nor that the values key is containerPorts.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPORT="$WORKSPACE/agent_output/DRIFT_REPORT.json"
GT="${TASK_DIR:-}/ground_truth.json"
MAX_REPORT_BYTES=1048576

verdict() { printf '{"score": %s, "passed": %s, "detail": "%s"}\n' "$1" "$2" "$3"; exit 0; }

if [[ ! -f "$GT" ]]; then
  verdict 0.0 false "VERIFIER_INFRA_ERROR: ground_truth.json not found at $GT"
fi
if [[ -L "$REPORT" ]]; then
  verdict 0.0 false "DRIFT_REPORT.json is a symlink, not a regular file"
fi
if [[ ! -f "$REPORT" ]]; then
  verdict 0.0 false "DRIFT_REPORT.json not found"
fi
if [[ "$(wc -c <"$REPORT")" -gt "$MAX_REPORT_BYTES" ]]; then
  verdict 0.0 false "DRIFT_REPORT.json exceeds ${MAX_REPORT_BYTES} bytes"
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
    verdict(0.0, f"DRIFT_REPORT.json is not valid JSON: {exc}")
if not isinstance(report, dict):
    verdict(0.0, "DRIFT_REPORT.json is not a JSON object")

claimed = report.get("drift_points")
if not isinstance(claimed, list) or not claimed:
    verdict(0.0, "DRIFT_REPORT.json has no drift_points array")

def norm(value):
    """Flatten a field to comparable text. An agent may hand back a string, a
    nested dict, or a list; compare content, not the shape it chose."""
    return re.sub(r"\s+", "", json.dumps(value) if not isinstance(value, str) else value).lower()

found, detail = 0, []
for want in expected:
    tokens = want.get("evidence_tokens") or {}
    ident = [t.lower() for t in tokens.get("identifies", [])]
    actual_tokens = [t.lower() for t in tokens.get("actual", [])]
    hit = False
    for point in claimed:
        if not isinstance(point, dict):
            continue
        where = norm(point.get("key", "")) + norm(point.get("file", ""))
        if not all(t in where for t in ident):
            continue
        # The identifying name alone is not evidence — the prompt supplies
        # serflan-udp. Credit only if the point also carries the expression it
        # actually resolves to, which is readable only in the template.
        if all(t in norm(point.get("actual", "")) for t in actual_tokens):
            hit = True
            break
    found += hit
    detail.append(str(want.get("key")) + ("=hit" if hit else "=miss"))

verdict(found / len(expected),
        "Identified %d/%d real drift points with the actual (wrong) expression: %s"
        % (found, len(expected), ", ".join(detail)))
'
