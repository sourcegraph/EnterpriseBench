#!/usr/bin/env bash
# check_expected_values.sh — checkpoint "expected_values"
#
# Grades DRIFT_REPORT.json against ground_truth.json:scoring_evidence (sealed
# root-only), verified at terraform v1.7.0 and terraform-provider-aws v5.31.0.
#
# What changed and why: the old check paid for the mere PRESENCE of
# terraform_core_file / provider_file / impact keys — keys the prompt's own
# answer template spells out — so any schema-shaped payload with non-empty
# strings earned 1.0 with zero repo access (key-presence credit, the exploit
# class of EnterpriseBench-jn73.2.7.3.1.4). The root-cause explanation must now
# be grounded on both sides: a real core plan/diff file cited in
# terraform_core_file (the prompt names no file, and the old eval_diff.go /
# eval_refresh.go do not exist at v1.7.0), the core comparison machinery
# (objchange: ProposedNew at objchange.go:31, AssertObjectCompatible at
# compatible.go:29), and the provider normalization machinery
# (StateFunc structure.NormalizeJsonString + DiffSuppressOnRefresh at
# policy.go:85-91, verify.PolicyToSet at policy.go:208).
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
    print(json.dumps({"score": round(score, 2), "passed": score >= 0.7, "detail": detail}))
    raise SystemExit(0)

with open(os.environ["GT"]) as fh:
    evidence = json.load(fh).get("scoring_evidence") or {}
core_files = [t.lower() for t in evidence.get("config_valid_core_files") or []]
core_mech = [t.lower() for t in evidence.get("expected_values_core_mechanism") or []]
provider_mech = [t.lower() for t in evidence.get("expected_values_provider_mechanism") or []]
if not core_files or not core_mech or not provider_mech:
    verdict(0.0, "VERIFIER_INFRA_ERROR: no expected_values scoring_evidence in ground_truth.json")

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
whole = norm(dict_points)

has_core_file = any(
    any(t in norm(p.get("terraform_core_file", "")) for t in core_files)
    for p in dict_points
)
has_core_mech = any(t in whole for t in core_mech)
has_provider_mech = any(t in whole for t in provider_mech)

score = 0.4 * has_core_file + 0.3 * has_core_mech + 0.3 * has_provider_mech
verdict(score,
        "Root cause grounded: real core file=%s, core comparison machinery=%s, provider normalization machinery=%s"
        % (has_core_file, has_core_mech, has_provider_mech))
'
