#!/usr/bin/env bash
# check_ownership.sh — verify agent identified correct code owners or subsystem
# Reads agent output from $WORKSPACE/agent_output/answer.json
# Checks for implementation-specific ownership terms from the Envoy codebase
# bash+jq+grep reimplementation (no python3 in container). Output JSON is
# byte-identical to the previous python implementation.
set -euo pipefail

export ANSWER_FILE="${WORKSPACE:-/workspace}/agent_output/answer.json"
export GT_FILE="${TASK_DIR:-/task}/ground_truth.json"

if [[ ! -f "$ANSWER_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 1
fi

if [[ ! -f "$GT_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No ground_truth.json found"}'
    exit 1
fi

n_kw=$(jq '(.ownership_keywords // []) | length' "$GT_FILE")
if [[ "$n_kw" -eq 0 ]]; then
    jq -cn '{score: 0.0, passed: false, detail: "No ownership keywords in ground truth"}'
    exit 0
fi

# ownership = answer.get('ownership', answer.get('owners', answer.get('subsystem','')))
# dict -> ' '.join(str(v) for values); list -> ' '.join(str(v)); else -> str(ownership)
ownership_text=$(jq -r '
  def q: "\u0027";
  def pyrepr_inner:
    if type=="string" then q + . + q
    elif type=="object" then "{" + ([to_entries[] | q + .key + q + ": " + (.value|pyrepr_inner)] | join(", ")) + "}"
    elif type=="array" then "[" + ([.[]|pyrepr_inner] | join(", ")) + "]"
    elif type=="boolean" then (if . then "True" else "False" end)
    elif type=="null" then "None"
    else tostring end;
  def pystr:
    if type=="string" then .
    elif type=="object" then "{" + ([to_entries[] | q + .key + q + ": " + (.value|pyrepr_inner)] | join(", ")) + "}"
    elif type=="array" then "[" + ([.[]|pyrepr_inner] | join(", ")) + "]"
    elif type=="boolean" then (if . then "True" else "False" end)
    elif type=="null" then "None"
    else tostring end;
  (if has("ownership") then .ownership elif has("owners") then .owners elif has("subsystem") then .subsystem else "" end) as $o
  | (if ($o|type)=="object" then [$o[] | pystr] | join(" ")
     elif ($o|type)=="array" then [$o[] | pystr] | join(" ")
     else ($o|pystr) end) | ascii_downcase
' "$ANSWER_FILE")

# full_answer_text = json.dumps(answer).lower(); search_text = ownership_text + " " + full
full_answer_text=$(jq -c . "$ANSWER_FILE" | sed 's/,/, /g; s/:/: /g' | tr '[:upper:]' '[:lower:]')
search_text="${ownership_text} ${full_answer_text}"

if [[ -z "${ownership_text//[[:space:]]/}" ]]; then
    jq -cn '{score: 0.0, passed: false, detail: "Agent provided no ownership info"}'
    exit 0
fi

# keyword_present: split the gold compound on '-' and count it matched when ANY token
# appears in the (already-lowercased) search text. The old check required the whole
# de-hyphenated keyword ("alert_scheduling") contiguous in the normalized prose, which
# prose that says "alert scheduling" never satisfies.
matched=0
matched_kw=()
while IFS= read -r kw; do
  kw_lc=$(printf '%s' "$kw" | tr '[:upper:]' '[:lower:]')
  while IFS= read -r tok; do
    [[ -z "$tok" ]] && continue
    if grep -qF -- "$tok" <<<"$search_text"; then
      matched=$((matched + 1)); matched_kw+=("$kw"); break
    fi
  done < <(printf '%s\n' "$kw_lc" | tr '-' '\n')
done < <(jq -r '.ownership_keywords[]' "$GT_FILE")

# score = round(min(1.0, matched/4.0), 2)
case "$matched" in
  0) score="0.0";;
  1) score="0.25";;
  2) score="0.5";;
  3) score="0.75";;
  *) score="1.0";;
esac
if [[ "$matched" -ge 2 ]]; then passed=true; else passed=false; fi

# matched_kw python list repr
if [[ ${#matched_kw[@]} -eq 0 ]]; then
  matched_repr="[]"
else
  matched_repr=$(printf "%s\n" "${matched_kw[@]}" | jq -R . | jq -s -r 'def q: "\u0027"; "[" + (map(q + . + q) | join(", ")) + "]"')
fi

detail="Matched ${matched}/${n_kw} implementation-specific ownership keywords: ${matched_repr}"
jq -cn --argjson s "$score" --argjson p "$passed" --arg d "$detail" '{score: $s, passed: $p, detail: $d}'
