#!/usr/bin/env bash
# check_expected_values.sh — checkpoint "determine_expected_values"
#
# Grades DRIFT_REPORT.json against ground_truth.json:drift_points (sealed
# root-only), curated from argo-cd PR #22035 / issue #22034 and verified by
# parsing both values.yaml files at the pinned SHAs.
#
# The old check scored two greps over the whole report: one for
# "remove"/"delete"/"omit"/"upstream default", one for "3.17"/"strict"/
# "validation"/"tighten". instruction.md supplied every one of those words — it
# asked "Should the override exist at all, or should it be removed to let the
# upstream default apply?" and named Helm 3.17.1 four times — so the checkpoint
# was satisfiable by restating the question, with no repo access.
#
# It also inverted the grade. A correct answer states the upstream default as a
# fact ("the upstream chart defaults this key to {runAsUser: 1000, ...}"), which
# contains none of those words, so it scored 0.0 here while a prompt echo scored
# 1.0. That is why a correct report totalled 0.4625 and a fabricated one 0.8125.
#
# The expected value must now be the concrete upstream default the null override
# displaces, quoted from the upstream chart. instruction.md asks for exactly that
# ("the real default as it appears in the upstream chart, not a description of
# it"), and "runtimedefault", "allowprivilegeescalation", "runasuser" and "1000"
# each appear ZERO times in it — reading dandydeveloper-charts is the only way to
# obtain them.
#
# "runasuser"/"1000" is also what separates the two drift points: the upstream
# haproxy.containerSecurityContext table has NO runAsUser, while the top-level
# containerSecurityContext sets runAsUser: 1000. So a report that finds one
# drift point cannot collect the other's credit by naming the same key twice.
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
    verdict(0.0, f"DRIFT_REPORT.json is not valid JSON: {exc}")
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
    expected_tokens = [t.lower() for t in tokens.get("expected", [])]
    hit = False
    for point in claimed:
        if not isinstance(point, dict):
            continue
        where = norm(point.get("key", "")) + norm(point.get("file", ""))
        if not all(t in where for t in ident):
            continue
        if all(t in norm(point.get("expected", "")) for t in expected_tokens):
            hit = True
            break
    found += hit
    detail.append(str(want.get("key")) + ("=hit" if hit else "=miss"))

verdict(found / len(expected),
        "Named the upstream default the override displaces for %d/%d drift points: %s"
        % (found, len(expected), ", ".join(detail)))
'
