"""
topological_order validator — checks that a proposed repo refactor ordering
respects the dependency DAG.

Scoring:
  - Fraction of pairwise dependency constraints satisfied (partial credit)
  - Penalty for missing repos from the graph
  - Score 0.0 if the dependency graph contains cycles
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Dict, List

from eb_verify.plugins import ValidationResult


def _strip_markup(token: str) -> str:
    """Strip markdown emphasis/backticks and trailing list punctuation from a token.

    Agents write the ordering as "**grpc-go**", "`cobra`", or "etcd," — markdown
    emphasis (`*`, `_`), inline-code backticks, and trailing list punctuation
    (`,.;:-`) wrap the real repo name and otherwise survive basename-normalization,
    blocking the name match. Strip leading emphasis/backtick chars and trailing
    emphasis/backtick/punctuation chars. Internal hyphens (grpc-go) are preserved;
    only trailing ones are removed. Mirrors the sed pass in check_topo_order.sh.
    """
    return token.strip().lstrip("*_`").rstrip("*_`,.;:-")


def _basename(name: str) -> str:
    """Reduce an org/repo slug to its workspace basename.

    Ground truth keys repos by slug ("spf13/cobra") while agent output names
    them by workspace basename ("cobra", from /workspace/cobra/). Strip
    everything up to and including the last "/" so the two forms compare equal.
    """
    return name.rsplit("/", 1)[-1]


def _has_cycle(graph: Dict[str, List[str]]) -> bool:
    """Detect cycles in a dependency graph using Kahn's algorithm."""
    # Build adjacency: dep -> dependents (forward edges)
    adjacency: Dict[str, List[str]] = {node: [] for node in graph}
    for node, deps in graph.items():
        for dep in deps:
            if dep in adjacency:
                adjacency[dep].append(node)

    # Compute in-degree: count how many dependencies each node has
    in_degree: Dict[str, int] = {node: 0 for node in graph}
    for node, deps in graph.items():
        for dep in deps:
            if dep in graph:
                in_degree[node] = in_degree.get(node, 0) + 1

    queue = deque(node for node, deg in in_degree.items() if deg == 0)
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for dependent in adjacency[current]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    return visited < len(graph)


def validate_topological_order(
    proposed_order: List[str],
    dependency_graph: Dict[str, List[str]],
) -> dict:
    """Validate a proposed ordering against a dependency graph.

    Args:
        proposed_order: List of repo names in the agent's proposed refactor order.
        dependency_graph: Mapping of repo -> list of repos it depends on.
            E.g. {"B": ["A"]} means B depends on A, so A must come before B.

    Returns:
        Dict with "score" (0.0-1.0) and "detail" (explanation string).
    """
    if not proposed_order:
        return {"score": 0.0, "detail": "Empty proposal"}

    # Basename-normalize both sides so slug ("spf13/cobra") and workspace
    # basename ("cobra") match. Topological correctness is unaffected — only the
    # name-matching is made form-agnostic.
    graph = {
        _basename(node): [_basename(dep) for dep in deps]
        for node, deps in dependency_graph.items()
    }
    proposed = [
        b for repo in proposed_order if (b := _basename(_strip_markup(repo)))
    ]

    if _has_cycle(graph):
        return {"score": 0.0, "detail": "Dependency graph contains a cycle"}

    graph_nodes = set(graph.keys())

    # Filter proposed order to only nodes in the graph
    known_proposed = [r for r in proposed if r in graph_nodes]

    if not known_proposed:
        return {"score": 0.0, "detail": "No recognized repos in proposal"}

    # Build position map for the proposed order
    position = {repo: i for i, repo in enumerate(known_proposed)}

    # Count pairwise constraints: for each (node, dep) where dep is in graph,
    # dep must appear before node in the proposed order.
    total_constraints = 0
    satisfied_constraints = 0

    for node, deps in graph.items():
        for dep in deps:
            if dep not in graph_nodes:
                continue
            total_constraints += 1
            if node in position and dep in position:
                if position[dep] < position[node]:
                    satisfied_constraints += 1
                # else: violation — dep appears after node

    if total_constraints == 0:
        # No constraints to check (all nodes are independent)
        coverage = len(known_proposed) / len(graph_nodes) if graph_nodes else 1.0
        return {
            "score": min(1.0, coverage),
            "detail": "No dependency constraints to validate",
        }

    constraint_score = satisfied_constraints / total_constraints

    # Penalty for missing repos
    coverage = len(known_proposed) / len(graph_nodes) if graph_nodes else 1.0
    missing = graph_nodes - set(known_proposed)

    # Final score: constraint satisfaction is primary, coverage is a small bonus.
    # Missing repos reduce the score multiplicatively.
    score = constraint_score * coverage

    detail_parts = []
    detail_parts.append(
        f"{satisfied_constraints}/{total_constraints} dependency constraints satisfied"
    )
    detail_parts.append(f"{len(known_proposed)}/{len(graph_nodes)} repos covered")
    if missing:
        detail_parts.append(f"missing: {', '.join(sorted(missing))}")
    if score >= 0.85:
        detail_parts.insert(0, "Valid topological ordering")
    elif score > 0.0:
        detail_parts.insert(0, "Partially valid ordering")

    return {"score": round(score, 4), "detail": "; ".join(detail_parts)}


class TopologicalOrderValidator:
    """Plugin interface for topological order validation.

    For workspace-based validation, expects:
      - ordering.json: {"proposed_order": [...], "dependency_graph": {...}}
    """

    artifact_type = "topological_order"

    def validate(self, workspace: Path) -> ValidationResult:
        import json

        from eb_verify.plugins import safe_read

        candidates = list(workspace.glob("**/ordering.json"))
        if not candidates:
            return ValidationResult(
                valid=False, detail="No ordering.json found in workspace"
            )

        try:
            data = json.loads(safe_read(candidates[0], workspace))
        except (json.JSONDecodeError, ValueError) as e:
            return ValidationResult(valid=False, detail=f"ordering.json invalid: {e}")

        proposed_order = data.get("proposed_order", [])
        dependency_graph = data.get("dependency_graph", {})

        if not proposed_order or not dependency_graph:
            return ValidationResult(
                valid=False,
                detail="ordering.json must contain proposed_order and dependency_graph",
            )

        result = validate_topological_order(proposed_order, dependency_graph)
        # Threshold aligned with check_topo_order.sh (0.5) — shell scripts are authoritative
        valid = result["score"] >= 0.5
        return ValidationResult(valid=valid, detail=result["detail"])
