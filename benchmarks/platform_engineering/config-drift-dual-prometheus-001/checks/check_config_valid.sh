#!/usr/bin/env bash
# Checkpoint: Agent produces a structurally valid, repo-grounded DRIFT_REPORT.json
set -euo pipefail

export REPORT="${WORKSPACE:-/workspace}/DRIFT_REPORT.json"

if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "DRIFT_REPORT.json not found"}\n'
  exit 0
fi

# Structural validity: must be JSON with a non-empty drift_points array, and
# at least one entry must carry a real Go source file on BOTH sides (not a
# placeholder like "N/A" or an empty string) plus a multi-hop override_chain.
# A generic or garbage report (no real file citations) scores 0 here.
SCORE=$(python3 -c "
import json

try:
    report = json.load(open('$REPORT'))
except Exception:
    print(0.0)
    raise SystemExit

points = report.get('drift_points', [])
if not isinstance(points, list) or not points:
    print(0.0)
    raise SystemExit

valid = 0
for p in points:
    if not isinstance(p, dict):
        continue
    prom_file = str(p.get('prometheus_source_file', ''))
    thanos_file = str(p.get('thanos_source_file', ''))
    chain = p.get('override_chain', [])
    prom_ok = prom_file.endswith('.go') and ('/' in prom_file or prom_file.startswith('tsdb') or prom_file.startswith('cmd'))
    thanos_ok = thanos_file.endswith('.go') and ('/' in thanos_file or thanos_file.startswith('cmd') or thanos_file.startswith('pkg'))
    chain_ok = isinstance(chain, list) and len(chain) >= 2
    if prom_ok and thanos_ok and chain_ok:
        valid += 1

print(1.0 if valid >= 1 else 0.0)
" 2>/dev/null || echo "0.0")

if awk "BEGIN {exit !($SCORE >= 1.0)}"; then
  printf '{"score": 1.0, "passed": true, "reason": "Report has at least one drift point with real Go source citations on both sides and a multi-hop override chain"}\n'
else
  printf '{"score": 0.0, "passed": false, "reason": "Report missing a valid drift_point with real prometheus_source_file/thanos_source_file (.go paths) and override_chain"}\n'
fi
