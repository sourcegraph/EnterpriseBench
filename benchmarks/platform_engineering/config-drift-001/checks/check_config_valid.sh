#!/usr/bin/env bash
# Checkpoint 3: Validate corrected configuration if provided
set -euo pipefail

export WORKSPACE="${WORKSPACE:-/workspace}"
export CHART_DIR="$WORKSPACE/charts/bitnami/consul"
export REPORT="$WORKSPACE/charts/DRIFT_REPORT.json"

if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "DRIFT_REPORT.json not found"}\n'
  exit 0
fi

# The report must actually state the precise fix for the drift point: the
# serflan-udp entry in consul-headless-service.yaml should reference the
# Helm template expression .Values.containerPorts.serfLAN (not serfWAN). A
# report that is empty, garbage, or that only echoes the generic "serfLAN" /
# "serfWAN" vocabulary from the prompt without naming the actual template
# expression and file does not demonstrate the fix was found in the repo.
export FIX_OK=0
if python3 -c "
import json, os

try:
    report = json.load(open(os.environ['REPORT']))
except Exception:
    exit(1)

points = report.get('drift_points', [])
if not isinstance(points, list):
    exit(1)

for p in points:
    if not isinstance(p, dict):
        continue
    file_ = str(p.get('file', '')).lower()
    key = str(p.get('key', '')).lower()
    expected = str(p.get('expected', '')).lower().replace(' ', '')
    actual = str(p.get('actual', '')).lower().replace(' ', '')
    chain = ' '.join(str(c) for c in p.get('override_chain', []) if isinstance(c, str)).lower()

    if 'consul-headless-service.yaml' not in (file_ + ' ' + chain):
        continue
    if 'serflan' not in key and 'serflan' not in file_:
        continue
    # Expected value must name the real Helm template expression, not just
    # the bare word 'serflan'.
    if '.values.containerports.serflan' not in expected:
        continue
    # Actual (buggy) value must show it was wired to the wrong key.
    if 'serfwan' not in actual:
        continue
    exit(0)

exit(1)
" 2>/dev/null; then
  FIX_OK=1
fi

if [[ "$FIX_OK" -eq 0 ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "DRIFT_REPORT.json does not specify the corrected .Values.containerPorts.serfLAN wiring for consul-headless-service.yaml serflan-udp"}\n'
  exit 0
fi

# Check if corrected config files were provided
export CORRECTED_SERVICE="$CHART_DIR/templates/consul-headless-service.yaml"
if [[ ! -f "$CORRECTED_SERVICE" ]]; then
  # No corrected config is optional — award credit for a report that already
  # states the exact fix precisely (verified above), since providing a
  # rewritten chart file is not required by this task's artifacts.
  printf '{"score": 1.0, "passed": true, "reason": "Corrected config not provided, but report specifies the exact serfLAN fix"}\n'
  exit 0
fi

# If helm is available, try to template the corrected chart
if command -v helm &>/dev/null; then
  if helm template test-consul "$CHART_DIR" --dry-run 2>/dev/null | grep -q 'kind:'; then
    printf '{"score": 1.0, "passed": true, "reason": "Corrected chart templates successfully"}\n'
  else
    printf '{"score": 0.25, "passed": false, "reason": "Corrected chart fails helm template"}\n'
  fi
else
  # No helm available — check that the corrected file has consistent port references
  if python3 -c "
import os
service_file = os.path.join(os.environ['CHART_DIR'], 'templates', 'consul-headless-service.yaml')
content = open(service_file).read()
# Basic check: serflan-udp should reference serfLAN, serfwan-udp should reference serfWAN
lines = content.split('\n')
ok = True
for i, line in enumerate(lines):
    if 'serflan' in line.lower() and 'serfWAN' in line:
        ok = False
    if 'serfwan' in line.lower() and 'serfLAN' in line and 'serfWAN' not in line:
        ok = False
exit(0 if ok else 1)
" 2>/dev/null; then
    printf '{"score": 1.0, "passed": true, "reason": "Corrected config has consistent port references"}\n'
  else
    printf '{"score": 0.25, "passed": false, "reason": "Corrected config still has inconsistent port references"}\n'
  fi
fi
