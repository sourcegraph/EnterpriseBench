#!/usr/bin/env bash
# check_schema_change.sh — checkpoint "schema_change"
#
# Re-anchored to ground_truth.json:scoring_evidence[schema_change]
# (EnterpriseBench-jn73.2.7.3.1.4). Credits ONLY non-prompt evidence: every
# graded token is absent from instruction.md and present in the pinned repos
# (supabase v0.24.03 docker/volumes/db + gotrue v2.143.0 migrations),
# so a schema-shape-valid deliverable whose only content is prompt text scores
# 0. Structure alone earns nothing — only investigation-derived values count.
set -euo pipefail

CHECKPOINT="schema_change"
WORKSPACE="${WORKSPACE:-/workspace}"
ANSWER_FILE="$WORKSPACE/agent_output/answer.json"
GT="${TASK_DIR:-}/ground_truth.json"
MAX_ANSWER_BYTES=1048576

verdict() { printf '{"score": %s, "passed": %s, "detail": "%s"}\n' "$1" "$2" "$3"; exit 0; }

if [[ ! -f "$GT" ]]; then
  verdict 0.0 false "VERIFIER_INFRA_ERROR: ground_truth.json not found at $GT"
fi
if [[ -L "$ANSWER_FILE" ]]; then
  verdict 0.0 false "answer.json is a symlink, not a regular file"
fi
if [[ ! -f "$ANSWER_FILE" ]]; then
  verdict 0.0 false "No answer.json found"
fi
if [[ "$(wc -c <"$ANSWER_FILE")" -gt "$MAX_ANSWER_BYTES" ]]; then
  verdict 0.0 false "answer.json exceeds ${MAX_ANSWER_BYTES} bytes"
fi

export ANSWER_FILE GT CHECKPOINT
python3 -c '
import json, os

def verdict(score, detail):
    print(json.dumps({"score": round(score, 2), "passed": score >= 0.5, "detail": detail}))
    raise SystemExit(0)

with open(os.environ["GT"]) as fh:
    gt = json.load(fh)
evidence = (gt.get("scoring_evidence") or {}).get(os.environ["CHECKPOINT"]) or []
if not evidence:
    verdict(0.0, "VERIFIER_INFRA_ERROR: no scoring_evidence for " + os.environ["CHECKPOINT"])

try:
    with open(os.environ["ANSWER_FILE"], encoding="utf-8", errors="replace") as fh:
        answer = json.load(fh)
except json.JSONDecodeError:
    verdict(0.0, "answer.json is not valid JSON")

text = json.dumps(answer).lower()
found = sum(1 for token in evidence if token.lower() in text)
verdict(found / len(evidence),
        "Cited %d/%d pinned-repo evidence tokens for %s" % (found, len(evidence), os.environ["CHECKPOINT"]))
'
