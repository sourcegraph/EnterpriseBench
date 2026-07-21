#!/usr/bin/env bash
# check_ownership.sh — verify agent identified correct code owners or subsystem
# Reads agent output from $WORKSPACE/agent_output/answer.json
# Checks for implementation-specific ownership terms from ground_truth.json
set -euo pipefail

export ANSWER_FILE="${WORKSPACE:-/workspace}/agent_output/answer.json"
export GT_FILE="${TASK_DIR:-/task}/ground_truth.json"

if [[ ! -f "$ANSWER_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 1
fi

if [[ ! -f "$GT_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No ground_truth.json found"}'
    exit 1
fi

python3 -c "
import json, sys, os, re

gt = json.load(open(os.environ['GT_FILE']))
answer = json.load(open(os.environ['ANSWER_FILE']))

# Implementation-specific ownership terms from ground_truth.json.
# Generic words like 'overflow' or 'upstream' are NOT sufficient —
# the agent must reference actual file names or class names.
ownership_kw = gt.get('ownership_keywords', [])

if not ownership_kw:
    print(json.dumps({'score': 0.0, 'passed': False, 'detail': 'No ownership keywords in ground truth'}))
    sys.exit(0)

# Extract agent ownership text (flexible key names)
ownership = answer.get('ownership', answer.get('owners', answer.get('subsystem', '')))
if isinstance(ownership, dict):
    ownership_text = ' '.join(str(v) for v in ownership.values()).lower()
elif isinstance(ownership, list):
    ownership_text = ' '.join(str(v) for v in ownership).lower()
else:
    ownership_text = str(ownership).lower()

# Also search the full answer text in case ownership is embedded elsewhere
full_answer_text = json.dumps(answer).lower()
search_text = ownership_text + ' ' + full_answer_text

if not ownership_text.strip():
    print(json.dumps({'score': 0.0, 'passed': False, 'detail': 'Agent provided no ownership info'}))
    sys.exit(0)

def keyword_present(kw, text):
    # Match case-insensitively; treat underscores and hyphens as interchangeable
    kw_norm = kw.lower().replace('-', '_')
    text_norm = text.replace('-', '_')
    return kw_norm in text_norm

matched_kw = [kw for kw in ownership_kw if keyword_present(kw, search_text)]
matched = len(matched_kw)

# Need at least 2 implementation-specific keyword hits to pass.
# Score is proportional to matches (full score at 4+ out of 8 keywords).
score = round(min(1.0, matched / 4.0), 2)
passed = matched >= 2

detail = (
    f'Matched {matched}/{len(ownership_kw)} implementation-specific ownership keywords: '
    f'{matched_kw}'
)
print(json.dumps({'score': score, 'passed': passed, 'detail': detail}))
"
