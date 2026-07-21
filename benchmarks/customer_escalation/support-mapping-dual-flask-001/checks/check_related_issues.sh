#!/usr/bin/env bash
# check_related_issues.sh — checkpoint "related_issues"
#
# Re-anchored (EnterpriseBench-jn73.2.7.3.1.4): grades the ground-truth
# related_references by ANCHORED path match only — exact, or a suffix on a "/"
# boundary. The previous token-overlap fuzz credited prompt vocabulary; now a
# reference earns credit only as a full repo path, which the prompt never
# supplies. A basename or concept word alone earns nothing.
set -euo pipefail

export ANSWER_FILE="${WORKSPACE:-/workspace}/agent_output/answer.json"
export GT_FILE="$TASK_DIR/ground_truth.json"

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
if not isinstance(gt, dict):
    infra("ground_truth.json is not a JSON object")

refs = gt.get("related_references")
if not isinstance(refs, list) or not refs:
    inner = gt.get("ground_truth", gt)
    suff = inner.get("sufficient_files") if isinstance(inner, dict) else None
    refs = [sf.get("path") for sf in suff or [] if isinstance(sf, dict)]
refs = [r for r in refs if isinstance(r, str) and r.strip()]
if not refs:
    infra("no related_references in ground_truth.json")

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


field = answer.get("related_issues",
                   answer.get("references",
                              answer.get("related", answer.get("docs"))))
leaves = []
string_leaves(field, leaves)
agent_refs = [l.strip().strip("`") for l in leaves if l.strip()]
if not agent_refs:
    verdict(0.0, False, "Agent provided no related references")

# Anchored match only: exact, or a suffix on a path boundary (an agent path may
# carry a repo-dir prefix). Unanchored substring overlap would let prompt text
# or path-spam soft-cheat.
matched = sum(
    1 for ref in refs
    if any(af == ref or af.endswith("/" + ref) for af in agent_refs)
)
score = matched / len(refs)
verdict(score, score >= 0.3,
        "Matched %d/%d related references (anchored path match)" % (matched, len(refs)))
'
