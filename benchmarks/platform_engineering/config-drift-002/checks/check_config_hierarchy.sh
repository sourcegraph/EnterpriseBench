#!/usr/bin/env bash
# check_config_hierarchy.sh — checkpoint "config_hierarchy"
#
# Grades DRIFT_REPORT.json against
# ground_truth.json:scoring_evidence[config_hierarchy] (sealed root-only).
#
# The instruction's first ask is a trace of the external-RabbitMQ config through
# the template layers. What proves the trace happened is reaching the layers the
# prompt does NOT hand over:
#
#   notes.txt              — where the password/erlangCookie validation actually
#                            lives. The prompt describes that validation but
#                            names _helpers.tpl, externalrabbitmq-secrets.yaml
#                            and skipper/configmap.yaml instead; NOTES.txt is
#                            mentioned 0 times. _helpers.tpl contains 0 hits for
#                            auth.password and erlangCookie.
#   rabbitmq-password      — the hardcoded secret KEY (externalrabbitmq-secrets
#                            .yaml:18, skipper/configmap.yaml:52). The prompt
#                            says a key cannot be specified but never names it.
#   scdf.rabbitmq.fullname — what NOTES.txt:77 resolves the secret name through,
#                            instead of scdf.rabbitmq.secretName.
#
# Each is scored on its own (found/len), so every token must be absent from the
# prompt; all three are (verified with fixed-string grep -coF). Note the
# discipline: "rabbitmq.auth.erlangCookie" leaks from the prompt but the secret
# field "rabbitmq-erlang-cookie" does not, and a regex "." conflates them.
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
    evidence = (json.load(fh).get("scoring_evidence") or {}).get("config_hierarchy") or []
if not evidence:
    verdict(0.0, "VERIFIER_INFRA_ERROR: no scoring_evidence[config_hierarchy] in ground_truth.json")

try:
    with open(os.environ["REPORT"], encoding="utf-8", errors="replace") as fh:
        report = json.load(fh)
except (json.JSONDecodeError, UnicodeDecodeError) as exc:
    verdict(0.0, "DRIFT_REPORT.json is not valid JSON: " + str(exc))
if not isinstance(report, dict):
    verdict(0.0, "DRIFT_REPORT.json is not a JSON object")

# The trace may be spread across drift_points, override_chain entries or a
# free-text summary; grade the claim, not the field it landed in.
haystack = re.sub(r"\s+", "", json.dumps(report)).lower()
found = [t for t in evidence if re.sub(r"\s+", "", t).lower() in haystack]

verdict(len(found) / len(evidence),
        "Trace reached %d/%d chart-only layers (%s)"
        % (len(found), len(evidence), ", ".join(found) if found else "none"))
'
