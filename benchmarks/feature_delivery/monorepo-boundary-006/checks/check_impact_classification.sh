#!/usr/bin/env bash
# Checkpoint 2: Verify impact classification
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/pnpm/IMPACT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "IMPACT_REPORT.md not found"}\n'
  exit 0
fi

CONTENT=$(cat "$REPORT" | tr '[:upper:]' '[:lower:]')

# "major" alone is a generic semver term available to any closed-book guess.
# Require it to be tied to a repo-specific package/artifact the agent can only
# know by reading the code: the source-of-change package (@pnpm/config /
# getOptionsFromRootManifest) and a downstream consumer package (the pnpm CLI
# or plugin-commands-rebuild/plugin-commands-installation) both classified major.
FOUND=0
TOTAL=2
if echo "$CONTENT" | grep -q 'major' && echo "$CONTENT" | grep -qiE '@pnpm/config|getoptionsfromrootmanifest'; then
  FOUND=$((FOUND + 1))
fi
if echo "$CONTENT" | grep -q 'major' && echo "$CONTENT" | grep -qiE 'plugin-commands-rebuild|plugin-commands-installation|pnpm (cli|CLI)|pnpm/test'; then
  FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 1 ]; then PASSED=true; else PASSED=false; fi
printf '{"score": %s, "passed": %s, "reason": "Classified %d/%d packages as major with repo-grounded citation"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
