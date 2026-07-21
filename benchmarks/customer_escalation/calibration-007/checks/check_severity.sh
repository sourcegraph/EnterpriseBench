#!/usr/bin/env bash
# check_severity.sh — checkpoint "severity"
#
# Re-anchored to ground_truth.json:scoring_evidence[severity]
# (EnterpriseBench-jn73.2.7.3.1.4). The gen-1 check credited 1.0 when
# expected_severity appeared as a SUBSTRING of the field (the ticket header
# hands that vocabulary over) and 0.3 for ANY non-empty value — structure-only
# credit. Now the severity level must equal expected_severity EXACTLY, and all
# credit scales with non-prompt evidence — pinned-repo symbols that justify the
# assessment. A level guess with no repo evidence earns 0. ground_truth.json is
# sealed root-only.
set -euo pipefail

CHECKPOINT="severity"
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
expected = str(gt.get("expected_severity") or "").strip().lower()
if not expected:
    verdict(0.0, "VERIFIER_INFRA_ERROR: no expected_severity in ground truth")

with open(os.environ["ANSWER"], encoding="utf-8", errors="replace") as fh:
    raw = fh.read()
try:
    answer = json.loads(raw)
except ValueError:
    verdict(0.0, "answer.json is not valid JSON")

field = answer.get("severity", answer.get("impact")) if isinstance(answer, dict) else None
if isinstance(field, dict):
    field = field.get("level", field.get("severity", field.get("value")))
level = field.strip().lower() if isinstance(field, str) else ""
if not level:
    verdict(0.0, "No severity level provided")
if level != expected:
    verdict(0.0, "Severity level does not match the assessed impact")

text = raw.lower()
found = sum(1 for token in evidence if token.lower() in text)
verdict(found / len(evidence),
        "Severity level correct; cited %d/%d non-prompt evidence tokens" % (found, len(evidence)))
'
