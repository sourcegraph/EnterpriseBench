#!/usr/bin/env bash
# check_drift_points.sh — verify agent identified drift points between the ArgoCD
# application-controller defaults and the argo-helm chart values.yaml.
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
def blob(p): return json.dumps(p).lower()
has_argocd = any(any(t in blob(p) for t in ['argo-cd','argocd_default','argocd_source_file','appcontroller','argocd_application_controller','util/settings','settings.go']) for p in points)
has_helm = any(any(t in blob(p) for t in ['argo-helm','values.yaml','helm_override','helm_source_path','helm']) for p in points)
concepts = ['timeout','retry','resync','processor','resource','tracking','health','sync','self.heal','self heal','reconcil']
matched = sum(1 for c in concepts if any(c in blob(p) for p in points))
repos_score = (0.5 if has_argocd else 0.0) + (0.5 if has_helm else 0.0)
concept_score = min(1.0, matched / 3.0)
score = round(repos_score * 0.6 + concept_score * 0.4, 2)
passed = bool(has_argocd and has_helm and matched >= 2)
detail = f'argocd_ref={has_argocd}, helm_ref={has_helm}, concepts={matched}/{len(concepts)}, points={len(points)}'
print(json.dumps({'score': score, 'passed': passed, 'detail': detail}))
"
