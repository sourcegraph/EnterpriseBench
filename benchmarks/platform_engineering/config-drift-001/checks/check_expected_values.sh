#!/usr/bin/env bash
# check_expected_values.sh — checkpoint "determine_expected_values"
#
# Grades DRIFT_REPORT.json against ground_truth.json:drift_points (sealed
# root-only), curated from bitnami/charts PR #33114 and verified at the pinned SHA.
#
# The old check credited an expected value that merely mentioned "serflan" for a
# key mentioning "serflan" — tautological, and pure prompt vocabulary besides:
# instruction.md names both ports, so "serflan-udp should use serfLAN" is
# derivable without opening the chart. It is also the obvious guess, which is
# the point: a checkpoint a fabricator wins by guessing measures nothing.
#
# The expected value must now name the values.yaml key path it resolves through.
# "containerports" appears ZERO times in instruction.md — the prompt says
# "values.yaml" but never which key — so citing containerPorts.serfLAN requires
# having read the chart (EnterpriseBench-jn73.2.7.3.1.4).
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
    return re.sub(r"\s+", "", json.dumps(value) if not isinstance(value, str) else value).lower()

found, detail = 0, []
for want in expected:
    tokens = want.get("evidence_tokens") or {}
    ident = [t.lower() for t in tokens.get("identifies", [])]
    expected_tokens = [t.lower() for t in tokens.get("expected", [])]
    hit = False
    for point in claimed:
        if not isinstance(point, dict):
            continue
        where = norm(point.get("key", "")) + norm(point.get("file", ""))
        if not all(t in where for t in ident):
            continue
        if all(t in norm(point.get("expected", "")) for t in expected_tokens):
            hit = True
            break
    found += hit
    detail.append(str(want.get("key")) + ("=hit" if hit else "=miss"))

verdict(found / len(expected),
        "Correct expected value (via the containerPorts key path) for %d/%d drift points: %s"
        % (found, len(expected), ", ".join(detail)))
'
