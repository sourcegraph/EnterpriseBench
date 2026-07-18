"""
topological_order validator — checks that a proposed repo refactor ordering
respects the dependency DAG.

Scoring:
  - Fraction of pairwise dependency constraints satisfied (partial credit)
  - Penalty for missing repos from the graph
  - Score 0.0 if the dependency graph contains cycles
"""

from __future__ import annotations

import re
from collections import deque
from pathlib import Path
from typing import Dict, Iterable, List

from eb_verify.artifact_io import safe_read, select_unique
from eb_verify.plugins import ValidationResult

# Agents write the required "numbered list of repos in order" as either:
#   - "### Step N — <description>" headings, with the actual repo named on a
#     separate "**Repo:** `<repo>`" line within that step's block, or
#   - plain "Step N — <repo> ..." lines (e.g. inside a fenced code block), or
#   - "### Phase A — <repo>: <description>" headings (lettered, not numbered),
#     with the repo token sometimes bolded as "**Step N** — ..." underneath, or
#   - a flat markdown numbered list "N. <repo> ..." / "N. **<repo>** ...".
# All shapes appear in real completed runs. Extraction below handles them
# instead of assuming one flat shape.
_HEADING_RE = re.compile(r"^(#{1,6})\s*(.+)$")
_ANCHOR_RE = re.compile(r"order|topolog", re.IGNORECASE)
_LEADING_HASH_RE = re.compile(r"^#{1,6}\s*")
_STEP_LINE_RE = re.compile(
    r"^(?:Step|Phase)\s+([0-9]+|[A-Za-z])\s*[—\-:]\s*(.+)$", re.IGNORECASE
)
_NUMBERED_LINE_RE = re.compile(r"^(\d+)[.)]\s+(.+)$")
_REPO_FIELD_RE = re.compile(r"^\*{0,2}repo\*{0,2}:\*{0,2}\s*(.+)$", re.IGNORECASE)
_BACKTICK_TOKEN_RE = re.compile(r"^\*{0,2}`([^`]+)`")
_BOLD_TOKEN_RE = re.compile(r"^\*\*([^*]+)\*\*")
_PLAIN_TOKEN_RE = re.compile(r"^([^\s(]+)")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")
_MIN_SUBSTRING_MATCH_LEN = 5


def _clean_token(text: str) -> str:
    """Pull the repo/module identifier out of a line fragment, stripping markdown."""
    text = text.strip()
    m = _BACKTICK_TOKEN_RE.match(text)
    if m:
        return m.group(1).strip()
    m = _BOLD_TOKEN_RE.match(text)
    if m:
        return m.group(1).strip()
    m = _PLAIN_TOKEN_RE.match(text)
    if m:
        return m.group(1).strip("`*,:;")
    return text


def extract_proposed_order(plan_text: str) -> List[str]:
    """Extract the ordered list of repo/module tokens an agent proposed.

    Scans the section whose heading mentions "order" or "topological" (the
    task instructions always ask for this), then reads step ordering from
    either "Step N" markers (heading or plain line, preferring a "**Repo:**"
    field inside that step's block if present) or a flat "N." numbered list.
    Returns raw tokens as written — callers resolve them against a
    dependency graph's actual keys via `resolve_tokens_to_graph`.
    """
    lines = plan_text.splitlines()

    anchor_idx = None
    anchor_level = None
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line.strip())
        if m and _ANCHOR_RE.search(m.group(2)):
            anchor_idx = i
            anchor_level = len(m.group(1))
            break

    if anchor_idx is not None:
        end = len(lines)
        for j in range(anchor_idx + 1, len(lines)):
            hm = _HEADING_RE.match(lines[j].strip())
            if hm and len(hm.group(1)) <= anchor_level:
                end = j
                break
        section = lines[anchor_idx + 1 : end]
    else:
        section = lines

    step_matches = []  # (index_in_section, rest_text)
    for idx, line in enumerate(section):
        destripped = _LEADING_HASH_RE.sub("", line.strip()).replace("**", "")
        m = _STEP_LINE_RE.match(destripped)
        if m:
            step_matches.append((idx, m.group(1), m.group(2)))

    tokens: List[str] = []
    if step_matches:
        for k, (idx, _step_id, rest) in enumerate(step_matches):
            block_end = step_matches[k + 1][0] if k + 1 < len(step_matches) else len(section)
            token_source = rest
            for block_line in section[idx + 1 : block_end]:
                rm = _REPO_FIELD_RE.match(block_line.strip())
                if rm:
                    token_source = rm.group(1)
                    break
            tokens.append(_clean_token(token_source))
    else:
        for line in section:
            destripped = _LEADING_HASH_RE.sub("", line.strip())
            m = _NUMBERED_LINE_RE.match(destripped)
            if m:
                tokens.append(_clean_token(m.group(2)))

    return tokens


