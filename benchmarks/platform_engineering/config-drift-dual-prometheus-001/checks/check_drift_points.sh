#!/usr/bin/env bash
# Checkpoint: Verify agent identified the actual cross-repo drift points.
#
# The real drift (verified against prometheus v2.48.0 / thanos v0.32.5 source):
#   - Prometheus's tsdb/db.go DefaultOptions() sets MinBlockDuration ==
#     MaxBlockDuration == DefaultBlockDuration (2h) as the TSDB library
#     default, but cmd/prometheus/main.go's flag parsing only pins
#     MinBlockDuration to that "2h" default — MaxBlockDuration is instead
#     recomputed from retention (min(31d, retention/10), 1.5d/36h at the
#     15d default retention) whenever the flag is left at 0. So the shipped
#     CLI default silently uncouples min/max block duration from the TSDB
#     library default.
#   - Thanos's cmd/thanos/sidecar.go validatePrometheus() requires
#     TSDBMinTime == TSDBMaxTime (i.e. compaction disabled) before it will
#     upload blocks, and errors out ("Compaction needs to be disabled") when
#     they diverge unless --shipper.ignore-unequal-block-size is set.
#
# "min-block-duration" / "max-block-duration" / "retention" are already named
# in the prompt, so matching those bare terms is not evidence of repo reading.
# Each check below requires a genuine source-level artifact from ONE side
# PLUS the matching artifact from the OTHER side in the same drift point.
set -euo pipefail

export REPORT="${WORKSPACE:-/workspace}/DRIFT_REPORT.json"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "DRIFT_REPORT.json not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=2

# 1. Prometheus-side artifact: the block-duration default split between the
#    TSDB library default (DefaultBlockDuration / DefaultOptions in tsdb/db.go)
#    and the CLI's retention-derived MaxBlockDuration in cmd/prometheus/main.go.
if python3 -c "
import json
report = json.load(open('$REPORT'))
points = report.get('drift_points', [])
for p in points:
    blob = ' '.join(str(p.get(k, '')) for k in ('config_key', 'prometheus_default', 'prometheus_source_file', 'override_chain')).lower()
    if ('defaultblockduration' in blob or 'defaultoptions' in blob or 'tsdb/db.go' in blob) and \
       ('main.go' in blob or 'retention' in blob):
        raise SystemExit(0)
raise SystemExit(1)
" 2>/dev/null; then
  FOUND=$((FOUND + 1))
fi

# 2. Thanos-side artifact: the sidecar's compaction-disabled validation gate
#    (validatePrometheus / TSDBMinTime vs TSDBMaxTime) in cmd/thanos/sidecar.go.
if python3 -c "
import json
report = json.load(open('$REPORT'))
points = report.get('drift_points', [])
for p in points:
    blob = ' '.join(str(p.get(k, '')) for k in ('thanos_expectation', 'thanos_source_file', 'failure_mode', 'override_chain')).lower()
    if ('validateprometheus' in blob or 'tsdbmintime' in blob or 'tsdbmaxtime' in blob or 'sidecar.go' in blob) and \
       ('compact' in blob or 'block size' in blob or 'blocksize' in blob):
        raise SystemExit(0)
raise SystemExit(1)
" 2>/dev/null; then
  FOUND=$((FOUND + 1))
fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 2 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d drift-point artifacts (Prometheus block-duration default split, Thanos compaction-disabled gate)"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
