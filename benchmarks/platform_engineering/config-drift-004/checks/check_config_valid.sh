#!/usr/bin/env bash
# Checkpoint 3: Validate the corrected-config / validation-plan specificity.
#
# A generic report (or the raw prompt pasted back) must NOT pass this check.
# We require the report to be valid JSON with a real drift_points array that
# cites manifests/ha/install.yaml -- the rendered snapshot CI diffs against.
# That path is never named in instruction.md, so citing it demonstrates the
# agent actually traced the render pipeline rather than echoing the prompt.
# We additionally require a concrete fix action tied to the actual override
# file, or (if the agent went further) an actually-corrected values.yaml.
set -euo pipefail

export WORKSPACE="${WORKSPACE:-/workspace}"
export VALUES_FILE="$WORKSPACE/argo-cd/manifests/ha/base/redis-ha/chart/values.yaml"
export REPORT="$WORKSPACE/argo-cd/DRIFT_REPORT.json"

if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "DRIFT_REPORT.json not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2

# Check 1: report parses as JSON, has a non-empty drift_points array, and
# names the rendered manifest snapshot (manifests/ha/install.yaml) that CI
# actually diffs against -- an artifact not mentioned anywhere in the prompt.
if python3 -c "
import json, os
try:
    report = json.load(open(os.environ['REPORT']))
except Exception:
    exit(1)
points = report.get('drift_points', [])
if not isinstance(points, list) or not points:
    exit(1)
text = json.dumps(report).lower()
if 'install.yaml' in text:
    exit(0)
exit(1)
" 2>/dev/null; then
  FOUND=$((FOUND + 1))
fi

# Check 2: the report specifies a concrete fix action (delete/remove) tied to
# the actual override file path, OR the agent went further and provided a
# corrected values.yaml that no longer sets securityContext to null.
FIX_SPECIFIED=1
python3 -c "
import json, os
try:
    report = json.load(open(os.environ['REPORT']))
except Exception:
    exit(1)
text = json.dumps(report).lower()
if ('delete' in text or 'remove' in text) and 'manifests/ha/base/redis-ha/chart/values.yaml' in text:
    exit(0)
exit(1)
" 2>/dev/null || FIX_SPECIFIED=0

VALUES_FIXED=0
if [[ -f "$VALUES_FILE" ]]; then
  python3 -c "
import os, re
content = open(os.environ['VALUES_FILE']).read()
if re.search(r'securitycontext\s*:\s*null', content, re.IGNORECASE):
    exit(1)
exit(0)
" 2>/dev/null && VALUES_FIXED=1
fi

if [[ "$FIX_SPECIFIED" -eq 1 || "$VALUES_FIXED" -eq 1 ]]; then
  FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Validation specificity: %d/%d checks passed"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
