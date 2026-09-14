#!/usr/bin/env bash
# check_removal_impact.sh — file-discovery checkpoint: verify the agent cited
# the canonical Wagtail and Django files involved in the JSONField removal
# impact analysis (matched against ground_truth.required_files).
#
# Reimplemented in bash+jq (no python3 in container — the task image ships
# bash/grep/jq but not python3, so `python3 -m eb_verify.plugins.file_extraction`
# exited 127). Byte-identical to that plugin with --keys
# source_files,files,error_source.files --policy suffix (suffix = exact match or
# agent path endswith "/"+gt_path), pass-threshold 0.5.

set -euo pipefail

export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"
export GT_FILE="$TASK_DIR/ground_truth.json"

KEYS=(source_files files error_source.files)

# Missing answer.json is an agent failure: zero-score, exit 0.
if [[ ! -f "$ANSWER_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 0
fi

# Missing ground_truth.json is an infra failure: error JSON to stderr, exit 1.
if [[ ! -f "$GT_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No ground_truth.json found"}' >&2
    exit 1
fi

# Parse both files; malformed JSON -> generic Parse error, exit 0.
if ! jq -e . "$ANSWER_FILE" >/dev/null 2>&1 || ! jq -e . "$GT_FILE" >/dev/null 2>&1; then
    echo '{"score": 0.0, "passed": false, "detail": "Parse error"}'
    exit 0
fi

# GT files: required_files entries as {"path":...} or bare strings.
mapfile -t gt_files < <(jq -r '
  if type=="object" and (.required_files|type)=="array" then
    .required_files[]
    | if type=="object" then (.path // empty | select(type=="string" and . != ""))
      elif type=="string" and . != "" then .
      else empty end
  else empty end' "$GT_FILE")
total=${#gt_files[@]}

if [[ "$total" -eq 0 ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No GT files"}'
    exit 0
fi

# Agent files: walk the dotted-key fallback chain; first key whose dotted lookup
# resolves to a non-null value wins (even if it is not a list -> empty list).
agent_files=()
if [[ "$(jq -r 'type' "$ANSWER_FILE")" == "object" ]]; then
    for key in "${KEYS[@]}"; do
        # dotted resolve; emit "null" if any segment missing/non-object
        resolved=$(jq -c --arg k "$key" '
          ($k|split(".")) as $segs
          | reduce $segs[] as $s (.;
              if type=="object" then (.[$s] // null) else null end)
        ' "$ANSWER_FILE")
        if [[ "$resolved" != "null" ]]; then
            # First non-null candidate wins; only a list yields entries.
            if [[ "$(jq -c --arg k "$key" '($k|split(".")) as $segs | reduce $segs[] as $s (.; if type=="object" then (.[$s] // null) else null end) | type' "$ANSWER_FILE")" == '"array"' ]]; then
                mapfile -t agent_files < <(jq -r --arg k "$key" '
                  ($k|split(".")) as $segs
                  | (reduce $segs[] as $s (.; if type=="object" then (.[$s] // null) else null end))
                  | .[]
                  | if type=="object" then
                      # python: p = f.get("path") or f.get("file","");
                      #         append only if isinstance(p,str) and p
                      ( ((.path // null) as $p
                         | (if ($p == null or $p == false or $p == 0 or $p == ""
                                or $p == [] or $p == {}) then (.file // "") else $p end))
                        | select(type=="string" and . != "") )
                    elif type=="string" then .
                    else empty end' "$ANSWER_FILE")
            fi
            break
        fi
    done
fi

# suffix policy: af == gt  OR  af endswith "/"+gt
found=0
for gt_f in "${gt_files[@]}"; do
    for af in "${agent_files[@]}"; do
        if [[ "$af" == "$gt_f" || "$af" == *"/$gt_f" ]]; then
            found=$((found + 1))
            break
        fi
    done
done

score=$(jq -n --argjson f "$found" --argjson t "$total" '
  (($f / $t * 100) | round) / 100
  | if . == (. | floor) then "\(.).0" else (. | tostring) end' | tr -d '"')
passed=$(jq -n --argjson f "$found" --argjson t "$total" 'if ($f / $t) >= 0.5 then true else false end')
printf '{"score": %s, "passed": %s, "detail": "Found %s/%s required files"}\n' \
    "$score" "$passed" "$found" "$total"
