#!/usr/bin/env bash
# check_severity.sh — checkpoint "severity"
#
# Re-anchored to ground_truth.json:scoring_evidence[severity]
# (EnterpriseBench-jn73.2.7.3.1.4). The severity level is graded ONLY when the
# answer also cites repo-derived mechanism evidence (tokens absent from
# instruction.md, present in the pinned repos): a level word alone — which the
# prompt often hands over — earns 0. Level matching is word-boundary anchored,
# so e.g. "overflow" can never read as severity "low".
set -euo pipefail

export ANSWER_FILE="${WORKSPACE:-/workspace}/agent_output/answer.json"
export GT_FILE="$TASK_DIR/ground_truth.json"
export CHECKPOINT="severity"

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
import json, os, re, sys


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

LEVELS = {"low": 0, "medium": 1, "high": 2, "critical": 3}
expected = gt.get("expected_severity") if isinstance(gt, dict) else None
expected = expected.lower() if isinstance(expected, str) else ""
if expected not in LEVELS:
    infra("expected_severity missing or unknown in ground_truth.json")

checkpoint = os.environ["CHECKPOINT"]
evidence = (gt.get("scoring_evidence") or {}).get(checkpoint)
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


sev_field = answer.get("severity", answer.get("impact", answer.get("priority")))
sev_leaves = []
string_leaves(sev_field, sev_leaves)
sev_text = "\n".join(sev_leaves).lower()
if not sev_text.strip():
    verdict(0.0, False, "Agent provided no severity assessment")

# Mechanism evidence may sit anywhere in the answer (rationale, code paths),
# but only in string VALUES — keys never earn credit.
all_leaves = []
string_leaves(answer, all_leaves)
full_text = "\n".join(all_leaves).lower()
found = [t for t in evidence if t.lower() in full_text]

m = re.search(r"\b(critical|high|medium|low)\b", sev_text)
if m:
    severity_score = max(0.0, 1.0 - abs(LEVELS[expected] - LEVELS[m.group(1)]) * 0.4)
else:
    severity_score = 0.0

if not found:
    verdict(0.0, False,
            "Severity level alone earns nothing: 0/%d repo-derived mechanism "
            "tokens cited" % len(evidence))

score = 0.6 * severity_score + 0.4 * (len(found) / len(evidence))
passed = severity_score >= 0.6
verdict(score, passed,
        "severity_score=%.2f (expected %s), %d/%d mechanism evidence tokens"
        % (severity_score, expected, len(found), len(evidence)))
'
