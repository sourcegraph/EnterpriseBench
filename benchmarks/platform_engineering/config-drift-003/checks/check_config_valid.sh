#!/usr/bin/env bash
# Checkpoint 3: Validate corrected configuration if provided
set -euo pipefail

export WORKSPACE="${WORKSPACE:-/workspace}"
export CHART_DIR="$WORKSPACE/charts/bitnami/redis"
export REPORT="$WORKSPACE/charts/DRIFT_REPORT.json"

if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "DRIFT_REPORT.json not found"}\n'
  exit 0
fi

# Reject non-JSON / empty reports outright — a garbage report must not be able
# to reach the "corrected config not provided" skip path below.
if ! python3 -c "
import json, os
json.load(open(os.environ['REPORT']))
" 2>/dev/null; then
  printf '{"score": 0.0, "passed": false, "reason": "DRIFT_REPORT.json is not valid JSON"}\n'
  exit 0
fi

# Check if corrected _helpers.tpl was provided
export HELPERS="$CHART_DIR/templates/_helpers.tpl"
if [[ -f "$HELPERS" ]]; then
  # Verify the fix stores generated password for reuse
  if python3 -c "
import os
helpers = open(os.environ['HELPERS']).read()
# The fix should store the password in .Values.global.redis.password for reuse
if '.Values' in helpers and 'global' in helpers and ('set' in helpers or 'store' in helpers):
    if 'redis.password' in helpers or 'redis-password' in helpers:
        exit(0)
exit(1)
" 2>/dev/null; then
    printf '{"score": 1.0, "passed": true, "reason": "Corrected helper stores password for reuse"}\n'
    exit 0
  elif command -v helm &>/dev/null; then
    if helm template test-redis "$CHART_DIR" --set serviceBindings.enabled=true --dry-run 2>/dev/null | grep -q 'kind:'; then
      printf '{"score": 0.75, "passed": true, "reason": "Corrected chart templates successfully"}\n'
      exit 0
    else
      printf '{"score": 0.25, "passed": false, "reason": "Corrected chart fails helm template"}\n'
      exit 0
    fi
  fi
  # HELPERS present but neither content nor helm-based check passed; fall
  # through to report-based grading below rather than auto-passing.
fi

# No (verifiable) corrected chart file was provided — grade the analysis in
# DRIFT_REPORT.json itself. A garbage/empty report must score 0 here; it must
# cite the specific override chain and the specific caching fix, not just
# repeat prompt vocabulary.
export FOUND=0
export TOTAL=3

# Check 1: at least one drift point's override_chain names the helper file
# AND at least one specific downstream template that consumes it.
if python3 -c "
import json, os
report = json.load(open(os.environ['REPORT']))
points = report.get('drift_points', [])
ok = False
for p in points:
    chain = p.get('override_chain', '')
    text = json.dumps(chain).lower()
    if '_helpers.tpl' in text and any(f in text for f in ('secret.yaml', 'health-configmap.yaml', 'application.yaml')):
        ok = True
        break
if ok:
    exit(0)
exit(1)
" 2>/dev/null; then
  FOUND=$((FOUND + 1))
fi

# Check 2: the fix description cites the specific cache location
# (.Values.global.redis.password) together with a caching/first-call verb —
# this exact symbol is not present anywhere in the prompt.
if python3 -c "
import json, os
report = json.load(open(os.environ['REPORT']))
text = json.dumps(report).lower()
if 'global.redis.password' in text and any(k in text for k in ('cache', 'first call', 'first invocation', 'set .values', 'store')):
    exit(0)
exit(1)
" 2>/dev/null; then
  FOUND=$((FOUND + 1))
fi

# Check 3: a concrete validation step naming the chart's actual env var
# (REDIS_PASSWORD) alongside the helm template command.
if python3 -c "
import json, os
report = json.load(open(os.environ['REPORT']))
text = json.dumps(report).lower()
if 'helm template' in text and 'redis_password' in text:
    exit(0)
exit(1)
" 2>/dev/null; then
  FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Corrected-config analysis: %d/%d checks passed"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
