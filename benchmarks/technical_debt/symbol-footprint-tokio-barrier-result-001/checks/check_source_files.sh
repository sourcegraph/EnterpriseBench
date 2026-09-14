#!/usr/bin/env bash
# check_source_files.sh -- F1 of the agent's file list against the derived key.
#
# Scored as F1, not recall: these tasks ask for an exhaustive set, so listing
# every file in the repository must not score as a perfect answer.
set -euo pipefail

export ANSWER_FILE="${WORKSPACE}/agent_output/answer.json"
export GT_FILE="${TASK_DIR}/ground_truth.json"

if [[ ! -f "$ANSWER_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 1
fi
if [[ ! -f "$GT_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No ground_truth.json found"}'
    exit 1
fi

python3 -c "
import json, os

gt = json.load(open(os.environ['GT_FILE']))
answer = json.load(open(os.environ['ANSWER_FILE']))

required = [f['path'] for f in gt.get('required_files', [])]
raw = answer.get('source_files', answer.get('files', []))
listed = []
for f in (raw if isinstance(raw, list) else []):
    if isinstance(f, dict):
        listed.append(f.get('path', f.get('file', '')))
    elif isinstance(f, str):
        listed.append(f)
listed = [p.strip().lstrip('/') for p in listed if p]

def matches(want, got):
    # Suffix match in either direction: an agent may report a workspace-prefixed
    # path, and a repo-relative key must still count it.
    return got.endswith(want) or want.endswith(got)

if not required:
    print(json.dumps({'score': 0.0, 'passed': False, 'detail': 'No required files in GT'}))
    raise SystemExit
if not listed:
    print(json.dumps({'score': 0.0, 'passed': False, 'detail': 'Agent listed no files'}))
    raise SystemExit

hit = sum(1 for w in required if any(matches(w, g) for g in listed))
extra = sum(1 for g in listed if not any(matches(w, g) for w in required))
recall = hit / len(required)
precision = hit / (hit + extra) if (hit + extra) else 0.0
f1 = 0.0 if not (recall + precision) else 2 * recall * precision / (recall + precision)
print(json.dumps({
    'score': round(f1, 4),
    'passed': f1 >= 0.5,
    'detail': f'{hit}/{len(required)} required, {extra} spurious (recall {recall:.2f}, precision {precision:.2f})',
}))
"
