#!/usr/bin/env bash
# check_config_valid.sh — checkpoint "config_valid"
#
# Grades DRIFT_REPORT.json against ground_truth.json:scoring_evidence
# (sealed root-only), verified at terraform v1.7.0 and
# terraform-provider-aws v5.31.0.
#
# What changed and why: the old check paid 1.0 for any drift_points entry that
# merely HAD a config_key field — the key the prompt's own answer template
# spells out — so a schema-shaped payload earned full credit with zero repo
# access, and even the shape-mirroring echo picked up 0.3 (the
# check_config_valid exploit class, EnterpriseBench-jn73.2.7.3.1.4). Structure
# alone now earns 0. A report is "valid" only if it is grounded in the pinned
# repos: at least one entry must cite a real core file and one a real provider
# file from the ground-truth allowlists. The prompt names no file — and the
# old ground truth's eval_diff.go / resource_aws_security_group.go do not
# exist at the pinned revs, so guessing from stale layout knowledge fails too.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPORT="$WORKSPACE/DRIFT_REPORT.json"
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
    print(json.dumps({"score": round(score, 2), "passed": score >= 1.0, "detail": detail}))
    raise SystemExit(0)

with open(os.environ["GT"]) as fh:
    evidence = json.load(fh).get("scoring_evidence") or {}
core_files = [t.lower() for t in evidence.get("config_valid_core_files") or []]
provider_files = [t.lower() for t in evidence.get("config_valid_provider_files") or []]
if not core_files or not provider_files:
    verdict(0.0, "VERIFIER_INFRA_ERROR: no config_valid file allowlists in ground_truth.json")

try:
    with open(os.environ["REPORT"], encoding="utf-8", errors="replace") as fh:
        report = json.load(fh)
except (json.JSONDecodeError, UnicodeDecodeError) as exc:
    verdict(0.0, f"DRIFT_REPORT.json is not valid JSON: {exc}")
if not isinstance(report, dict):
    verdict(0.0, "DRIFT_REPORT.json is not a JSON object")

points = report.get("drift_points")
if not isinstance(points, list) or not points:
    verdict(0.0, "DRIFT_REPORT.json has no drift_points array")

def norm(value):
    return re.sub(r"\s+", "", json.dumps(value) if not isinstance(value, str) else value).lower()

dict_points = [p for p in points if isinstance(p, dict)]
has_core = any(
    any(t in norm(p.get("terraform_core_file", "")) for t in core_files)
    for p in dict_points
)
has_provider = any(
    any(t in norm(p.get("provider_file", "")) for t in provider_files)
    for p in dict_points
)

score = 0.5 * has_core + 0.5 * has_provider
verdict(score,
        "Report grounded in pinned repos: real core file cited=%s, real provider file cited=%s"
        % (has_core, has_provider))
'
