#!/usr/bin/env bash
# Checkpoint 3: Verify parallelization claims are correct
set -euo pipefail

export ANSWER="${WORKSPACE:-/workspace}/REFACTOR_PLAN.md"
export GT="${TASK_DIR:-$(dirname "$(dirname "$0")")}/ground_truth.json"

if [[ ! -f "$ANSWER" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "REFACTOR_PLAN.md not found"}\n'
  exit 0
fi

if [[ ! -f "$GT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "ground_truth.json not found"}\n'
  exit 0
fi

python3 -c "
import json, os, re

with open(os.environ['GT']) as f:
    gt = json.load(f)

with open(os.environ['ANSWER']) as f:
    answer = f.read()

gt_parallel = set()
for group in gt.get('parallelizable_steps', []):
    gt_parallel.add(frozenset(group))

dep_graph = gt.get('dependency_graph', {})
graph_nodes = set(dep_graph.keys())

# Look for parallel annotations in the answer (e.g., 'parallel:', 'concurrent:', 'independent:')
parallel_section = re.findall(r'(?:parallel|concurrent|independent|simultaneously)[:\s]*([^\n]+(?:\n\s*[-*]\s*[^\n]+)*)', answer, re.IGNORECASE)

# An explicit, repo-grounded statement that nothing is parallelizable — NOT
# just silence/omission. A report that never mentions parallelism at all
# (e.g. an empty or copy-pasted-prompt report) must not earn this credit.
no_parallel_stated = bool(re.search(
    r'no\s+(?:steps?\s+)?(?:that\s+)?(?:can\s+be\s+|are\s+)?parallel|'
    r'not\s+parallelizable|'
    r'zero\s+parallel(?:iz\w*)?\s+steps|'
    r'cannot\s+(?:be\s+)?(?:run|executed|done)\s+in\s+parallel|'
    r'every\s+step\s+(?:has\s+|depends)|'
    r'each\s+step\s+(?:has\s+|depends)|'
    r'hard\s+depend\w*\s+on\s+(?:the\s+)?previous|'
    r'strictly\s+sequential',
    answer, re.IGNORECASE,
))

if not gt_parallel and no_parallel_stated:
    # No parallelism expected, and the plan explicitly says so (not just
    # silence) — verify it isn't simultaneously contradicting itself.
    raw_tokens = re.findall(r'[@\w/.-]+', ' '.join(parallel_section)) if parallel_section else []
    claimed = [t for t in raw_tokens if t in graph_nodes]
    violations = 0
    for repo in claimed:
        deps = dep_graph.get(repo, [])
        for dep in deps:
            if dep in claimed:
                violations += 1
    if violations == 0:
        print(json.dumps({'score': 1.0, 'passed': True, 'reason': 'Explicitly states no parallelizable steps, no contradictions'}))
    else:
        score = max(0.0, 1.0 - violations * 0.3)
        print(json.dumps({'score': score, 'passed': score >= 0.5, 'reason': f'{violations} contradictory parallel claims despite stating none'}))
elif not parallel_section and not gt_parallel:
    # Report never addresses parallelism at all — no credit for silence.
    print(json.dumps({'score': 0.2, 'passed': False, 'reason': 'Plan does not address parallelism at all'}))
elif not parallel_section and gt_parallel:
    # Parallelism exists but agent did not identify any
    print(json.dumps({'score': 0.2, 'passed': False, 'reason': 'Agent did not identify parallelizable steps'}))
elif parallel_section and not gt_parallel:
    # Agent claimed parallelism where none exists — check if claims are at least valid
    raw_tokens = re.findall(r'[@\w/.-]+', ' '.join(parallel_section))
    claimed = [t for t in raw_tokens if t in graph_nodes]
    violations = 0
    for repo in claimed:
        deps = dep_graph.get(repo, [])
        for dep in deps:
            if dep in claimed:
                violations += 1
    if violations == 0:
        print(json.dumps({'score': 0.8, 'passed': True, 'reason': 'Parallel claims are valid (no dependency violations)'}))
    else:
        score = max(0.0, 1.0 - violations * 0.3)
        print(json.dumps({'score': score, 'passed': score >= 0.5, 'reason': f'{violations} dependency violations in parallel claims'}))
else:
    # Both exist — check for overlap and correctness
    raw_tokens = re.findall(r'[@\w/.-]+', ' '.join(parallel_section))
    claimed_repos = set(t for t in raw_tokens if t in graph_nodes)
    gt_all_parallel = set()
    for group in gt_parallel:
        gt_all_parallel.update(group)

    overlap = claimed_repos & gt_all_parallel
    if gt_all_parallel:
        recall = len(overlap) / len(gt_all_parallel)
    else:
        recall = 1.0

    # Check that claimed parallel repos have no direct mutual deps
    violations = 0
    for repo in claimed_repos:
        deps = dep_graph.get(repo, [])
        for dep in deps:
            if dep in claimed_repos:
                violations += 1
    correctness = 1.0 if violations == 0 else max(0.0, 1.0 - violations * 0.3)

    score = recall * 0.6 + correctness * 0.4
    passed = score >= 0.5
    print(json.dumps({'score': round(score, 2), 'passed': passed, 'reason': f'Recall={recall:.2f}, correctness={correctness:.2f}'}))
"
