#!/usr/bin/env bash
# Checkpoint 2: Verify impact classification
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/babel/IMPACT_REPORT.md"
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "IMPACT_REPORT.md not found"}\n'
  exit 0
fi

python3 -c "
import re, json, sys

with open('${REPORT}') as f:
    content = f.read().lower()

# Three required signals, each anchored to a repo artifact from the ground
# truth (none of these strings appear in instruction.md — the prompt only
# names the source-of-truth file and the generic major/minor/patch/none
# options, so pasting the prompt back cannot satisfy any of these):
#   1. Parser-side evidence: the hand-written declaration file that has to
#      drop the field, tied to its patch-level classification.
#   2. Generator-side evidence: the printer file, or the 'unreachable output'
#      reasoning that justifies why dropping it is patch-level, not a break.
#   3. The semver word 'patch' co-located with a concrete repo artifact,
#      not just recited from the multiple-choice list in the prompt.
ARTIFACT_RE = r'types\.d\.ts|generators/typescript\.ts|\bunreachable\b|ast-types/generated|builders/generated'

signals = {
    'parser_evidence':    bool(re.search(r'types\.d\.ts', content)),
    'generator_evidence': bool(re.search(r'generators/typescript\.ts|\bunreachable\b', content)),
    'semver_grounded':    bool(re.search(r'\bpatch\b', content)) and bool(re.search(ARTIFACT_RE, content)),
}

matched = sum(signals.values())

# Score is proportional to how many signals are present (out of 3)
score = round(matched / 3.0, 2)

# Must have at least 2 of the 3 signals to pass
passed = matched >= 2

reason_parts = [k for k, v in signals.items() if v]
missing_parts = [k for k, v in signals.items() if not v]

reason = (
    f'Matched {matched}/3 required signals '
    f'(found: {reason_parts}; missing: {missing_parts})'
)

print(json.dumps({'score': score, 'passed': passed, 'reason': reason}))
"
