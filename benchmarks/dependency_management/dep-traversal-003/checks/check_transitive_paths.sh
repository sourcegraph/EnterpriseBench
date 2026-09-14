#!/usr/bin/env bash
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/BLAST_RADIUS.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "BLAST_RADIUS.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2
# Repo-specific evidence only — generic CVE/task language (Accept-Language,
# "only imports", "language package") is given in the prompt and must not earn
# credit on its own. Require the actual call sites found by reading hugo's
# source: langs/language.go and langs/i18n/translationProvider.go are where
# golang.org/x/text/language is genuinely imported and used in this workspace;
# common/text/transform.go is the real "imports x/text but not the vulnerable
# sub-package" red herring.
if grep -qiE 'langs/language\.go|langs/i18n/translationProvider\.go|common/text/transform\.go' "$REPORT"; then FOUND=$((FOUND + 1)); fi
# Agent must cite the actual function(s) called in that source, not just the
# sub-package name — language.Parse / language.Make are the real call sites;
# ParseAcceptLanguage itself is never called from this code.
if grep -qiE 'language\.Parse\(|language\.Make\(|language\.Und\b' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Function-level analysis: %d/%d criteria met"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
