#!/usr/bin/env bash
# Checkpoint 3: Verify agent correctly identifies parallelizable steps
set -euo pipefail

ANSWER="${WORKSPACE:-/workspace}/REFACTOR_PLAN.md"
GT="${TASK_DIR:-$(dirname "$(dirname "$0")")}/ground_truth.json"

if [[ ! -f "$ANSWER" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "REFACTOR_PLAN.md not found"}\n'
  exit 0
fi

if [[ ! -f "$GT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "ground_truth.json not found"}\n'
  exit 0
fi

export ANSWER_FILE="$ANSWER"
export GT_FILE="$GT"

python3 -c "
import json, os

with open(os.environ['GT_FILE']) as f:
    gt = json.load(f)
with open(os.environ['ANSWER_FILE']) as f:
    answer_text = f.read().lower()

parallel_steps = gt.get('parallelizable_steps', [])

mentions_parallel = any(term in answer_text for term in ['parallel', 'concurrent', 'simultaneous', 'independent'])

# A bare 'sequential'/'depends on' claim is generic — the prompt's own output
# format ('Dependency relationships', 'Parallelization annotations') can bait
# that. Full credit requires the claim PLUS repo-specific breaking-change
# grounding for all three repos (only found by reading the actual repos),
# mirroring ground_truth.json's breaking_changes list.
sequential_claim = any(term in answer_text for term in [
    'no parallel', 'not parallelizable', 'fully sequential', 'strictly sequential',
    'strictly linear', 'cannot be parallelized', 'zero parallel',
])
babel_grounded = any(t in answer_text for t in ['@babel/parser', 'packages/babel-parser', '@babel/traverse', '@babel/types'])
webpack_grounded = any(t in answer_text for t in ['babel-loader', 'tree-shaking', 'tree shaking'])
nextjs_grounded = any(t in answer_text for t in ['next.config.js', 'swc fallback', 'packages/next', 'next/font', 'next/image'])
breaking_change_grounded = babel_grounded and webpack_grounded and nextjs_grounded

if not parallel_steps:
    if sequential_claim and breaking_change_grounded:
        score = 1.0
        detail = 'Correctly identified fully sequential chain with repo-grounded breaking-change reasoning'
    elif sequential_claim:
        score = 0.6
        detail = 'Correctly identified sequential chain but breaking-change reasoning is not repo-grounded'
    elif mentions_parallel:
        score = 0.3
        detail = 'Mentions parallelism but chain is fully sequential'
    else:
        score = 0.4
        detail = 'Did not explicitly address parallelism'
    passed = score >= 0.5
else:
    if mentions_parallel:
        score = 0.8
        detail = 'Agent addresses parallelism opportunities'
    else:
        score = 0.2
        detail = 'Agent did not identify parallelism opportunities'
    passed = score >= 0.5

print(json.dumps({'score': round(score, 2), 'passed': passed, 'reason': detail}))
"
