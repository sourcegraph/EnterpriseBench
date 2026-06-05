#!/usr/bin/env bash
# check_related_issues.sh — verify agent identified related issues and documentation
# Reads agent output from $WORKSPACE/agent_output/answer.json
# Checks for references to related docs, PRs, configuration guides
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

# bash+jq+grep (no python3 in container).
# related_refs = gt.related_references else (gt.ground_truth // gt).sufficient_files[].path;
# agent_refs = EVERY string scalar at any nesting depth in answer.json plus every object
# key (the whole structure flattened). A gt ref counts as matched when, for some agent ref,
# the fraction of gt's significant tokens (split on / and ., lowercased, stopwords removed)
# that also appear in the agent ref's significant tokens is >= 0.5.
# score = round(found/len(related_refs), 2), passed at >= 0.3.
#
# Why flatten the whole answer: instruction.md never names a specific top-level key, so
# agents put correct gold paths nested under root_cause / code_mapping / free-form prose.
# The earlier version read only the 4 top-level keys (related_issues/references/related/
# docs), so a correctly-identified-but-nested path scored 0.0. Flattening matches the ref
# wherever it lives. Threshold/weight are unchanged — only the search surface is widened.

# related_refs (newline-delimited). Fallback to sufficient_files[].path when empty/absent.
related_refs=$(jq -r '
  (if has("related_references") then .related_references else [] end) as $rr
  | (if ($rr|length) > 0 then $rr
     else ((.ground_truth // .) | .sufficient_files // [] | map(.path)) end)
  | .[]' "$GT_FILE")

if [[ -z "$related_refs" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No related references in ground truth"}'
    exit 0
fi
total=$(printf '%s\n' "$related_refs" | grep -c . || true)

# agent_refs: flatten the entire answer.json to every string scalar (any depth) plus
# every object key, deduped. agent_n guards the "no references at all" case.
FLATTEN='[ (.. | strings), (paths | .[] | select(type=="string")) ] | unique'
agent_n=$(jq -r "$FLATTEN | length" "$ANSWER_FILE")

if [[ "$agent_n" -eq 0 ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "Agent provided no related references"}'
    exit 0
fi

agent_refs=$(jq -r "$FLATTEN | .[]" "$ANSWER_FILE")

# Count gt refs matched by any agent ref via significant-token overlap >= 0.5.
# Tokenization: lowercase, replace "/" and "." with spaces, split on whitespace, drop
# stopwords, dedup. A gt ref matches when |gt_sig & ag_sig| / |gt_sig| >= 0.5.
found=$(awk '
  BEGIN{
    split("src lib the and for main java py go ts js rs cpp h", s, " ")
    for (i in s) stop[s[i]]=1
  }
  NR==FNR { gtref[FNR]=$0; gtn=FNR; next }
  { agref[FNR]=$0; agn=FNR }
  END{
    for (a=1; a<=agn; a++){
      line=tolower(agref[a]); gsub(/[\/.]/, " ", line)
      nA=split(line, w, /[ \t\n]+/)
      for (i=1;i<=nA;i++){ if (w[i]!="" && !(w[i] in stop)) AGset[a SUBSEP w[i]]=1 }
    }
    matched=0
    for (g=1; g<=gtn; g++){
      gl=tolower(gtref[g]); gsub(/[\/.]/, " ", gl)
      nG=split(gl, gw, /[ \t\n]+/); delete G; gsig=0
      for (i=1;i<=nG;i++){ if (gw[i]!="" && !(gw[i] in stop) && !(gw[i] in G)){ G[gw[i]]=1; gsig++ } }
      hit=0
      if (gsig>0){
        for (a=1; a<=agn; a++){
          inter=0
          for (k in G){ if ((a SUBSEP k) in AGset) inter++ }
          if (inter/gsig >= 0.5){ hit=1; break }
        }
      }
      if (hit) matched++
    }
    print matched
  }
' <(printf '%s\n' "$related_refs") <(printf '%s\n' "$agent_refs"))

score=$(awk -v f="$found" -v t="$total" 'BEGIN{printf "%.10f", f/t}')
pyfloat() { printf '%.*f' "$2" "$1" | sed -e 's/0*$//' -e 's/\.$/.0/'; }
score_r=$(pyfloat "$score" 2)
passed=$(awk -v s="$score" 'BEGIN{print (s>=0.3)?"true":"false"}')
printf '{"score": %s, "passed": %s, "detail": "Matched %s/%s related references"}\n' \
  "$score_r" "$passed" "$found" "$total"
