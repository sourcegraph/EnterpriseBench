#!/usr/bin/env bash
# check_ownership.sh — checkpoint "ownership"
#
# Re-anchored to ground_truth.json:scoring_evidence[ownership]
# (EnterpriseBench-jn73.2.7.3.1.4). Credits ONLY repo-derived evidence tokens —
# absent from instruction.md, present in the pinned repos — found in the
# answer's ownership fields. Key presence or prompt vocabulary earns 0, so a
# schema-shaped prompt echo scores 0 on every token.
set -euo pipefail

export ANSWER_FILE="${WORKSPACE:-/workspace}/agent_output/answer.json"
export GT_FILE="$TASK_DIR/ground_truth.json"
export CHECKPOINT="ownership"

if [[ ! -f "$GT_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "VERIFIER_INFRA_ERROR: no ground_truth.json"}'
    exit 1
fi
if [[ -L "$ANSWER_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "answer.json is a symlink, not a regular file"}'
    exit 0
fi
if [[ ! -f "$ANSWER_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 0
fi
if [[ "$(wc -c <"$ANSWER_FILE")" -gt 1048576 ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "answer.json exceeds 1048576 bytes"}'
    exit 0
fi

python3 -c '
import json, os, sys


def verdict(score, passed, detail):
    print(json.dumps({"score": round(score, 2), "passed": passed, "detail": detail}))
    sys.exit(0)


def infra(detail):
    print(json.dumps({"score": 0.0, "passed": False,
                      "detail": "VERIFIER_INFRA_ERROR: " + detail}))
    sys.exit(1)


try:
    with open(os.environ["GT_FILE"]) as fh:
        gt = json.load(fh)
except (json.JSONDecodeError, OSError, ValueError) as e:
    infra("unreadable ground_truth.json (%s)" % e)

checkpoint = os.environ["CHECKPOINT"]
evidence = (gt.get("scoring_evidence") or {}).get(checkpoint) if isinstance(gt, dict) else None
if not (isinstance(evidence, list) and evidence
        and all(isinstance(t, str) and t for t in evidence)):
    infra("no scoring_evidence[%s] in ground_truth.json" % checkpoint)

# The answer is agent-owned and untrusted: any failure to parse it is an agent
# failure (scored 0), never the no-verdict channel.
try:
    with open(os.environ["ANSWER_FILE"], encoding="utf-8", errors="replace") as fh:
        answer = json.load(fh)
except Exception:
    verdict(0.0, False, "Malformed answer.json")
if not isinstance(answer, dict):
    verdict(0.0, False, "answer.json is not a JSON object")


def string_leaves(node, out, depth=0):
    # Depth-capped so a pathologically nested answer degrades to a scored 0
    # instead of crashing this check into the no-verdict channel.
    if depth > 40:
        return
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for v in node.values():
            string_leaves(v, out, depth + 1)
    elif isinstance(node, list):
        for v in node:
            string_leaves(v, out, depth + 1)


field = answer.get("ownership", answer.get("owners", answer.get("subsystem")))
leaves = []
string_leaves(field, leaves)
text = "\n".join(leaves).lower()
if not text.strip():
    verdict(0.0, False, "Agent provided no ownership info")

# Fixed-string containment over string VALUES only — the same rule the
# nonprompt integrity gate applies. Keys never earn credit.
found = [t for t in evidence if t.lower() in text]
score = len(found) / len(evidence)
verdict(score, score >= 0.5,
        "Cited %d/%d repo-derived ownership evidence tokens" % (len(found), len(evidence)))
'
