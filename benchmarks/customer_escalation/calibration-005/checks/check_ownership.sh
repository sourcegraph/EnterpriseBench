#!/usr/bin/env bash
# check_ownership.sh — checkpoint "ownership"
#
# Re-anchored to ground_truth.json:scoring_evidence[ownership]
# (EnterpriseBench-jn73.2.7.3.1.4). Credits ONLY non-prompt evidence — symbols
# that exist in the pinned repo and are absent from every instruction*.md — so
# a schema-shape-valid deliverable whose only content is prompt text scores 0.
# Key presence alone earns nothing: credit requires naming the responsible
# code, which requires reading the repo. ground_truth.json is sealed root-only.
set -euo pipefail

CHECKPOINT="ownership"
WORKSPACE="${WORKSPACE:-/workspace}"
ANSWER="$WORKSPACE/agent_output/answer.json"
GT="${TASK_DIR:-}/ground_truth.json"
MAX_ANSWER_BYTES=1048576

verdict() { printf '{"score": %s, "passed": %s, "detail": "%s"}\n' "$1" "$2" "$3"; exit 0; }

if [[ ! -f "$GT" ]]; then
  verdict 0.0 false "VERIFIER_INFRA_ERROR: ground_truth.json not found at $GT"
fi
if [[ -L "$ANSWER" ]]; then
  verdict 0.0 false "answer.json is a symlink, not a regular file"
fi
if [[ ! -f "$ANSWER" ]]; then
  verdict 0.0 false "No answer.json found"
fi
if [[ "$(wc -c <"$ANSWER")" -gt "$MAX_ANSWER_BYTES" ]]; then
  verdict 0.0 false "answer.json exceeds ${MAX_ANSWER_BYTES} bytes"
fi

export ANSWER GT CHECKPOINT
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

with open(os.environ["ANSWER"], encoding="utf-8", errors="replace") as fh:
    raw = fh.read()
try:
    json.loads(raw)
except ValueError:
    verdict(0.0, "answer.json is not valid JSON")

text = raw.lower()
found = sum(1 for token in evidence if token.lower() in text)
verdict(found / len(evidence),
        "Cited %d/%d non-prompt evidence tokens for %s" % (found, len(evidence), os.environ["CHECKPOINT"]))
'
