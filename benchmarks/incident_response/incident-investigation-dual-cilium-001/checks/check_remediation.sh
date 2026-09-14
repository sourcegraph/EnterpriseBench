#!/usr/bin/env bash
set -euo pipefail
REPORT="${WORKSPACE:-/workspace}/INCIDENT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "INCIDENT_REPORT.md not found"}\n'; exit 0
fi
FOUND=0; TOTAL=2
# Fix type is generic vocabulary (fine — the prompt never names a fix), but
# the location must cite a specific repo-side file, not just "in the loader"
# or "in ebpf" which would match almost any paragraph mentioning either repo.
if grep -qiE 'serial.*regenerat|mutex|deep.copy|deep copy|clone.*reader|fresh.*reader|per.call.*reader|per-call|defensive.copy|defensive copy|copy.*MapSpec|copy.*spec' "$REPORT"; then FOUND=$((FOUND+1)); fi
if grep -qiE 'pkg/bpf/collection\.go|pkg/datapath/loader|upstream.*cilium/ebpf|cilium/ebpf.*(fix|patch)|map\.go|collection\.go|elf_reader\.go' "$REPORT"; then FOUND=$((FOUND+1)); fi
SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 1 ]; then PASSED=true; else PASSED=false; fi
printf '{"score": %s, "passed": %s, "reason": "Remediation quality: %d/%d key elements"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
