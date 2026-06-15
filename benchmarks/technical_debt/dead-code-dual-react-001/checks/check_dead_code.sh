#!/usr/bin/env bash
# check_dead_code.sh — Verify dead code identification using call_graph plugin.
# Precision-weighted: FP heavily penalized.
# bash+jq+grep (no python3 in container). Scoring semantics identical to the prior
# python3 implementation: claimed = whole report (list of {file,symbol}); precision-
# weighted F0.5 = 1.25*p*r/(0.25*p+r); gate 0.7x if p<0.9 else 0.85x if r<0.6.
# Env: WORKSPACE, TASK_DIR, TASK_ID
set -euo pipefail

REPORT="${WORKSPACE}/dead_code_report.json"
GT_DIR="${TASK_DIR}/ground_truth.json"

if [[ ! -f "$REPORT" ]]; then
    echo '{"score": 0.0, "detail": "No dead_code_report.json found"}'
    exit 0
fi

if [[ ! -f "$GT_DIR" ]]; then
    echo '{"score": 0.0, "detail": "Ground truth not found"}'
    exit 0
fi

# Normalize an array to unique (file,symbol) pairs serialized as compact JSON
# (so null values and string values stay distinct, like python tuples). Python used
# Agents key the file on either "file" or the MCP "path"; accept whichever is present.
# Entries missing both a file/path and a symbol are skipped, not aborted — the earlier
# `error("KeyError")` scored a mostly-correct report 0.0 on a single malformed entry.
NORM='map(if ((has("file") or has("path")) and has("symbol")) then ([(.file // .path), .symbol] | @json)
          else empty end) | unique | .[]'

claimed=$(jq -r "$NORM" "$REPORT")
dead=$(jq -r '(.dead_code // []) | '"$NORM" "$GT_DIR")
live=$(jq -r '(.live_code // []) | '"$NORM" "$GT_DIR")

export LC_ALL=C  # comm checks sort order in the active locale; pin it to match the sorts.
count_common() { comm -12 <(sort <<<"$1") <(sort <<<"$2") | grep -c . || true; }
count_only_first() { comm -23 <(sort <<<"$1") <(sort <<<"$2") | grep -c . || true; }

tp_count=$(count_common "$claimed" "$dead")
fp_count=$(count_common "$claimed" "$live")
fn_count=$(count_only_first "$dead" "$claimed")

read -r score precision recall < <(awk -v tp="$tp_count" -v fp="$fp_count" -v fn="$fn_count" 'BEGIN{
  bs=0.25
  p=(tp+fp>0)?tp/(tp+fp):0.0
  r=(tp+fn>0)?tp/(tp+fn):0.0
  if (p+r>0) f=(1+bs)*(p*r)/(bs*p+r); else f=0.0
  if (p<0.9) s=f*0.7; else if (r<0.6) s=f*0.85; else s=f
  printf "%.10f %.10f %.10f\n", s, p, r
}')

pyfloat() { printf '%.*f' "$2" "$1" | sed -e 's/0*$//' -e 's/\.$/.0/'; }
score_r=$(pyfloat "$score" 4)
f_str=$(awk -v tp="$tp_count" -v fp="$fp_count" -v fn="$fn_count" 'BEGIN{
  bs=0.25
  p=(tp+fp>0)?tp/(tp+fp):0.0
  r=(tp+fn>0)?tp/(tp+fn):0.0
  if (p+r>0) f=(1+bs)*(p*r)/(bs*p+r); else f=0.0
  printf "%.3f", f
}')
detail=$(printf 'precision=%.3f recall=%.3f f0.5=%s TP=%s FP=%s FN=%s' \
  "$precision" "$recall" "$f_str" "$tp_count" "$fp_count" "$fn_count")
printf '{"score": %s, "detail": "%s"}\n' "$score_r" "$detail"
