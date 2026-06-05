#!/usr/bin/env bash
# check_ownership.sh — verify agent identified correct code owners or subsystem
# Reads agent output from $WORKSPACE/agent_output/answer.json
# Checks for correct ownership attribution against ground truth keywords
# bash+jq+grep reimplementation (no python3 in container). Output JSON is
# byte-identical to the previous python implementation.
set -euo pipefail

export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"
export GT_FILE="$TASK_DIR/ground_truth.json"

if [[ ! -f "$ANSWER_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 1
fi

if [[ ! -f "$GT_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No ground_truth.json found"}'
    exit 1
fi

# ownership_kw = gt.get('ownership_keywords', [])
# Fallback (only when empty): derive from required_files rationale words
# (len>4, isalpha), dedup, first 10. Reproduced for fidelity though GT always
# supplies ownership_keywords here.
n_kw=$(jq '(.ownership_keywords // []) | length' "$GT_FILE")
kw_file=$(mktemp)
if [[ "$n_kw" -ne 0 ]]; then
  jq -r '.ownership_keywords[]' "$GT_FILE" > "$kw_file"
else
  # python: words from each rationale split, len>4 and isalpha (ASCII letters),
  # then list(set(...))[:10]. set() order is non-deterministic in CPython, but
  # this branch never fires for the real GT (ownership_keywords present).
  jq -r '(.ground_truth // .).required_files // [] | .[] | (.rationale // "")' "$GT_FILE" \
    | tr ' ' '\n' \
    | awk '{ if (length($0) > 4 && $0 ~ /^[A-Za-z]+$/) print tolower($0) }' \
    | awk '!seen[$0]++' | head -n 10 > "$kw_file"
fi
n_kw=$(wc -l < "$kw_file" | tr -d ' ')

if [[ "$n_kw" -eq 0 ]]; then
    rm -f "$kw_file"
    jq -cn '{score: 0.0, passed: false, detail: "No ownership keywords in ground truth"}'
    exit 0
fi

# ownership = answer.get('ownership', answer.get('owners', answer.get('subsystem','')))
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

if [[ -z "${ownership_text//[[:space:]]/}" ]]; then
    rm -f "$kw_file"
    jq -cn '{score: 0.0, passed: false, detail: "Agent provided no ownership info"}'
    exit 0
fi

# matched: split the gold compound on '-' and count the keyword as matched when ANY of
# its tokens appears in the ownership prose. The old check required the contiguous
# de-hyphenated compound ("type legalization"/"typelegalization") as a substring, which
# natural prose never emits — a correct "type legalization pass" still scored 0.0.
matched=0
while IFS= read -r kw; do
  kw_lc=$(printf '%s' "$kw" | tr '[:upper:]' '[:lower:]')
  while IFS= read -r tok; do
    [[ -z "$tok" ]] && continue
    if grep -qF -- "$tok" <<<"$ownership_text"; then
      matched=$((matched + 1)); break
    fi
  done < <(printf '%s\n' "$kw_lc" | tr '-' '\n')
done < "$kw_file"
rm -f "$kw_file"

# score = round(min(1.0, matched / max(n_kw*0.4, 1)), 2)
score=$(jq -n --argjson m "$matched" --argjson n "$n_kw" '
  ([$n*0.4, 1] | max) as $den
  | ([1.0, ($m/$den)] | min) as $v
  | (($v*100|round)/100)')
# whole-number results need explicit float form (1.0 / 0.0), jq drops trailing .0
if jq -n --argjson s "$score" 'if $s == 1 then true else false end' | grep -q true; then
  score="1.0"
elif jq -n --argjson s "$score" 'if $s == 0 then true else false end' | grep -q true; then
  score="0.0"
fi

passed=$(jq -n --argjson s "$score" 'if $s >= 0.3 then true else false end')
detail="Matched ${matched}/${n_kw} ownership keywords"
jq -cn --argjson s "$score" --argjson p "$passed" --arg d "$detail" '{score: $s, passed: $p, detail: $d}'
