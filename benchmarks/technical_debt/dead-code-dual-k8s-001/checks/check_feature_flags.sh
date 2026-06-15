#!/usr/bin/env bash
# check_feature_flags.sh — Verify feature-flag / lifecycle identification.
# Env: WORKSPACE, TASK_DIR, TASK_ID
set -euo pipefail

REPORT="${WORKSPACE}/dead_code_report.json"
GT_DIR="${TASK_DIR}/ground_truth.json"

if [[ ! -f "$REPORT" ]]; then
    echo '{"score": 0.0, "detail": "No dead_code_report.json found"}'
    exit 0
fi

# Gate: flag/lifecycle credit requires at least one correctly-identified dead-code
# (file,symbol) pair, so a filler report cannot bank credit on fabricated claims.
NORM='map(if ((has("file") or has("path")) and has("symbol")) then ([(.file // .path), .symbol] | @json) else empty end) | unique | .[]'
claimed=$(jq -r "$NORM" "$REPORT" 2>/dev/null || true)
dead=$(jq -r '(.dead_code // []) | '"$NORM" "$GT_DIR" 2>/dev/null || true)
export LC_ALL=C
tp_count=$(comm -12 <(sort <<<"$claimed") <(sort <<<"$dead") | grep -c . || true)
if [[ "$tp_count" -eq 0 ]]; then
    echo '{"score": 0.0, "detail": "No correctly-identified dead-code pair — feature-flag/lifecycle credit requires >=1 true positive"}'
    exit 0
fi

# Combined per-item haystack "evidence symbol file", lowercased (.get default "").
mapfile -t combined < <(jq -r '
  (if type=="array" then . else [] end) | .[]
  | ( (.evidence // "") + " " + (.symbol // "") + " " + (.file // "") )
      | ascii_downcase' "$REPORT" 2>/dev/null || true)

gt_flag_names=$(jq -r '(.feature_flags // []) | map(.flag | ascii_downcase) | unique | .[]' "$GT_DIR")
gt_count=$(printf '%s' "$gt_flag_names" | grep -c . || true)

if [[ "$gt_count" -eq 0 ]]; then
    # No named feature flags in ground truth: the dead code is lifecycle/deprecation
    # driven, not flag-gated. Pass only when the report actually addresses it — its
    # evidence/symbol/file text must hit a lifecycle keyword. A filler or empty report
    # no longer earns free credit (was the y50 default-pass-to-1.0 garbage-credit bug).
    for hay in "${combined[@]}"; do
        if printf '%s' "$hay" | grep -qiE 'deprecated|removed|no longer|lifecycle'; then
            echo '{"score": 1.0, "detail": "No named feature flags; dead code is lifecycle/deprecation-driven (acknowledged in evidence)"}'
            exit 0
        fi
    done
    echo '{"score": 0.0, "detail": "No named feature flags in ground truth and report does not address the deprecation/lifecycle driver"}'
    exit 0
fi

# A flag is found if it is a literal substring of ANY item haystack.
found=""
while IFS= read -r flag; do
    [[ -z "$flag" ]] && continue
    for hay in "${combined[@]}"; do
        if printf '%s' "$hay" | grep -qF -- "$flag"; then
            found+="$flag"$'\n'
            break
        fi
    done
done <<< "$gt_flag_names"

found_sorted=$(printf '%s' "$found" | sed '/^$/d' | LC_ALL=C sort)
found_count=$(printf '%s' "$found_sorted" | grep -c . || true)
joined=$(printf '%s' "$found_sorted" | paste -sd, - | sed 's/,/, /g')
[[ -z "$joined" ]] && joined="none"

score=$(awk -v f="$found_count" -v t="$gt_count" 'BEGIN{printf "%.10f", f/t}')
pyfloat() { printf '%.*f' "$2" "$1" | sed -e 's/0*$//' -e 's/\.$/.0/'; }
score_r=$(pyfloat "$score" 4)
printf '{"score": %s, "detail": "flags_found=%s/%s (%s)"}\n' "$score_r" "$found_count" "$gt_count" "$joined"
