#!/usr/bin/env bash
# check_drift_points.sh — checkpoint "identify_drift_points"
#
# Grades DRIFT_REPORT.json against ground_truth.json:drift_points, which is
# sealed root-only and was curated from argo-cd PR #22035 / issue #22034 and
# verified by parsing both values.yaml files at the pinned SHAs.
#
# What changed and why: this check used to credit the report if its text merely
# contained "securitycontext" AND one of "null"/"empty"/"nil"/"unset".
# instruction.md handed the agent BOTH words — it said "Focus especially on
# securityContext fields" and "contain `null` values" — so a DRIFT_REPORT.json
# fabricated from the prompt alone, with no repo access, scored 1.0 here.
#
# Worse, the word the prompt told the agent to look for was the WRONG one:
# ArgoCD does not override securityContext at any level. The two keys it does
# override to null are redis-ha.haproxy.containerSecurityContext and
# redis-ha.containerSecurityContext. Upstream ships securityContext (pod-level)
# and containerSecurityContext (container-level) side by side at each level, so
# "securityContext" is a plausible-looking answer that is simply not the drift.
# Measured before this rebuild: a prompt-only fabrication scored 0.8125 overall
# while a correct, repo-derived report scored 0.4625 — the task paid more for
# echoing the prompt than for reading the repos.
#
# The md-echo gate could not see any of it: a `cp instruction.md` copy is not
# valid JSON, so json.load threw and this check "passed" at 0.0 while
# check_config_valid.sh handed that same copy a free 1.0
# (EnterpriseBench-jn73.2.7.3.1.4).
#
# Identification now requires the key path AND the value it is overridden to,
# both readable only in the values file. Of the graded tokens,
# "containersecuritycontext", "haproxy" and "null" appear ZERO times in
# instruction.md: the prompt never names the drifted key, never says how many
# drift points there are, and never says the overrides are null — an agent that
# has not opened the values file cannot know haproxy is one of the two levels.
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
    """Flatten a field to comparable text. An agent may hand back a string, a
    nested dict, a list, or a bare JSON null; compare content, not the shape it
    chose. json.dumps(None) == "null", so both `actual: null` and
    `actual: "containerSecurityContext: null"` carry the token."""
    return re.sub(r"\s+", "", json.dumps(value) if not isinstance(value, str) else value).lower()

found, detail = 0, []
for want in expected:
    tokens = want.get("evidence_tokens") or {}
    ident = [t.lower() for t in tokens.get("identifies", [])]
    actual_tokens = [t.lower() for t in tokens.get("actual", [])]
    hit = False
    for point in claimed:
        if not isinstance(point, dict):
            continue
        where = norm(point.get("key", "")) + norm(point.get("file", ""))
        if not all(t in where for t in ident):
            continue
        # The key path alone is not evidence enough: credit only if the point
        # also carries the value the key is actually overridden to, which is
        # readable only in ArgoCD values.yaml.
        if all(t in norm(point.get("actual", "")) for t in actual_tokens):
            hit = True
            break
    found += hit
    detail.append(str(want.get("key")) + ("=hit" if hit else "=miss"))

verdict(found / len(expected),
        "Identified %d/%d real drift points with the overridden value: %s"
        % (found, len(expected), ", ".join(detail)))
'
