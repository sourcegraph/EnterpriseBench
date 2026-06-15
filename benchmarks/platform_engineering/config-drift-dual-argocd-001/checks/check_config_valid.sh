#!/usr/bin/env bash
# check_config_valid.sh — verify the agent produced a structurally valid DRIFT_REPORT.json.
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
    points = report.get('drift_points', [])
    if not isinstance(points, list) or len(points) == 0:
        print(json.dumps({'score': 0.2, 'passed': False, 'detail': 'drift_points array is empty or missing'}))
    else:
        valid = sum(1 for p in points if isinstance(p, dict) and 'config_key' in p)
        if valid > 0:
            print(json.dumps({'score': 1.0, 'passed': True, 'detail': f'Valid report with {valid} drift points'}))
        else:
            print(json.dumps({'score': 0.3, 'passed': False, 'detail': 'drift_points lack config_key field'}))
except json.JSONDecodeError:
    print(json.dumps({'score': 0.0, 'passed': False, 'detail': 'Invalid JSON in DRIFT_REPORT.json'}))
"
