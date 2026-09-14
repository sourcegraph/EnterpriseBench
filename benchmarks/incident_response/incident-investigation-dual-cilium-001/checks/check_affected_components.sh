#!/usr/bin/env bash
set -euo pipefail
REPORT="${WORKSPACE:-/workspace}/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'; exit 0
fi
FOUND=0; TOTAL=3
# Repo-specific artifacts only — generic incident terms (endpoint
# regeneration, cilium/ebpf as a bare repo name) are given in the prompt and
# must not earn credit on their own.
if grep -qiE 'pkg/endpoint/bpf\.go|regenerateBPF' "$REPORT"; then FOUND=$((FOUND+1)); fi
if grep -qiE 'pkg/datapath/loader|netlink\.go|loadDatapath' "$REPORT"; then FOUND=$((FOUND+1)); fi
if grep -qiE 'map\.go|collection\.go|elf_reader\.go|MapSpec|createMap' "$REPORT"; then FOUND=$((FOUND+1)); fi
SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi
printf '{"score": %s, "passed": %s, "reason": "Identified %d/%d affected components"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
