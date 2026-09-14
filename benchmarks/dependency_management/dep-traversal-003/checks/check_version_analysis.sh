#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2
# Repo-specific evidence only — "affected/vulnerable/version" and the CVE's own
# < 0.3.8 range are already stated in the prompt and must not earn credit on
# their own. Require the actual pinned version resolved in hugo's own
# go.mod/go.sum (v0.7.0), which can only be obtained by reading the lockfile.
if grep -qiE 'v0\.7\.0' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Agent must tie that resolved version to a classification for the named repo,
# not just restate the CVE's generic fixed-version language.
if grep -qiE 'hugo.*(patch|safe|not.*vulnerable|not.*affected|unaffected|already.*fixed)|(patch|safe|not.*vulnerable|not.*affected|unaffected|already.*fixed).*hugo' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Version analysis: %d/%d criteria met"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
