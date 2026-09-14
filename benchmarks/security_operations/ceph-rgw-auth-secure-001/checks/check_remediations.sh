#!/usr/bin/env bash
# check_remediations.sh — verify agent proposed remediation recommendations
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"

REPORT="$WORKSPACE/security_audit.md"
if [[ ! -f "$REPORT" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "security_audit.md not found"}\n'
    exit 0
fi

FOUND=0
TOTAL=2

# 1. A concrete remediation tied to a specific RGW source file — not just the
#    prompt's generic "remediation recommendations" phrasing on its own.
if grep -qiE 'rgw_auth_s3\.(cc|h)|rgw_auth\.(cc|h)|rgw_rest_s3\.cc' "$REPORT" && \
   grep -qiE 'remediat|recommend|mitigat|fix|patch|harden|disable|replace|tighten' "$REPORT"; then
    FOUND=$((FOUND + 1))
fi

# 2. A real risk-assessment conclusion tagged with an actual severity level —
#    not just the prompt's bare "Summary risk assessment" checklist bullet
#    (which never states a severity level itself).
if grep -qiE 'risk assessment|overall risk|overall severity' "$REPORT" && \
   grep -qiE '\b(critical|high|medium|low)\b' "$REPORT"; then
    FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
PASSED=$([ "$FOUND" -ge 1 ] && echo true || echo false)
printf '{"score": %s, "passed": %s, "detail": "Remediation quality: %d/%d criteria"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
