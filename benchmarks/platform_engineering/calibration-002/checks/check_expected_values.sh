#!/usr/bin/env bash
# check_expected_values.sh — checkpoint "determine_expected_values"
#
# Grades answer.json against ground_truth.json:drift_points (sealed root-only),
# verified against pallets/click at the pinned tag 8.2.1.
#
# The old check credited an answer containing the ground-truth "expected"
# strings verbatim — one of which ("CLI > envvar > default > prompt") sits in
# instruction.md, so a prompt echo scored 0.5 with zero repo access
# (EnterpriseBench-jn73.2.7.3.1.4). The resolution behavior must now be
# explained through the mechanism that exists only in the repo:
# Option.resolve_envvar_value returning None on an unset envvar
# (src/click/core.py:2961) and the Option.consume_value prompt gate that fires
# on ParameterSource.DEFAULT unless prompt_required is False
# (src/click/core.py:3023-3031). Neither symbol appears in instruction.md.
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
    verdict(0.0, f"answer.json is not valid JSON: {exc}")
if not isinstance(report, dict):
    verdict(0.0, "answer.json is not a JSON object")

claimed = report.get("drift_points")
if not isinstance(claimed, list) or not claimed:
    verdict(0.0, "answer.json has no drift_points array")

def norm(value):
    return re.sub(r"\s+", "", json.dumps(value) if not isinstance(value, str) else value).lower()

found, detail = 0, []
for want in expected:
    tokens = want.get("evidence_tokens") or {}
    ident = [t.lower() for t in tokens.get("identifies", [])]
    mechanism = [t.lower() for t in tokens.get("mechanism", [])]
    hit = False
    for point in claimed:
        if not isinstance(point, dict):
            continue
        whole = norm(point)
        if not all(t in whole for t in ident):
            continue
        # The source name alone is prompt vocabulary. The resolution behavior
        # must name the mechanism that is readable only in the repo.
        if all(t in whole for t in mechanism):
            hit = True
            break
    found += hit
    detail.append(str(want.get("key")) + ("=hit" if hit else "=miss"))

verdict(found / len(expected),
        "Correct resolution mechanism for %d/%d drift points: %s"
        % (found, len(expected), ", ".join(detail)))
'
