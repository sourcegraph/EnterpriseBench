#!/usr/bin/env bash
# check_impact_classification.sh — checkpoint "classify_change_impact"
#
# This was the worst free-credit hole in the family: the old check lowercased the
# whole report and scored 1.0 if the bare string 'minor' appeared ANYWHERE. The
# old instruction.md said "Both changes are additive features, so I'd expect
# minor bumps, but verify" and "they're both landing in the same minor release",
# so a `cp instruction.md IMPACT_REPORT.md` copy took this entire 0.45-weight
# checkpoint. The answer was in the question, twice, and one word of it was
# enough (EnterpriseBench-jn73.2.7.3.1.2).
#
# The classification itself is right — all three affected packages take a minor
# bump, both changes being additive — so this is not a re-scope. It is now graded
# where the work actually is: PER PACKAGE, on a line naming that package. The
# 'none/patch/minor/major' label list still in instruction.md is an output
# contract, not an answer, and cannot pay: a report has to attach the bump to the
# right package to score.
#
# Gated on 'applyDecs2305' (ZERO hits in instruction.md), so the classification
# only pays behind evidence the packages were identified by reading.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPORT="$WORKSPACE/agent_output/IMPACT_REPORT.md"
GT="${TASK_DIR:-$(dirname "$(dirname "$0")")}/ground_truth.json"
MAX_REPORT_BYTES=1048576

verdict() { printf '{"score": %s, "passed": %s, "detail": "%s"}\n' "$1" "$2" "$3"; exit 0; }

if [[ ! -f "$GT" ]]; then
  verdict 0.0 false "VERIFIER_INFRA_ERROR: ground_truth.json not found at $GT"
fi
if [[ -L "$REPORT" ]]; then
  verdict 0.0 false "IMPACT_REPORT.md is a symlink, not a regular file"
fi
if [[ ! -f "$REPORT" ]]; then
  verdict 0.0 false "IMPACT_REPORT.md not found"
fi
if [[ "$(wc -c <"$REPORT")" -gt "$MAX_REPORT_BYTES" ]]; then
  verdict 0.0 false "IMPACT_REPORT.md exceeds ${MAX_REPORT_BYTES} bytes"
fi

export REPORT GT
python3 -c '
import json, os

def verdict(score, detail):
    print(json.dumps({"score": round(score, 2), "passed": score >= 0.5, "detail": detail}))
    raise SystemExit(0)

with open(os.environ["GT"]) as fh:
    gt = json.load(fh)

tokens = (gt.get("scoring_evidence") or {}).get("classify_change_impact") or []
if not tokens:
    verdict(0.0, "VERIFIER_INFRA_ERROR: no classify_change_impact evidence in ground_truth.json")

deps = gt.get("affected_dependents") or []
if not deps:
    verdict(0.0, "VERIFIER_INFRA_ERROR: no affected_dependents in ground_truth.json")

with open(os.environ["REPORT"], encoding="utf-8", errors="replace") as fh:
    text = fh.read()
lowered = text.lower()

# Fixed-string containment, never a regex.
missing = [t for t in tokens if t.lower() not in lowered]
if missing:
    verdict(0.0,
            "Not evidenced: report never names the runtime helper the decorator "
            "change actually updates (%s), so any bump it states rests on the "
            "prompt rather than on the packages it claims to have analysed"
            % (", ".join(missing),))

lines = lowered.splitlines()

# The bump must be attached to a SPECIFIC package: the bare word pays nothing,
# because instruction.md carries it in the "none/patch/minor/major" label list.
# "Attached" means within the package mention line or the few lines under it —
# a table row states both on one line, a per-package section states the name in a
# heading and the bump in a bullet below it, and both are correct. A window,
# rather than a whole-document search, keeps "minor" from being credited to a
# package named paragraphs away.
WINDOW = 5

def classified(keys, bump):
    for i, line in enumerate(lines):
        if any(k in line for k in keys):
            if bump in "\n".join(lines[i:i + WINDOW]):
                return True
    return False

results = []
for dep in deps:
    # Accept either the npm name (@babel/helpers) or the package directory
    # (babel-helpers). Reports use both, and neither is more correct.
    keys = {dep["name"].lower(), dep["path"].split("/")[-1].lower()}
    bump = dep["semver_bump"].lower()
    results.append((dep["path"].split("/")[-1], classified(keys, bump)))

got = [k for k, ok in results if ok]
score = len(got) / len(results)

if not got:
    verdict(0.0,
            "No package carries its semver bump: the report states a bump "
            "nowhere it can be attributed, which is what the prompt supplies")

verdict(score,
        "Per-package classification correct for %d/%d affected packages (%s)"
        % (len(got), len(results), ",".join(got)))
'
