#!/usr/bin/env bash
# check_drift_points.sh — checkpoint "drift_points"
#
# Grades DRIFT_REPORT.json against ground_truth.json:drift_points (sealed
# root-only), verified at terraform v1.7.0 and terraform-provider-aws v5.31.0.
#
# What changed and why: the old check counted concept words the prompt itself
# hands over (json, normaliz, policy, security_group, phantom, set, list,
# ordering, default), so the shape-mirroring prompt echo scored 1.0 with zero
# repo access (EnterpriseBench-jn73.2.7.3.1.4). A drift point now earns credit
# only when it carries what only the pinned provider tree shows: the real Read
# implementation path (internal/service/iam/policy.go,
# internal/service/ec2/vpc_security_group.go — the prompt names no file, and
# the pre-v4 aws/resource_aws_* layout no longer exists) plus the schema hook
# that does the normalization (verify.SuppressEquivalentPolicyDiffs at
# policy.go:85, SecurityGroupRuleHash as the rule-set hash at
# vpc_security_group.go:128).
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
    located = [t.lower() for t in tokens.get("located", [])]
    mechanism = [t.lower() for t in tokens.get("mechanism", [])]
    hit = False
    for point in claimed:
        if not isinstance(point, dict):
            continue
        whole = norm(point)
        if not all(t in whole for t in ident):
            continue
        # The resource name is prompt vocabulary. Credit requires the real
        # provider Read path in provider_file AND the normalization hook that
        # only the pinned tree shows.
        if all(t in norm(point.get("provider_file", "")) for t in located) and all(
            t in whole for t in mechanism
        ):
            hit = True
            break
    found += hit
    detail.append(str(want.get("config_key")) + ("=hit" if hit else "=miss"))

verdict(found / len(expected),
        "Identified %d/%d drift points with the real provider file and normalization hook: %s"
        % (found, len(expected), ", ".join(detail)))
'
