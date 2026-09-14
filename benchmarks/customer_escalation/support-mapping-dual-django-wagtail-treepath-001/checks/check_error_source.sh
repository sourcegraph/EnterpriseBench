#!/usr/bin/env bash
# check_error_source.sh -- file discovery: agent identified correct source files across both repos
set -euo pipefail

export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"
export GT_FILE="$TASK_DIR/ground_truth.json"

# bash+jq+grep reimplementation of `python3 -m eb_verify.plugins.file_extraction
# --keys source_files,files,error_source.files --policy suffix` (no python3 in
# container). Byte-identical scoring: agent file list from the first present of
# source_files / files / error_source.files (dotted lookup; dict item -> path||file);
# a GT required path counts when an agent path equals it or ends with "/"+path
# (suffix policy); score = round(found/total, 2), passes at >= 0.5.

if [[ -z "${ANSWER_FILE:-}" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "ANSWER_FILE env var not set"}' >&2
    exit 1
fi
if [[ -z "${GT_FILE:-}" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "GT_FILE env var not set"}' >&2
    exit 1
fi

if [[ ! -f "$ANSWER_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 0
fi

if [[ ! -f "$GT_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No ground_truth.json found"}' >&2
    exit 1
fi

# Parse both files; any parse/read error -> generic "Parse error", exit 0.
if ! answer_json=$(jq -c '.' "$ANSWER_FILE" 2>/dev/null) \
   || ! gt_json=$(jq -c '.' "$GT_FILE" 2>/dev/null); then
    echo '{"score": 0.0, "passed": false, "detail": "Parse error"}'
    exit 0
fi

# GT required paths: accept [{path:..}] or ["a",..]; non-dict gt or non-list -> none.
mapfile -t gt_files < <(printf '%s' "$gt_json" | jq -r '
    if type == "object" then
      (.required_files // []) as $r
      | if ($r | type) == "array" then
          $r[]
          | if type == "object" then (.path // "" | select(type == "string" and . != ""))
            elif type == "string" then select(. != "")
            else empty end
        else empty end
    else empty end')

total=${#gt_files[@]}
if [[ "$total" -eq 0 ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No GT files"}'
    exit 0
fi

# Agent files: first present (non-null) of source_files / files / error_source.files via
# dotted resolution; must be a list; dict item -> (.path or .file or "") truthy-or; str kept.
mapfile -t agent_files < <(printf '%s' "$answer_json" | jq -r '
    def resolve($k): reduce ($k | split("."))[] as $s (.;
        if type == "object" then .[$s] else null end);
    if type != "object" then empty
    else
      ( resolve("source_files") ) as $a
      | ( if $a != null then $a
          else (resolve("files")) as $b
            | if $b != null then $b
              else (resolve("error_source.files")) end
          end ) as $raw
      | if ($raw | type) == "array" then
          $raw[]
          | if type == "object" then
              ( (.path // "") as $p
                | if ($p | type) == "string" and $p != "" then $p
                  else (.file // "") end )
              | select(type == "string" and . != "")
            elif type == "string" then .
            else empty end
        else empty end
    end')

found=0
for gt in "${gt_files[@]}"; do
    hit=0
    for af in "${agent_files[@]}"; do
        # suffix policy: exact match OR endswith("/" + gt)
        if [[ "$af" == "$gt" ]] || [[ "$af" == *"/$gt" ]]; then
            hit=1
            break
        fi
    done
    found=$((found + hit))
done

# score = round(found/total, 2) (IEEE ties-to-even, python repr); passes at >= 0.5.
read -r score passed < <(jq -nr --argjson f "$found" --argjson t "$total" '
    ($f / $t) as $x
    | ($x * 100) as $y
    | ($y | floor) as $fl
    | ($y - $fl) as $d
    | ( if $d < 0.5 then $fl
        elif $d > 0.5 then $fl + 1
        else (if ($fl % 2) == 0 then $fl else $fl + 1 end) end ) as $r2
    | ($r2 / 100) as $sc
    | "\($sc | tojson) \(if $sc >= 0.5 then "true" else "false" end)"')
[[ "$score" == *.* ]] || score="${score}.0"
printf '{"score": %s, "passed": %s, "detail": "Found %s/%s required files"}\n' \
    "$score" "$passed" "$found" "$total"
