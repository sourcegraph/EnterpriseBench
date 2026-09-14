#!/usr/bin/env bash
# check_findings.sh — verify agent identified at least 3 security findings
# grounded in the actual RGW auth source (not just the prompt's own
# vulnerability-category checklist).
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"

REPORT="$WORKSPACE/security_audit.md"
if [[ ! -f "$REPORT" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "security_audit.md not found"}\n'
    exit 0
fi

FOUND=0
TOTAL=4

# 1. Must cite a specific RGW source file that implements the S3 auth/signature
#    logic — the prompt only says "the RGW source tree", never these filenames.
grep -qiE 'rgw_auth_s3\.(cc|h)|rgw_auth\.(cc|h)|rgw_rest_s3\.cc' "$REPORT" && FOUND=$((FOUND + 1))

# 2. Signature downgrade finding tied to the actual weak mechanism (SigV2 /
#    HMAC-SHA1), not just the prompt's bare "V2 vs V4" phrase.
grep -qiE 'sigv2|signature[ _-]?v2|hmac[-_ ]?sha1|downgrad(e|ing).*(v2|v4|sig)|(v2|v4).*downgrad' "$REPORT" && FOUND=$((FOUND + 1))

# 3. Timing-attack / replay finding tied to the specific remediation mechanism
#    (constant-time comparison, CRYPTO_memcmp, replay cache/nonce, clock-skew
#    tolerance) rather than the prompt's generic "timing attacks"/"replay"
#    wording.
grep -qiE 'crypto_memcmp|constant[-_ ]?time|replay cache|nonce|timestamp tolerance|clock skew' "$REPORT" && FOUND=$((FOUND + 1))

# 4. Must actually tag findings with distinct severities (at least 3 of
#    critical/high/medium/low), demonstrating >=3 discrete findings rather
#    than one paragraph reusing the word "severity".
SEV_COUNT=$(grep -oiE '\b(critical|high|medium|low)\b' "$REPORT" | tr '[:upper:]' '[:lower:]' | sort -u | wc -l)
[ "$SEV_COUNT" -ge 3 ] && FOUND=$((FOUND + 1))

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 3 ]; then
    PASSED="true"
else
    PASSED="false"
fi

printf '{"score": %s, "passed": %s, "detail": "Found %d/%d repo-grounded finding criteria"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
