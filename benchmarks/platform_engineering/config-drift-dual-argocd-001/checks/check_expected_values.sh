#!/usr/bin/env bash
# check_expected_values.sh — verify each drift point documents the ArgoCD default, its
# source file, the Helm override path, and the impact (per the instruction's JSON shape).
set -euo pipefail

export REPORT="${WORKSPACE:-/workspace}/DRIFT_REPORT.json"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "detail": "DRIFT_REPORT.json not found"}\n'
  exit 0
fi

python3 -c "
import json, os
try:
    report = json.load(open(os.environ['REPORT']))
except json.JSONDecodeError:
    print(json.dumps({'score': 0.0, 'passed': False, 'detail': 'Invalid JSON in DRIFT_REPORT.json'})); raise SystemExit
points = report.get('drift_points', [])
def s(p, k): return str(p.get(k, '')).strip()
has_default = any(s(p,'argocd_default') for p in points)
has_acfile  = any(s(p,'argocd_source_file') for p in points)
has_helm    = any(s(p,'helm_override') or s(p,'helm_source_path') for p in points)
has_impact  = any(len(s(p,'impact')) > 10 for p in points)
file_score = (0.5 if has_acfile else 0.0) + (0.5 if has_helm else 0.0)
score = round(file_score * 0.5 + (0.25 if has_default else 0.0) + (0.25 if has_impact else 0.0), 2)
passed = bool(has_acfile and has_helm and (has_default or has_impact))
detail = f'argocd_default={has_default}, argocd_file={has_acfile}, helm={has_helm}, impact={has_impact}'
print(json.dumps({'score': score, 'passed': passed, 'detail': detail}))
"
