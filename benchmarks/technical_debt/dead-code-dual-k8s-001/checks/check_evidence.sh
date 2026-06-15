#!/usr/bin/env bash
# check_evidence.sh — Validate that each dead code claim has evidence.
# Checks: (a) every entry has non-empty evidence, (b) evidence mentions callers/references.
# Env: WORKSPACE, TASK_DIR, TASK_ID
set -euo pipefail

REPORT="${WORKSPACE}/dead_code_report.json"
GT_DIR="${TASK_DIR}/ground_truth.json"

if [[ ! -f "$REPORT" ]]; then
    echo '{"score": 0.0, "detail": "No dead_code_report.json found"}'
    exit 0
fi

# bash+jq+grep (no python3 in container). Semantics identical to the prior python3:
# claimed = whole report (list); falsy → "Empty report"; per item, evidence =
# item.get("evidence","").strip(); count non-empty (has_evidence) and those whose
# lowercased text contains any QUALITY_KEYWORD as a literal substring (quality_evidence);
# score = 0.6*has/total + 0.4*quality/total, rounded to 4dp.

# python `if not claimed` (falsy: empty list/object/string, null, 0, false).
falsy=$(jq -r 'if (.==null or .==false or .==0 or .=="" or .==[] or .=={}) then "yes" else "no" end' "$REPORT")
if [[ "$falsy" == "yes" ]]; then
    echo '{"score": 0.0, "detail": "Empty report"}'
    exit 0
fi

# Gate: evidence credit requires at least one correctly-identified dead-code
# (file,symbol) pair. Evidence about wrong/fabricated dead code earns nothing —
# this stops a filler report banking the evidence floor on garbage claims.
NORM='map(if ((has("file") or has("path")) and has("symbol")) then ([(.file // .path), .symbol] | @json) else empty end) | unique | .[]'
claimed=$(jq -r "$NORM" "$REPORT" 2>/dev/null || true)
dead=$(jq -r '(.dead_code // []) | '"$NORM" "$GT_DIR" 2>/dev/null || true)
export LC_ALL=C
tp_count=$(comm -12 <(sort <<<"$claimed") <(sort <<<"$dead") | grep -c . || true)
if [[ "$tp_count" -eq 0 ]]; then
    echo '{"score": 0.0, "detail": "No correctly-identified dead-code pair — evidence credit requires >=1 true positive"}'
    exit 0
fi

QUALITY_KEYWORDS=(
    "never called" "no callers" "zero callers" "no references"
    "zero references" "unused" "not imported" "no importers"
    "dead" "unreachable" "removed" "deprecated" "permanently"
    "always" "never" "no longer"
)

total=$(jq -r 'length' "$REPORT")

# Emit one line per item: the evidence value via .get("evidence","").
# A present-but-non-string evidence (incl. null) would crash python (.strip on non-str);
# jq @text on a non-string string/number is fine, but null→"null"? Guard to mirror python:
# absent key → "" ; present string → its value ; present non-string → error (python crash).
mapfile -t evid < <(jq -r '
  .[] | if has("evidence")
        then (.evidence | if type=="string" then . else error("AttributeError") end)
        else "" end' "$REPORT")

has_evidence_count=0
quality_evidence_count=0
for ev in "${evid[@]}"; do
    # python str.strip(): trim leading/trailing ASCII whitespace
    stripped=$(printf '%s' "$ev" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
    if [[ -n "$stripped" ]]; then
        has_evidence_count=$((has_evidence_count + 1))
        lower=$(printf '%s' "$stripped" | tr '[:upper:]' '[:lower:]')
        for kw in "${QUALITY_KEYWORDS[@]}"; do
            if printf '%s' "$lower" | grep -qF -- "$kw"; then
                quality_evidence_count=$((quality_evidence_count + 1))
                break
            fi
        done
    fi
done

score=$(awk -v h="$has_evidence_count" -v q="$quality_evidence_count" -v t="$total" \
  'BEGIN{printf "%.10f", 0.6*(h/t) + 0.4*(q/t)}')
pyfloat() { printf '%.*f' "$2" "$1" | sed -e 's/0*$//' -e 's/\.$/.0/'; }
score_r=$(pyfloat "$score" 4)
printf '{"score": %s, "detail": "entries=%s has_evidence=%s quality_evidence=%s"}\n' \
  "$score_r" "$total" "$has_evidence_count" "$quality_evidence_count"
