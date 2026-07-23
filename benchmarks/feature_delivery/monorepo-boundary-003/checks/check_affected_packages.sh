#!/usr/bin/env bash
# check_affected_packages.sh — checkpoint "identify_affected_packages"
#
# The old check grepped IMPACT_REPORT.md for 'babel.helpers|@babel/helpers',
# 'plugin.proposal.decorators|...' and 'babel.parser|@babel/parser' and scored
# found/3. instruction.md names @babel/parser, so a `cp instruction.md
# IMPACT_REPORT.md` copy scored 0.33 here for free. Those patterns are also
# unanchored regexes whose '.' matches any character — the exact trap that hides
# real leaks (EnterpriseBench-jn73.2.7.3.1.2); this check uses fixed strings.
#
# The premise is sound and was NOT re-scoped: @babel/helpers,
# @babel/plugin-proposal-decorators and @babel/parser really are the three
# affected packages, verified against the merged PRs. What was wrong is the
# ATTRIBUTION: the release notes claim (repeated by the old prompt and the old
# changed_package field) that the decorator core lands in
# @babel/helper-create-class-features-plugin. Neither PR touches it. That claim
# is what instruction.md now asks the agent to test, so refuting it is the
# finding and pure recall is not: the prompt supplies @babel/parser outright.
#
# Credit requires 'applyDecs2305' (ZERO hits in instruction.md now that the
# helper name has been removed from it) and then pays for the refutation plus the
# two packages the prompt never names.
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

tokens = (gt.get("scoring_evidence") or {}).get("identify_affected_packages") or []
if not tokens:
    verdict(0.0, "VERIFIER_INFRA_ERROR: no identify_affected_packages evidence in ground_truth.json")

with open(os.environ["REPORT"], encoding="utf-8", errors="replace") as fh:
    text = fh.read()
lowered = text.lower()

# Fixed-string containment, never a regex.
missing = [t for t in tokens if t.lower() not in lowered]
if missing:
    verdict(0.0,
            "Not evidenced: report never names the runtime helper the decorator "
            "change actually updates (%s), so nothing here shows the checkout "
            "was read rather than the prompt restated" % (", ".join(missing),))

EXCLUDED = ("not affected", "unaffected", "no change", "no changes", "not require",
            "no work", "out of scope", "not in scope", "excluded", "not impacted",
            "no update", "not need", "does not", "not touched", "untouched",
            "no part", "not part", "none", "not the", "is not")

# The refutation must be stated about the package ON ONE LINE. Scanning the whole
# document would credit a report that names it here and "not affected" in an
# unrelated sentence about something else.
claim = "helper-create-class-features-plugin"
refuted = any(claim in line and any(term in line for term in EXCLUDED)
              for line in lowered.splitlines())

# The prompt names @babel/parser outright, so it pays nothing. These two it never
# names. Accept either the npm name (@babel/helpers) or the package directory
# (babel-helpers) — reports use both, and neither is more correct.
found = [names[0] for names in (("babel-helpers", "babel/helpers"),
                                ("plugin-proposal-decorators",))
         if any(n in lowered for n in names)]

score = 0.5 * (1.0 if refuted else 0.0) + 0.5 * (len(found) / 2.0)

if not refuted and not found:
    verdict(0.0,
            "Cites the helper but neither refutes the helper-create-class-"
            "features-plugin attribution nor names the packages that actually "
            "changed")

verdict(score,
        "Attribution refuted=%s; real packages named %d/2 (%s)"
        % (refuted, len(found), ",".join(found) or "none"))
'
