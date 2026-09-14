#!/usr/bin/env bash
set -euo pipefail
REPORT="${WORKSPACE:-/workspace}/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'; exit 0
fi
FOUND=0; TOTAL=4
# Repo-specific artifacts only — generic incident terms (pod churn, endpoint
# regeneration, cilium/ebpf as a bare repo name) are given in the prompt and
# must not earn credit on their own.
if grep -qiE 'regenerateBPF|pkg/endpoint/bpf\.go' "$REPORT"; then FOUND=$((FOUND+1)); fi
if grep -qiE 'loadDatapath|pkg/datapath/loader|netlink\.go' "$REPORT"; then FOUND=$((FOUND+1)); fi
if grep -qiE 'LoadCollectionSpec|pkg/bpf/collection|bpf/collection\.go' "$REPORT"; then FOUND=$((FOUND+1)); fi
if grep -qiE 'MapSpec|createMap|elf_reader\.go|ebpf.*map\.go|ebpf.*collection\.go' "$REPORT"; then FOUND=$((FOUND+1)); fi
SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 3 ]; then PASSED=true; else PASSED=false; fi
printf '{"score": %s, "passed": %s, "reason": "Traced %d/%d error chain steps"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
