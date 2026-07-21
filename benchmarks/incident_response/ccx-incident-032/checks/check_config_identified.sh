#!/usr/bin/env bash
# check_config_identified.sh — checkpoint "config_identified"
#
# Re-anchored to ground_truth.json:scoring_evidence[config_identified]
# (EnterpriseBench-jn73.2.7.3.1.4). The gen-1 body graded the keywords
# 'circuit breaker', 'max_connections', 'max_pending_requests', 'thresholds'
# — every one handed over by the task prompt — so the structured prompt echo
# scored 1.0 on both instruction variants. Credit now requires what the
# prompt asks the agent to FIND, not what it already says: the concrete
# protobuf path in data-plane-api (envoy/config/cluster/v3/
# circuit_breaker.proto at 84e84367) and the cluster config field notation
# (circuit_breakers — the prompt only ever writes 'circuit breaker' spaced).
# Env: WORKSPACE, TASK_DIR
set -euo pipefail

CHECKPOINT="config_identified"
WORKSPACE="${WORKSPACE:-/workspace}"
ANSWER_FILE="$WORKSPACE/agent_output/answer.json"
if [[ ! -f "$ANSWER_FILE" ]]; then
    ANSWER_FILE="$WORKSPACE/answer.json"
fi
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
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 1
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

with open(os.environ["ANSWER_FILE"], encoding="utf-8", errors="replace") as fh:
    text = fh.read().lower()

found = sum(1 for token in evidence if token.lower() in text)
verdict(found / len(evidence),
        "Identified %d/%d non-prompt circuit-breaker config anchors" % (found, len(evidence)))
'
