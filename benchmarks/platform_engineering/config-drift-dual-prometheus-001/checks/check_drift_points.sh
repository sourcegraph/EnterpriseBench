#!/usr/bin/env bash
# check_drift_points.sh — verify agent identified drift points between Prometheus TSDB/storage
# defaults and the Thanos sidecar's expectations.
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
has_prom = any(any(t in blob(p) for t in ['prometheus','prometheus_default','prometheus_source_file','tsdb','tsdb/db.go','cmd/prometheus']) for p in points)
has_thanos = any(any(t in blob(p) for t in ['thanos','thanos_expectation','thanos_source_file','sidecar','shipper']) for p in points)
concepts = ['block','min-block','max-block','retention','wal','external label','duration','storage','upload']
matched = sum(1 for c in concepts if any(c in blob(p) for p in points))
repos_score = (0.5 if has_prom else 0.0) + (0.5 if has_thanos else 0.0)
concept_score = min(1.0, matched / 3.0)
score = round(repos_score * 0.6 + concept_score * 0.4, 2)
passed = bool(has_prom and has_thanos and matched >= 2)
detail = f'prometheus_ref={has_prom}, thanos_ref={has_thanos}, concepts={matched}/{len(concepts)}, points={len(points)}'
print(json.dumps({'score': score, 'passed': passed, 'detail': detail}))
"
