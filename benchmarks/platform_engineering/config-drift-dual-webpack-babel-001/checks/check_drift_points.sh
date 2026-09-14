#!/usr/bin/env bash
# check_drift_points.sh — file-discovery checkpoint: verify the agent cited the
# canonical source files in webpack and babel that document the ordering drift
# (matched against ground_truth.required_files).

#
# bash+jq+grep port of `python3 -m eb_verify.plugins.file_extraction
#   --keys source_files,files,error_source.files --policy suffix` (the container
# ships no python3). Semantics preserved exactly: agent file list is taken from
# the first present key in the fallback chain; a GT path matches when an agent
# path equals it or ends with "/"+path (suffix policy); score = found/total
# (round 2dp), passes at >= 0.5. Missing answer -> zero-score exit 0; missing GT
# -> error JSON on stderr exit 1; malformed JSON -> "Parse error" exit 0.
set -euo pipefail

export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"
export GT_FILE="$TASK_DIR/ground_truth.json"

if [[ ! -f "$ANSWER_FILE" ]]; then
  printf '{"score": 0.0, "passed": false, "detail": "No answer.json found"}\n'
  exit 0
fi
if [[ ! -f "$GT_FILE" ]]; then
  printf '{"score": 0.0, "passed": false, "detail": "No ground_truth.json found"}\n' >&2
  exit 1
fi
if ! jq -e . "$ANSWER_FILE" >/dev/null 2>&1 || ! jq -e . "$GT_FILE" >/dev/null 2>&1; then
  printf '{"score": 0.0, "passed": false, "detail": "Parse error"}\n'
  exit 0
fi

# GT paths: required_files as [{"path":...}] or ["a",...]; non-str/empty skipped.
gt_total=$(jq -r '
  (if type=="object" then (.required_files // []) else [] end)
  | (if type=="array" then . else [] end)
  | [ .[] | if type=="object" then (.path // "") elif type=="string" then . else "" end
            | select(type=="string" and . != "") ]
  | length' "$GT_FILE")

if [[ "$gt_total" -eq 0 ]]; then
  printf '{"score": 0.0, "passed": false, "detail": "No GT files"}\n'
  exit 0
fi

# found = GT paths with at least one agent path matching under suffix policy.
found=$(jq -n --slurpfile a "$ANSWER_FILE" --slurpfile g "$GT_FILE" '
  ($a[0]) as $answer | ($g[0]) as $gt
  | (if ($gt|type)=="object" then ($gt.required_files // []) else [] end)
  | (if type=="array" then . else [] end)
  | [ .[] | if type=="object" then (.path // "") elif type=="string" then . else "" end ]
  | map(select(type=="string" and . != "")) as $gt_files
  | (["source_files","files","error_source.files"]) as $keys
  | ( [ $keys[]
        | . as $dk
        | ($dk | split(".")) as $segs
        | reduce $segs[] as $s ($answer; if (.|type)=="object" then .[$s] else null end) ]
      | map(select(. != null)) | (.[0] // null) ) as $raw
  | ( if ($raw|type)=="array"
      then [ $raw[] | if type=="object" then (.path // .file // "") elif type=="string" then . else null end
                      | select(type=="string") ]
      else [] end ) as $agent_files
  | [ $gt_files[] as $gtf
      | select( any($agent_files[]; . == $gtf or endswith("/" + $gtf)) ) ]
  | length' )

round2() {
  awk -v x="$1" 'BEGIN{ s=sprintf("%.2f", x); sub(/0+$/, "", s); if (s ~ /\.$/) s=s"0"; print s }'
}
raw=$(jq -n --argjson f "$found" --argjson t "$gt_total" '$f/$t')
score=$(round2 "$raw")
passed=$(jq -rn --argjson f "$found" --argjson t "$gt_total" 'if ($f/$t)>=0.5 then "true" else "false" end')

printf '{"score": %s, "passed": %s, "detail": "Found %s/%s required files"}\n' \
  "$score" "$passed" "$found" "$gt_total"
