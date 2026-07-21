#!/usr/bin/env bash
# check_cross_repo_impact.sh — checkpoint "cross_repo_impact"
#
# Grades DRIFT_REPORT.json against ground_truth.json:scoring_evidence (sealed
# root-only), verified at argo-cd v2.9.0 and flux2 v2.2.0.
#
# What changed and why: the old check paid for the mere PRESENCE of the
# *_source_file and impact keys — keys the prompt's own answer template spells
# out — plus a >10-character impact string, so any schema-shaped payload earned
# full credit with zero repo access (key-presence credit, the exploit class of
# EnterpriseBench-jn73.2.7.3.1.4). The impact story must now be told through
# the mechanism that makes the three views diverge, which only the pinned
# trees show: ArgoCD's tracking metadata (argocd.argoproj.io/tracking-id,
# common/common.go:164, stripped by resourceTracking.Normalize before diffing)
# on one side, and Flux's kustomize.toolkit.fluxcd.io grouping or its
# post-build kustomize.SubstituteVariables step (internal/build/build.go:55,
# 394) on the other.
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
argocd_side = [t.lower() for t in evidence.get("cross_repo_impact_argocd") or []]
flux_side = [t.lower() for t in evidence.get("cross_repo_impact_flux") or []]
if not argocd_side or not flux_side:
    verdict(0.0, "VERIFIER_INFRA_ERROR: no cross_repo_impact scoring_evidence in ground_truth.json")

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

whole = norm([p for p in points if isinstance(p, dict)])

has_argocd = any(t in whole for t in argocd_side)
has_flux = any(t in whole for t in flux_side)

score = 0.5 * has_argocd + 0.5 * has_flux
verdict(score,
        "Impact grounded in divergence mechanisms: argocd tracking metadata=%s, flux build/grouping=%s"
        % (has_argocd, has_flux))
'
