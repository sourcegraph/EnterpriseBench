#!/usr/bin/env bash
# check_dead_code.sh — verify dead code identification
# bash+jq+grep (no python3 in container). Scoring semantics identical to the prior
# python3 implementation: normalize claimed/dead/live to (file,symbol) pairs, compute
# F-score (1.25*p*r/(0.25*p+r)), apply 0.7x penalty when precision<0.9.
set -euo pipefail

REPORT="${WORKSPACE}/agent_output/answer.json"
GT="${TASK_DIR}/ground_truth.json"

if [[ ! -f "$REPORT" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 0
fi

if [[ ! -f "$GT" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "Ground truth not found"}'
    exit 0
fi

# Normalize an array to unique sep-joined "filesymbol" pairs (python normalize()):
#   dict -> (.file//.path//"", .symbol//"") ; string -> ("", str) ; else dropped.
# Agents key the file on either "file" or the MCP "path"; accept whichever is present.
NORM='map(if type=="object" then ((.file // .path // "") + "" + (.symbol // ""))
          elif type=="string" then ("" + .)
          else empty end) | unique | .[]'

# python answer.get("dead_code", answer.get("dead_exports", [])): key-present wins even if null.
claimed=$(jq -r '(if has("dead_code") then .dead_code
                  elif has("dead_exports") then .dead_exports
                  else [] end) | '"$NORM" "$REPORT")
dead=$(jq -r '(if has("dead_code") then .dead_code else [] end) | '"$NORM" "$GT")
live=$(jq -r '(if has("live_code") then .live_code else [] end) | '"$NORM" "$GT")

export LC_ALL=C  # comm checks sort order in the active locale; pin it to match the sorts.
count_common() { comm -12 <(sort <<<"$1") <(sort <<<"$2") | grep -c . || true; }
count_only_first() { comm -23 <(sort <<<"$1") <(sort <<<"$2") | grep -c . || true; }

tp=$(count_common "$claimed" "$dead")
fp=$(count_common "$claimed" "$live")
fn=$(count_only_first "$dead" "$claimed")

read -r score precision recall < <(awk -v tp="$tp" -v fp="$fp" -v fn="$fn" 'BEGIN{
  p=(tp+fp>0)?tp/(tp+fp):0.0
  r=(tp+fn>0)?tp/(tp+fn):0.0
  if (p+r>0) f=(1.25*p*r)/(0.25*p+r); else f=0.0
  s=f; if (p<0.9) s*=0.7
  printf "%.10f %.10f %.10f\n", s, p, r
}')

# pyfloat: render like python repr(round(x,N)) — fixed-N decimals, strip trailing zeros
# (keeping one), so 1.0/0.0/0.625/0.4375 match json.dumps exactly.
pyfloat() { printf '%.*f' "$2" "$1" | sed -e 's/0*$//' -e 's/\.$/.0/'; }
score_r=$(pyfloat "$score" 4)
passed=$(awk -v s="$score" 'BEGIN{print (s>=0.3)?"true":"false"}')
detail=$(printf 'precision=%.2f recall=%.2f TP=%s FP=%s FN=%s' "$precision" "$recall" "$tp" "$fp" "$fn")
printf '{"score": %s, "passed": %s, "detail": "%s"}\n' "$score_r" "$passed" "$detail"
