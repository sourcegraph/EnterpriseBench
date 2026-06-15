#!/usr/bin/env bash
# check_contract_source.sh — verify agent identifies well-known type definitions
# Reimplemented in bash+jq+grep (no python3 in container — the task image ships
# bash/grep/jq but not python3, so the previous `python3 -c` body exited 127).
# Scoring is identical: file_score = found / max(#protobuf-gt-files, 1) over
# json.dumps(answer).lower() substring matches (full path or basename), keyword
# (WKT) coverage kw_score = min(1, kw_found/3); score = round(file_score*0.6 + kw*0.4, 2),
# pass when found>=1 and kw_found>=2.
set -euo pipefail

export ANSWER_FILE="${WORKSPACE:-/workspace}/agent_output/answer.json"
export GT_FILE="$TASK_DIR/ground_truth.json"

if [[ ! -f "$ANSWER_FILE" ]]; then
  printf '{"score": 0.0, "passed": false, "detail": "No answer.json found"}\n'
  exit 0
fi

# Missing ground_truth.json crashed the original python (FileNotFoundError, rc=1,
# no score line) — preserve that infra-failure signal rather than emit a fake score.
if [[ ! -f "$GT_FILE" ]]; then
  echo "ground_truth.json not found: $GT_FILE" >&2
  exit 1
fi

# json.dumps(answer).lower() — python default separators ", " / ": " (with spaces).
answer_text=$(jq -c . "$ANSWER_FILE" | sed 's/,/, /g; s/:/: /g' | tr '[:upper:]' '[:lower:]')

# gt_files: required_files + sufficient_files, repo == protobuf, lowercased path + basename.
mapfile -t gt_files < <(jq -r '
  ((.required_files // []) + (.sufficient_files // []))
  | .[] | select(.repo == "protobuf") | .path' "$GT_FILE")
total=${#gt_files[@]}

found=0
for f in "${gt_files[@]}"; do
  fl=$(printf '%s' "$f" | tr '[:upper:]' '[:lower:]')
  bl=$(printf '%s' "${f##*/}" | tr '[:upper:]' '[:lower:]')
  if [[ "$answer_text" == *"$fl"* || "$answer_text" == *"$bl"* ]]; then
    found=$((found + 1))
  fi
done

keywords=(timestamp duration fieldmask struct any.proto)
nkw=${#keywords[@]}
kw_found=0
for kw in "${keywords[@]}"; do
  if [[ "$answer_text" == *"$kw"* ]]; then kw_found=$((kw_found + 1)); fi
done

# score = round(file_score*0.6 + kw_score*0.4, 2) ; banker's rounding to match python.
score=$(jq -n --argjson found "$found" --argjson total "$total" --argjson kw "$kw_found" '
  ($found / ([$total, 1] | max)) as $fs
  | ([1.0, ($kw / 3.0)] | min) as $ks
  | ($fs * 0.6 + $ks * 0.4) as $s
  | ($s * 100) as $h
  | ($h | floor) as $fl
  | ($h - $fl) as $frac
  | (if $frac > 0.5 then $fl + 1
     elif $frac < 0.5 then $fl
     else (if ($fl % 2) == 0 then $fl else $fl + 1 end) end) as $r
  | ($r / 100)
  | if . == (. | floor) then "\(.).0" else (. | tostring) end' | tr -d '"')

passed=$(jq -n --argjson found "$found" --argjson kw "$kw_found" 'if ($found >= 1 and $kw >= 2) then true else false end')
printf '{"score": %s, "passed": %s, "detail": "Protobuf files: %s/%s, WKT keywords: %s/%s"}\n' \
    "$score" "$passed" "$found" "$total" "$kw_found" "$nkw"
