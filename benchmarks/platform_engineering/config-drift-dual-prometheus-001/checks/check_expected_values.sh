#!/usr/bin/env bash
# check_expected_values.sh — verify each drift point documents the Prometheus default, its
# source file, the Thanos expectation/source file, and the failure mode (instruction shape).
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
has_default = any(s(p,'prometheus_default') for p in points)
has_pfile   = any(s(p,'prometheus_source_file') for p in points)
has_thanos  = any(s(p,'thanos_expectation') or s(p,'thanos_source_file') for p in points)
has_fail    = any(len(s(p,'failure_mode')) > 10 for p in points)
file_score = (0.5 if has_pfile else 0.0) + (0.5 if has_thanos else 0.0)
score = round(file_score * 0.5 + (0.25 if has_default else 0.0) + (0.25 if has_fail else 0.0), 2)
passed = bool(has_pfile and has_thanos and (has_default or has_fail))
detail = f'prometheus_default={has_default}, prometheus_file={has_pfile}, thanos={has_thanos}, failure_mode={has_fail}'
print(json.dumps({'score': score, 'passed': passed, 'detail': detail}))
"
