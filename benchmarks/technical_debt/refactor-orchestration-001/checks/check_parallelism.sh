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

# Silence must not be scored the same as a correct, explicit claim — an
# empty/garbage report mentions none of this and must not pass. Require the
# report to actually engage with parallelization, and for the no-parallelism
# case (this task's ground truth), require an explicit statement to that
# effect rather than crediting the absence of the word 'parallel'.
addresses_parallelism = bool(re.search(r'parallel|concurren|independent step|simultaneously', answer, re.IGNORECASE))
explicit_no_parallel = bool(re.search(
    r'no parallel|not parallel|cannot be (run )?in parallel|no independent steps|'
    r'strictly sequential|sequential(ly)? only|zero parallel(izable)? steps|'
    r'no steps? (can|may) (be )?(run )?in parallel',
    answer, re.IGNORECASE))

if not gt_parallel:
    if explicit_no_parallel:
        # Correctly and explicitly matches the ground truth's empty
        # parallelizable_steps array.
        print(json.dumps({'score': 1.0, 'passed': True, 'reason': 'Correctly and explicitly stated no parallelizable steps'}))
    elif not addresses_parallelism:
        print(json.dumps({'score': 0.0, 'passed': False, 'reason': 'Report does not address parallelization at all'}))
    else:
        # Mentions parallelism but without a clear 'none' statement — check
        # whether specific repos were claimed parallel and validate them.
        raw_tokens = re.findall(r'[@\w/.-]+', ' '.join(parallel_section))
        claimed = [t for t in raw_tokens if t in graph_nodes]
        violations = 0
        for repo in claimed:
            deps = dep_graph.get(repo, [])
            for dep in deps:
                if dep in claimed:
                    violations += 1
        if not claimed:
            print(json.dumps({'score': 0.3, 'passed': False, 'reason': 'Parallelization discussed but not resolved — no explicit no-parallel statement and no specific repos claimed parallel'}))
        elif violations == 0:
            print(json.dumps({'score': 0.8, 'passed': True, 'reason': 'Parallel claims are valid (no dependency violations)'}))
        else:
            score = max(0.0, 1.0 - violations * 0.3)
            print(json.dumps({'score': score, 'passed': score >= 0.5, 'reason': f'{violations} dependency violations in parallel claims'}))
elif not parallel_section and gt_parallel:
    # Parallelism exists but agent did not identify any
    print(json.dumps({'score': 0.2, 'passed': False, 'reason': 'Agent did not identify parallelizable steps'}))
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
