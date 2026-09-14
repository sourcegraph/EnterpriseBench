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

# An empty/garbage report that never mentions parallelism at all must NOT be
# credited just because this ground truth happens to have no parallel steps —
# that is the 'always-pass on empty report' bug. When nothing is expected to
# be parallel, the plan must still contain an explicit 'Parallelization'
# section that asserts no steps can run in parallel (the task's required
# output format demands this heading); silence is not evidence of analysis.
has_parallel_heading = bool(re.search(r'^#{1,6}\s*.*parallel', answer, re.IGNORECASE | re.MULTILINE))
explicit_no_parallel = bool(re.search(
    r'no[^\n.]{0,40}parallel|not\s+parallelizable|zero\s+parallel(?:izable)?|'
    r'strictly\s+sequential|sequential[- ]only|cannot\s+(?:be\s+)?(?:run\s+)?(?:in\s+)?parallel',
    answer, re.IGNORECASE,
))

if not gt_parallel and has_parallel_heading and explicit_no_parallel:
    # No parallelism expected AND the plan explicitly says so under a
    # Parallelization heading — this is the correct, grounded outcome.
    print(json.dumps({'score': 1.0, 'passed': True, 'reason': 'Plan explicitly states no parallelizable steps'}))
elif not gt_parallel and not parallel_section:
    # Nothing expected, but the plan never addresses parallelism at all —
    # this must not silently pass (the always-pass bug on empty reports).
    print(json.dumps({'score': 0.0, 'passed': False, 'reason': 'No Parallelization section / explicit no-parallel statement found'}))
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
