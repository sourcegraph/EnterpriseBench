#!/usr/bin/env bash
# check_drift_points.sh — checkpoint "identify_drift_points"
#
# Grades DRIFT_REPORT.json against ground_truth.json:drift_points (sealed
# root-only), rebuilt from the chart at the pinned SHA 478a81c9.
#
# The old check asked two things of the report: that its text contain "password"
# plus any of different/random/regenerat/re-evaluat/mismatch, and that it name
# at least TWO DISTINCT FILES — any two. instruction.md supplies every one of
# those words, and "name two files" is not evidence of anything, so a report
# fabricated from the prompt scored full credit. The md-echo gate never saw it:
# a `cp instruction.md` copy is not valid JSON, so json.load threw and the check
# "passed" at 0.0 (EnterpriseBench-jn73.2.7.3.1.4).
#
# What actually requires reading the chart is WHICH files call the helper. At
# this SHA `include "redis.password"` appears in exactly three templates, and
# only two render under the task's scenario (configmap.yaml is gated by
# auth.acl.enabled, default false). secret-svcbind.yaml — the one the bug is
# actually about — is named 0 times in instruction.md, and the prompt no longer
# enumerates the consumers, so an agent has to find them.
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
    gt = json.load(fh)
expected = gt.get("drift_points") or []
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
    ident = [t.lower() for t in (want.get("evidence_tokens") or {}).get("identifies", [])]
    hit = any(
        isinstance(p, dict)
        and all(t in norm(p.get("file", "")) + norm(p.get("key", "")) for t in ident)
        for p in claimed
    )
    found += hit
    detail.append(str(want.get("file", "")).split("/")[-1] + ("=hit" if hit else "=miss"))

# Precision guard. The OLD answer key asserted health-configmap.yaml and
# master/application.yaml were drift points; neither calls the helper at all
# (0 hits chart-wide) — health-configmap reads the password at container runtime
# from REDIS_PASSWORD_FILE, master/application mounts the secret by reference.
# Naming them repeats a false claim rather than reporting the chart, so recall
# alone must not carry the checkpoint.
false_positives = []
for entry in gt.get("not_drifted") or []:
    name = str(entry.get("file", "")).split("/")[-1]
    if not name or "acl" in str(entry.get("note", "")).lower():
        continue  # configmap.yaml IS a real call site, just not rendered here
    if any(isinstance(p, dict) and name.lower() in norm(p.get("file", "")) for p in claimed):
        false_positives.append(name)

score = found / len(expected)
if false_positives:
    score *= 0.5
    detail.append("penalised for non-call-sites: " + ", ".join(false_positives))

verdict(score, "Found %d/%d rendered call sites: %s" % (found, len(expected), ", ".join(detail)))
'
