#!/usr/bin/env bash
# Checkpoint: Verify agent determined the correct expected values, not just
# named the right files. Requires the concrete numeric/symbol grounding that
# only comes from reading the source (these values are not in the prompt).
set -euo pipefail

export REPORT="${WORKSPACE:-/workspace}/DRIFT_REPORT.json"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "DRIFT_REPORT.json not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2

# 1. Correct Prometheus default values: MinBlockDuration defaults to 2h
#    (DefaultBlockDuration) while MaxBlockDuration is derived from retention
#    (min(31d, retention/10) -> 36h/1.5d at the 15d default retention). Either
#    the 2h/36h(1.5d) pairing or the 31d cap / retention-over-10 formula is
#    acceptable evidence of having read cmd/prometheus/main.go.
if python3 -c "
import json, re
report = json.load(open('$REPORT'))
points = report.get('drift_points', [])
for p in points:
    blob = ' '.join(str(p.get(k, '')) for k in ('prometheus_default', 'config_key', 'override_chain')).lower()
    has_2h = bool(re.search(r'\b2h\b', blob))
    has_derived_max = any(t in blob for t in ('36h', '1.5d', '31d', 'retention/10', 'retention / 10', 'retention-derived'))
    if has_2h and has_derived_max:
        raise SystemExit(0)
raise SystemExit(1)
" 2>/dev/null; then
  FOUND=$((FOUND + 1))
fi

# 2. Correct Thanos expected behavior: the sidecar treats min-block-duration
#    != max-block-duration as compaction being enabled and refuses to upload
#    (or only tolerates it with --shipper.ignore-unequal-block-size), and
#    separately warns when block time isn't 2h.
if python3 -c "
import json
report = json.load(open('$REPORT'))
points = report.get('drift_points', [])
for p in points:
    blob = ' '.join(str(p.get(k, '')) for k in ('thanos_expectation', 'failure_mode', 'override_chain')).lower()
    if ('compaction needs to be disabled' in blob or
        ('compaction' in blob and ('disable' in blob or 'enabled' in blob)) or
        'ignore-unequal-block-size' in blob or 'ignoreblocksize' in blob):
        raise SystemExit(0)
raise SystemExit(1)
" 2>/dev/null; then
  FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 1 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Correct expected values for %d/%d aspects (Prometheus default split, Thanos compaction gate)"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
