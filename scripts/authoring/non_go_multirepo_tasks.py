#!/usr/bin/env python3
"""Generator for 18 non-Go multi-repo tasks (EnterpriseBench-2o0).

Takes a structured definitions list and emits per-task directories with
task.toml, instruction.md, ground_truth.json, expected_solution.json,
and three checks/*.sh scripts that follow the support_code_mapping
template established by support-mapping-dual-httpx-httpcore-001.

Usage:
    python3 scripts/authoring/non_go_multirepo_tasks.py --write
"""
from __future__ import annotations

import argparse
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Repo:
    url: str
    rev: str
    path: str
    role: str  # primary | upstream | consumer | dependency | intermediary


@dataclass(frozen=True)
class GTFile:
    path: str
    repo: str
    confidence: float


@dataclass(frozen=True)
class Checkpoint:
    name: str
    weight: float
    description: str


@dataclass(frozen=True)
class TaskDef:
    task_id: str
    suite: str
    task_type: str
    difficulty: str
    duration: int
    description: str
    prompt: str
    repos: tuple[Repo, ...]
    languages: tuple[str, ...]
    total_loc: int
    frameworks: tuple[str, ...]
    multi_repo_pattern: str
    repo_set_id: str
    mcp_benefit_rationale: str
    required_files: tuple[GTFile, ...]
    sufficient_files: tuple[GTFile, ...]
    error_chain: tuple[str, ...]
    trigger_conditions: tuple[str, ...]
    expected_answer: dict[str, str]
    checkpoints: tuple[Checkpoint, ...] = field(
        default=(
            Checkpoint("error_source", 0.40,
                      "Agent identifies the correct source files across both repos"),
            Checkpoint("error_chain", 0.30,
                      "Agent traces the full propagation chain across the boundary"),
            Checkpoint("trigger_conditions", 0.30,
                      "Agent identifies the conditions producing the symptom"),
        )
    )


# ---------------------------------------------------------------------------
# Emitters
# ---------------------------------------------------------------------------


