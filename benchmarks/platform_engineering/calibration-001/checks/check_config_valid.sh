#!/usr/bin/env bash
# check_config_valid.sh — checkpoint "validate_corrected_config"
#
# Grades the top-level override_chain + fix of answer.json against
# ground_truth.json:scoring_evidence[validate_corrected_config] (sealed
# root-only), verified against pallets/flask at the pinned tag 3.1.0.
#
# What changed and why: the old check paid 1.0 for the mere PRESENCE of the
# override_chain and fix keys — keys instruction.md itself spells out in the
# answer template — so any schema-shaped payload, prompt echo included, earned
# full credit with zero repo access (the check_config_valid exploit class,
# EnterpriseBench-jn73.2.7.3.1.4). Structure alone now earns 0. Credit requires
# the VALUES to carry the chain facts only the repo shows: from_envvar loads
# through from_pyfile (src/flask/config.py:124), and only UPPERCASE module
# attributes survive from_object (src/flask/config.py:252-254). Neither token
# appears in instruction.md.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPORT="$WORKSPACE/agent_output/answer.json"
GT="${TASK_DIR:-}/ground_truth.json"
MAX_REPORT_BYTES=1048576

verdict() { printf '{"score": %s, "passed": %s, "detail": "%s"}\n' "$1" "$2" "$3"; exit 0; }

if [[ ! -f "$GT" ]]; then
  verdict 0.0 false "VERIFIER_INFRA_ERROR: ground_truth.json not found at $GT"
fi
if [[ -L "$REPORT" ]]; then
  verdict 0.0 false "answer.json is a symlink, not a regular file"
fi
if [[ ! -f "$REPORT" ]]; then
  verdict 0.0 false "answer.json not found"
fi
if [[ "$(wc -c <"$REPORT")" -gt "$MAX_REPORT_BYTES" ]]; then
  verdict 0.0 false "answer.json exceeds ${MAX_REPORT_BYTES} bytes"
fi

export REPORT GT
python3 -c '
import json, os, re

CHECKPOINT = "validate_corrected_config"

def verdict(score, detail):
    print(json.dumps({"score": round(score, 2), "passed": score >= 0.5, "detail": detail}))
    raise SystemExit(0)

with open(os.environ["GT"]) as fh:
    gt = json.load(fh)
evidence = (gt.get("scoring_evidence") or {}).get(CHECKPOINT) or []
if not evidence:
    verdict(0.0, "VERIFIER_INFRA_ERROR: no scoring_evidence for " + CHECKPOINT)

try:
    with open(os.environ["REPORT"], encoding="utf-8", errors="replace") as fh:
        report = json.load(fh)
except (json.JSONDecodeError, UnicodeDecodeError) as exc:
    verdict(0.0, f"answer.json is not valid JSON: {exc}")
if not isinstance(report, dict):
    verdict(0.0, "answer.json is not a JSON object")

def norm(value):
    return re.sub(r"\s+", "", json.dumps(value) if not isinstance(value, str) else value).lower()

# Only the fields this checkpoint is about: the corrected override chain and
# the fix description. Presence of the keys earns nothing — the values must
# carry the repo-only evidence.
explanation = norm(report.get("override_chain", "")) + norm(report.get("fix", ""))
if not explanation.strip("\"[]{}"):
    verdict(0.0, "override_chain and fix are missing or empty")

found = sum(1 for token in evidence if token.lower() in explanation)
verdict(found / len(evidence),
        "Override chain and fix carry %d/%d repo-anchored chain facts" % (found, len(evidence)))
'