def _last_segment(name: str) -> str:
    return name.rstrip("/").split("/")[-1].strip().lower()


def _normalize(text: str) -> str:
    """Strip everything but lowercase letters/digits, for punctuation-insensitive matching."""
    return _NON_ALNUM_RE.sub("", text.lower())


def resolve_tokens_to_graph(tokens: Iterable[str], graph_nodes: Iterable[str]) -> List[str]:
    """Map raw extracted tokens onto actual dependency-graph node keys.

    Step text names repos narratively ("go.etcd.io/etcd", "kubernetes"
    monorepo module paths like "k8s.io/apiserver") rather than the literal
    "org/repo" graph keys, so this matches in order of decreasing strictness:
    exact string, case-insensitive last-path-segment ("etcd-io/etcd" ~
    "go.etcd.io/etcd"), punctuation-stripped last-path-segment ("vercel/
    next.js" ~ "nextjs"), and finally substring containment between
    normalized last segments ("build-infra" ~ "Build Infrastructure") —
    guarded by a minimum length so short generic tokens ("api", "go") don't
    match everything. Deduplicates to first occurrence per resolved key and
    drops unmatched tokens (e.g. narrative sub-headings that aren't repo
    identifiers).
    """
    nodes = list(graph_nodes)
    node_set = set(nodes)
    seg_to_key: Dict[str, str] = {}
    seg_norm_to_key: Dict[str, str] = {}
    for key in nodes:
        seg = _last_segment(key)
        seg_to_key.setdefault(seg, key)
        seg_norm_to_key.setdefault(_normalize(seg), key)

    resolved: List[str] = []
    seen = set()

    for token in tokens:
        token = token.strip()
        if not token:
            continue

        match = token if token in node_set else seg_to_key.get(_last_segment(token))

        if match is None:
            token_seg = _last_segment(token)
            token_norm = _normalize(token_seg)
            match = seg_norm_to_key.get(token_norm)

        if match is None and len(token_norm) >= _MIN_SUBSTRING_MATCH_LEN:
            match = next(
                (
                    key
                    for key in nodes
                    if len(_normalize(_last_segment(key))) >= _MIN_SUBSTRING_MATCH_LEN
                    and (
                        token_norm in _normalize(_last_segment(key))
                        or _normalize(_last_segment(key)) in token_norm
                    )
                ),
                None,
            )

        if match and match not in seen:
            seen.add(match)
            resolved.append(match)

    return resolved


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

    if _has_cycle(dependency_graph):
        return {"score": 0.0, "detail": "Dependency graph contains a cycle"}

    graph_nodes = set(dependency_graph.keys())

    # Filter proposed order to only nodes in the graph
    known_proposed = [r for r in proposed_order if r in graph_nodes]

    if not known_proposed:
        return {"score": 0.0, "detail": "No recognized repos in proposal"}

    # Build position map for the proposed order
    position = {repo: i for i, repo in enumerate(known_proposed)}

    # Count pairwise constraints: for each (node, dep) where dep is in graph,
    # dep must appear before node in the proposed order.
    total_constraints = 0
    satisfied_constraints = 0

    for node, deps in dependency_graph.items():
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


def validate_refactor_plan_markdown(
    plan_text: str,
    dependency_graph: Dict[str, List[str]],
) -> dict:
    """Score a REFACTOR_PLAN.md-style markdown document against a dependency graph.

    Single entry point used by checks/check_topo_order.sh across all
    refactor-orchestration tasks: extracts the agent's proposed repo
    ordering from the markdown (whatever shape it's written in) and
    validates it, so the extraction logic lives in one tested place
    instead of being duplicated as a shell regex per task.
    """
    tokens = extract_proposed_order(plan_text)
    if not tokens:
        return {"score": 0.0, "detail": "No numbered ordering found in plan"}

    proposed_order = resolve_tokens_to_graph(tokens, dependency_graph.keys())
    return validate_topological_order(proposed_order, dependency_graph)


class TopologicalOrderValidator:
    """Plugin interface for topological order validation.

    For workspace-based validation, expects:
      - ordering.json: {"proposed_order": [...], "dependency_graph": {...}}
    """

    artifact_type = "topological_order"

    def validate(self, workspace: Path) -> ValidationResult:
        import json

        # Decoy-shadow-safe selection: reject >1 candidate as ambiguous.
        path, sel_err = select_unique(workspace, ["ordering.json"])
        if sel_err is not None:
            return ValidationResult(valid=False, detail=sel_err)
        if path is None:
            return ValidationResult(
                valid=False, detail="No ordering.json found in workspace"
            )

        try:
            data = json.loads(safe_read(path, workspace))
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
