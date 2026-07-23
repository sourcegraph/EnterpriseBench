#!/usr/bin/env bash
# check_drift_points.sh — checkpoint "identify_drift_points"
#
# Grades DRIFT_REPORT.json against ground_truth.json:drift_points (sealed
# root-only), rebuilt from the chart at the pinned SHA 53aca511.
#
# The old check ran three substring tests over json.dumps(drift_points) — the
# whole array flattened to one blob, so the tokens did not even have to belong
# to the same drift point: 'secret' and ('key' or 'password'); 'password' and
# (optional|required|unnecessary|external); 'erlang' or 'cookie'. instruction.md
# supplies every one of those words, so a SINGLE fabricated drift point
# containing nothing but prompt vocabulary scored 3/3. The md-echo gate saw
# none of it: a `cp instruction.md` copy is not valid JSON, so json.load threw
# and the check "passed" at 0.0 (EnterpriseBench-jn73.2.7.3.1.4).
#
# Each drift point is now matched individually and must pair its file with a
# token only the chart carries. The prompt names _helpers.tpl,
# externalrabbitmq-secrets.yaml and skipper/configmap.yaml, and MISDIRECTS: the
# password/erlangCookie validation it describes lives in NOTES.txt, which the
# prompt never mentions and which contains 0 hits in _helpers.tpl. The old
# answer key fell for exactly that misdirection.
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
    expected = json.load(fh).get("drift_points") or []
if not expected:
    verdict(0.0, "VERIFIER_INFRA_ERROR: no drift_points in ground_truth.json")

try:
    with open(os.environ["REPORT"], encoding="utf-8", errors="replace") as fh:
        report = json.load(fh)
except (json.JSONDecodeError, UnicodeDecodeError) as exc:
    verdict(0.0, "DRIFT_REPORT.json is not valid JSON: " + str(exc))
if not isinstance(report, dict):
    verdict(0.0, "DRIFT_REPORT.json is not a JSON object")

claimed = report.get("drift_points")
if not isinstance(claimed, list) or not claimed:
    verdict(0.0, "DRIFT_REPORT.json has no drift_points array")

def norm(value):
    return re.sub(r"\s+", "", json.dumps(value) if not isinstance(value, str) else value).lower()

found, detail = 0, []
for want in expected:
    tokens = want.get("evidence_tokens") or {}
    ident = [t.lower() for t in tokens.get("identifies", [])]
    chain = [t.lower() for t in tokens.get("chain", [])]
    hit = False
    for point in claimed:
        if not isinstance(point, dict):
            continue
        where = norm(point.get("file", "")) + norm(point.get("key", ""))
        if not all(t in where for t in ident):
            continue
        # The file alone is not evidence — the prompt names three of these
        # paths. Credit needs the chart-only token beside it, looked for
        # anywhere in the point (agents spread the trace across
        # actual/expected/override_chain).
        rest = norm(point)
        if all(t in rest for t in chain):
            hit = True
            break
    found += hit
    detail.append(str(want.get("file", "")).split("/")[-1] + ":" + str(want.get("line", "")) + ("=hit" if hit else "=miss"))

verdict(found / len(expected),
        "Identified %d/%d real drift points with chart-only evidence: %s"
        % (found, len(expected), ", ".join(detail)))
'
