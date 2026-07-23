#!/usr/bin/env bash
# check_root_cause.sh — checkpoint "root_cause_mechanism"
#
# Grades DRIFT_REPORT.json:root_cause against
# ground_truth.json:scoring_evidence[root_cause] (sealed root-only).
#
# "Helm re-evaluates the include" is NOT gradeable here, however true: the
# prompt states it outright ("a fundamental issue with how Helm evaluates
# template includes"). Crediting it credits the prompt. Everything the old
# check looked for was that same supplied vocabulary.
#
# The part that requires following the code is WHERE the randomness comes from.
# redis/_helpers.tpl:206-210 does not randomise; it delegates to
# common.secrets.passwords.manage, and randAlphaNum lives one level down in the
# common subchart (bitnami/common/templates/_secrets.tpl). Both tokens appear 0
# times in instruction.md, so reaching either means the agent opened the helper
# and followed it out of the chart. The old answer key asserted _helpers.tpl
# "calls randAlphaNum on every invocation" — randAlphaNum occurs 0 times in that
# file, which is how a key written from the prompt rather than the source reads.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPORT="$WORKSPACE/agent_output/DRIFT_REPORT.json"
GT="${TASK_DIR:-}/ground_truth.json"
MAX_REPORT_BYTES=1048576

verdict() { printf '{"score": %s, "passed": %s, "detail": "%s"}\n' "$1" "$2" "$3"; exit 0; }

if [[ ! -f "$GT" ]]; then
  verdict 0.0 false "VERIFIER_INFRA_ERROR: ground_truth.json not found at $GT"
fi
if [[ -L "$REPORT" ]]; then
  verdict 0.0 false "DRIFT_REPORT.json is a symlink, not a regular file"
fi
if [[ ! -f "$REPORT" ]]; then
  verdict 0.0 false "DRIFT_REPORT.json not found"
fi
if [[ "$(wc -c <"$REPORT")" -gt "$MAX_REPORT_BYTES" ]]; then
  verdict 0.0 false "DRIFT_REPORT.json exceeds ${MAX_REPORT_BYTES} bytes"
fi

export REPORT GT
python3 -c '
import json, os, re

def verdict(score, detail):
    print(json.dumps({"score": round(score, 2), "passed": score >= 0.5, "detail": detail}))
    raise SystemExit(0)

with open(os.environ["GT"]) as fh:
    evidence = (json.load(fh).get("scoring_evidence") or {}).get("root_cause") or []
if not evidence:
    verdict(0.0, "VERIFIER_INFRA_ERROR: no scoring_evidence[root_cause] in ground_truth.json")

try:
    with open(os.environ["REPORT"], encoding="utf-8", errors="replace") as fh:
        report = json.load(fh)
except (json.JSONDecodeError, UnicodeDecodeError) as exc:
    verdict(0.0, "DRIFT_REPORT.json is not valid JSON: " + str(exc))
if not isinstance(report, dict):
    verdict(0.0, "DRIFT_REPORT.json is not a JSON object")

# The instruction asks for a top-level root_cause string. Accept the trace
# wherever the agent put it (root_cause, or an override_chain that walks the
# helper out to the common subchart) — the claim is what is graded, not the
# field it landed in.
haystack = json.dumps(report.get("root_cause", "")) + json.dumps(report.get("drift_points", ""))
haystack = re.sub(r"\s+", "", haystack).lower()

found = [t for t in evidence if re.sub(r"\s+", "", t).lower() in haystack]
verdict(len(found) / len(evidence),
        "Traced %d/%d non-prompt root-cause anchors (%s)"
        % (len(found), len(evidence), ", ".join(found) if found else "none"))
'
