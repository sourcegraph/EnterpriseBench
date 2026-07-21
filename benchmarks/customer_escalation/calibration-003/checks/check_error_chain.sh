#!/usr/bin/env bash
# check_error_chain.sh — checkpoint "error_chain"
#
# Re-anchored to ground_truth.json:scoring_evidence["error_chain"] (gen-2
# discipline, precedent a51e7e8; EnterpriseBench-jn73.2.7.3.1.4). Grades ONLY
# repo-specific investigation evidence — file paths and symbol names absent
# from instruction.md — via case-insensitive fixed-string matching (no regex).
# A schema-shape-valid deliverable whose every string value is prompt text
# scores 0.0, and key presence alone earns nothing. An evidence entry is a
# string or a list of accepted alternates; each entry is one unit of credit.
set -euo pipefail

CHECKPOINT="error_chain"
ANSWER_KEYS="error_chain,chain,propagation,propagation_chain"
NO_FIELD_DETAIL="Agent provided no error chain"
WORKSPACE="${WORKSPACE:-/workspace}"
ANSWER_FILE="$WORKSPACE/agent_output/answer.json"
GT_FILE="${TASK_DIR:-}/ground_truth.json"
MAX_ANSWER_BYTES=1048576

verdict() { printf '{"score": %s, "passed": %s, "detail": "%s"}\n' "$1" "$2" "$3"; exit 0; }

# The answer key is ours, not the agent's: a missing/unreadable ground truth
# routes to re-run, never to an agent 0.0.
if [[ ! -f "$GT_FILE" ]]; then
    verdict 0.0 false "VERIFIER_INFRA_ERROR: ground_truth.json not found at $GT_FILE"
fi
# agent_output/ is agent-owned: refuse a symlinked answer, cap its size.
if [[ -L "$ANSWER_FILE" ]]; then
    verdict 0.0 false "answer.json is a symlink, not a regular file"
fi
if [[ ! -f "$ANSWER_FILE" ]]; then
    verdict 0.0 false "No answer.json found"
fi
if [[ "$(wc -c <"$ANSWER_FILE")" -gt "$MAX_ANSWER_BYTES" ]]; then
    verdict 0.0 false "answer.json exceeds $MAX_ANSWER_BYTES bytes"
fi

export ANSWER_FILE GT_FILE CHECKPOINT ANSWER_KEYS NO_FIELD_DETAIL
python3 <<'PYEOF'
import json, os

def verdict(score, passed, detail):
    print(json.dumps({"score": round(score, 2), "passed": passed, "detail": detail}))
    raise SystemExit(0)

try:
    with open(os.environ["GT_FILE"]) as fh:
        gt = json.load(fh)
except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
    verdict(0.0, False, f"VERIFIER_INFRA_ERROR: ground_truth.json unreadable: {exc}")

checkpoint = os.environ["CHECKPOINT"]
raw = (gt.get("scoring_evidence") or {}).get(checkpoint) or []
groups = [
    [str(t).lower() for t in (g if isinstance(g, list) else [g])] for g in raw
]
if not groups:
    verdict(0.0, False, f"VERIFIER_INFRA_ERROR: no scoring_evidence for {checkpoint}")

# "not a JSON object" is the detail contract for an agent-killed read
# (test_verifier_infra_detection.py): true for unparseable text, a list, or
# a bare string alike, and it marks this 0.0 as attribution, not judgement.
try:
    with open(os.environ["ANSWER_FILE"]) as fh:
        answer = json.load(fh)
except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
    verdict(0.0, False, f"answer.json is not a JSON object: {exc}")
if not isinstance(answer, dict):
    verdict(0.0, False, "answer.json is not a JSON object")

field = next(
    (answer[k] for k in os.environ["ANSWER_KEYS"].split(",") if answer.get(k)),
    None,
)
if not field:
    verdict(0.0, False, os.environ["NO_FIELD_DETAIL"])

# Fixed-string containment on the serialized field text — no regex, ever.
text = json.dumps(field, ensure_ascii=False).lower()
found = sum(1 for alts in groups if any(a in text for a in alts))
score = found / len(groups)
verdict(score, score >= 0.5,
        f"Cited {found}/{len(groups)} repo evidence groups for {checkpoint}")
PYEOF
