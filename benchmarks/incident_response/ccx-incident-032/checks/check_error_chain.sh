#!/usr/bin/env bash
# check_error_chain.sh — checkpoint "error_chain_traced"
#
# Re-anchored to ground_truth.json:scoring_evidence[error_chain_traced]
# (EnterpriseBench-jn73.2.7.3.1.4). The gen-1 body bag-of-words-matched the
# ground-truth chain prose ('connection', 'overflow', 'upstream', 'reset',
# ...) against the answer with a 50% threshold — vocabulary the task prompt
# hands over — so the structured prompt echo scored 1.0 on both instruction
# variants. Credit now requires the chain to hit the concrete hops the prompt
# never names: the pool implementation (conn_pool_base) that enforces the
# limits and the router file (upstream_request) that propagates the reset
# downstream, both verified at envoy v1.31.2. The prompt only ever gives the
# source/common/conn_pool/ directory, never these files.
# Env: WORKSPACE, TASK_DIR
set -euo pipefail

CHECKPOINT="error_chain_traced"
WORKSPACE="${WORKSPACE:-/workspace}"
ANSWER_FILE="$WORKSPACE/agent_output/answer.json"
if [[ ! -f "$ANSWER_FILE" ]]; then
    ANSWER_FILE="$WORKSPACE/answer.json"
fi
GT="${TASK_DIR:-}/ground_truth.json"
MAX_ANSWER_BYTES=1048576

verdict() { printf '{"score": %s, "passed": %s, "detail": "%s"}\n' "$1" "$2" "$3"; exit 0; }

if [[ ! -f "$GT" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No ground_truth.json found"}'
    exit 1
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

try:
    with open(os.environ["ANSWER_FILE"], encoding="utf-8", errors="replace") as fh:
        answer = json.load(fh)
except (ValueError, UnicodeDecodeError):
    verdict(0.0, "answer.json is not valid JSON")

agent_chain = answer.get("chain", answer.get("error_chain", answer.get("text", ""))) \
    if isinstance(answer, dict) else ""
if isinstance(agent_chain, list):
    agent_text = " ".join(str(s) for s in agent_chain)
else:
    agent_text = str(agent_chain)
agent_text = agent_text.lower()

found = sum(1 for token in evidence if token.lower() in agent_text)
verdict(found / len(evidence),
        "Chain hits %d/%d non-prompt hop anchors" % (found, len(evidence)))
'