def render_task_toml(t: TaskDef) -> str:
    lines: list[str] = []
    lines.append(f"# {t.task_type}: {t.description}")
    lines.append("")
    lines.append('difficulty_stratum = "dual_repo"')
    lines.append('mcp_suite = "eb_v1"')
    lines.append(f'repo_set_id = "{t.repo_set_id}"')
    lines.append("org_scale = true")
    lines.append('verification_modes = ["deterministic", "llm_curator"]')
    lines.append("")
    lines.append("[task]")
    lines.append(f'id = "{t.task_id}"')
    lines.append(f'suite = "{t.suite}"')
    lines.append(f'task_type = "{t.task_type}"')
    lines.append(f'difficulty = "{t.difficulty}"')
    lines.append(f"estimated_duration_minutes = {t.duration}")
    lines.append('session_type = "single"')
    lines.append(f'description = "{t.description}"')
    lines.append('prompt = """')
    lines.append(t.prompt.rstrip())
    lines.append('"""')
    lines.append("")
    for r in t.repos:
        lines.append("[[repos]]")
        lines.append(f'url = "{r.url}"')
        lines.append(f'rev = "{r.rev}"')
        lines.append(f'path = "{r.path}"')
        lines.append(f'role = "{r.role}"')
        lines.append("")
    lines.append("[metadata]")
    lines.append(f"languages = {json.dumps(list(t.languages))}")
    lines.append(f"total_loc = {t.total_loc}")
    lines.append("dependency_depth = 2")
    lines.append(f"frameworks = {json.dumps(list(t.frameworks))}")
    lines.append(f'multi_repo_pattern = "{t.multi_repo_pattern}"')
    lines.append("")
    for cp in t.checkpoints:
        lines.append("[[checkpoints]]")
        lines.append(f'name = "{cp.name}"')
        lines.append(f"weight = {cp.weight}")
        lines.append(f'verifier = "checks/check_{cp.name}.sh"')
        lines.append(f'description = "{cp.description}"')
        lines.append(f"timeout_seconds = {120 if cp.weight >= 0.30 else 60}")
        lines.append("")
    lines.append("[artifacts]")
    lines.append('required = ["answer"]')
    lines.append("optional = []")
    lines.append("")
    lines.append("[tool_access]")
    lines.append('expected_mcp_benefit = "high"')
    rationale = t.mcp_benefit_rationale.replace('"', '\\"')
    lines.append(f'mcp_benefit_rationale = "{rationale}"')
    lines.append("")
    lines.append("[ground_truth]")
    lines.append('tiers = ["deterministic"]')
    lines.append("")
    for f in t.required_files:
        lines.append("[[ground_truth.required_files]]")
        lines.append(f'path = "{f.path}"')
        lines.append(f'repo = "{f.repo}"')
        lines.append(f"confidence = {f.confidence}")
        lines.append('source = "deterministic"')
        lines.append("")
    for f in t.sufficient_files:
        lines.append("[[ground_truth.sufficient_files]]")
        lines.append(f'path = "{f.path}"')
        lines.append(f'repo = "{f.repo}"')
        lines.append(f"confidence = {f.confidence}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_instruction_md(t: TaskDef) -> str:
    return f"# {t.description}\n\n{t.prompt.strip()}\n"


def render_ground_truth_json(t: TaskDef) -> str:
    payload: dict[str, Any] = {
        "task_id": t.task_id,
        "task_type": t.task_type,
        "repos": [
            {"url": r.url, "rev": r.rev, "path": r.path} for r in t.repos
        ],
        "required_files": [
            {
                "path": f.path,
                "repo": f.repo,
                "confidence": f.confidence,
                "source": "deterministic",
            }
            for f in t.required_files
        ],
        "sufficient_files": [
            {"path": f.path, "repo": f.repo, "confidence": f.confidence}
            for f in t.sufficient_files
        ],
        "error_chain": list(t.error_chain),
        "trigger_conditions": list(t.trigger_conditions),
        "expected_answer": t.expected_answer,
    }
    return json.dumps(payload, indent=2) + "\n"


def render_expected_solution_json(t: TaskDef) -> str:
    # error_source: file-path-centric expected solution
    file_refs = ", ".join(f"{f.path}" for f in t.required_files)
    error_source_solution = (
        f"The cross-repo path crosses these required files: {file_refs}. "
        "Each file must be cited with the specific class, function, or "
        "construct relevant to the symptom (not just the file path)."
    )
    error_source_criteria = [
        f"Must identify {f.path} as part of the error source"
        for f in t.required_files
    ] + [
        "Must reference a specific class, function, or method (not just the file path)"
    ]

    # error_chain: synthesize chain
    chain_solution = "Cross-repo propagation chain: " + " -> ".join(
        f"({i + 1}) {step}" for i, step in enumerate(t.error_chain)
    ) + "."
    chain_criteria = [
        f"Must describe the step: {step[:80]}..." for step in t.error_chain[:2]
    ] + [
        "Must include all intermediate steps in the correct causal order",
        "Must cite at least one file from each repo in the propagation path",
    ]

    # trigger_conditions
    cond_solution = "Trigger conditions: " + "; ".join(
        f"({i + 1}) {c}" for i, c in enumerate(t.trigger_conditions)
    ) + "."
    cond_criteria = [
        f"Must identify the condition: {c[:80]}..."
        for c in t.trigger_conditions
    ] + [
        "Must explain the combined conditions necessary to reproduce the customer's reported behavior",
    ]

    payload = {
        "task_id": t.task_id,
        "checkpoints": {
            "error_source": {
                "expected_solution": error_source_solution,
                "evaluation_criteria": error_source_criteria,
            },
            "error_chain": {
                "expected_solution": chain_solution,
                "evaluation_criteria": chain_criteria,
            },
            "trigger_conditions": {
                "expected_solution": cond_solution,
                "evaluation_criteria": cond_criteria,
            },
        },
    }
    return json.dumps(payload, indent=2) + "\n"


CHECK_ERROR_SOURCE = dedent(
    """\
    #!/usr/bin/env bash
    # check_error_source.sh -- file discovery: agent identified correct source files across both repos
    set -euo pipefail

    export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"
    export GT_FILE="$TASK_DIR/ground_truth.json"

    exec python3 -m eb_verify.plugins.file_extraction \\
        --keys source_files,files,error_source.files \\
        --policy suffix
    """
)


CHECK_CHAIN_TEMPLATE = dedent(
    """\
    #!/usr/bin/env bash
    # check_error_chain.sh -- semantic chain matching: agent traces full propagation chain
    set -euo pipefail

    export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"
    export GT_FILE="$TASK_DIR/ground_truth.json"

    if [[ ! -f "$ANSWER_FILE" ]]; then
        echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
        exit 1
    fi

    python3 -c "
    import json, os

    answer = json.load(open(os.environ['ANSWER_FILE']))
    gt = json.load(open(os.environ['GT_FILE']))

    answer_text = json.dumps(answer).lower()
    gt_chain = gt.get('error_chain', [])

    found = 0
    for step in gt_chain:
        keywords = [w for w in step.lower().split() if len(w) > 5][:3]
        if all(kw in answer_text for kw in keywords):
            found += 1

    score = found / len(gt_chain) if gt_chain else 0.0
    detail = f'Matched {found}/{len(gt_chain)} chain steps'
    print(json.dumps({'score': round(score, 2), 'passed': score >= 0.5, 'detail': detail}))
    "
    """
)


CHECK_TRIGGER_TEMPLATE = dedent(
    """\
    #!/usr/bin/env bash
    # check_trigger_conditions.sh -- semantic conditions match
    set -euo pipefail

    export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"
    export GT_FILE="$TASK_DIR/ground_truth.json"

    if [[ ! -f "$ANSWER_FILE" ]]; then
        echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
        exit 1
    fi

    python3 -c "
    import json, os

    answer = json.load(open(os.environ['ANSWER_FILE']))
    gt = json.load(open(os.environ['GT_FILE']))

    answer_text = json.dumps(answer).lower()
    conditions = gt.get('trigger_conditions', [])

    found = 0
    for cond in conditions:
        keywords = [w for w in cond.lower().split() if len(w) > 5][:3]
        if all(kw in answer_text for kw in keywords):
            found += 1

    score = found / len(conditions) if conditions else 0.0
    detail = f'Matched {found}/{len(conditions)} trigger conditions'
    print(json.dumps({'score': round(score, 2), 'passed': score >= 0.5, 'detail': detail}))
    "
    """
)


def write_task(t: TaskDef, root: Path) -> Path:
    suite_dir = root / "benchmarks" / t.suite / t.task_id
    suite_dir.mkdir(parents=True, exist_ok=True)
    (suite_dir / "task.toml").write_text(render_task_toml(t))
    (suite_dir / "instruction.md").write_text(render_instruction_md(t))
    (suite_dir / "ground_truth.json").write_text(render_ground_truth_json(t))
    (suite_dir / "expected_solution.json").write_text(render_expected_solution_json(t))
    checks_dir = suite_dir / "checks"
    checks_dir.mkdir(exist_ok=True)
    for name, content in [
        ("check_error_source.sh", CHECK_ERROR_SOURCE),
        ("check_error_chain.sh", CHECK_CHAIN_TEMPLATE),
        ("check_trigger_conditions.sh", CHECK_TRIGGER_TEMPLATE),
    ]:
        p = checks_dir / name
        p.write_text(content)
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return suite_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true",
                        help="Write task directories under benchmarks/")
    args = parser.parse_args()

    from non_go_multirepo_tasks_data import TASKS  # noqa: E402

    if not args.write:
        for t in TASKS:
            print(t.task_id, "->", t.suite)
        return 0

    for t in TASKS:
        out = write_task(t, ROOT)
        print(f"wrote: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
