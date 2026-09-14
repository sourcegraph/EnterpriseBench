#!/usr/bin/env python3
"""Audit instruction.md quality, GT leakage, realism, completeness, and checkpoint alignment.

For each instruction.md:
1. GT leakage: Does it mention specific fix PR numbers/URLs, exact GT file paths, or direct answers?
2. Realism: Is it written as a realistic enterprise scenario?
3. Completeness: Does it mention workspace location and expected output format?
4. Checkpoint alignment: Does instruction ask for everything checkpoints verify?

Rating: PASS (no issues), WARN (minor), FAIL (GT leak or major problem)
"""

import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.tasks import find_task_dirs

try:
    import tomllib
except ImportError:
    import tomllib  # Python 3.11+

BENCHMARKS_DIR = Path(__file__).resolve().parent.parent / "benchmarks"
RESULTS_DIR = Path(__file__).resolve().parent.parent / "results" / "analysis"


def load_task_toml(task_dir):
    toml_path = task_dir / "task.toml"
    if not toml_path.exists():
        return None
    with open(toml_path, "rb") as f:
        return tomllib.load(f)


def load_instruction(task_dir):
    path = task_dir / "instruction.md"
    if not path.exists():
        return ""
    return path.read_text()


def load_ground_truth(task_dir):
    path = task_dir / "ground_truth.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def get_gt_file_paths(task_toml, gt_json):
    paths = set()
    for source in [task_toml, gt_json]:
        if not source:
            continue
        gt = source if isinstance(source, dict) and "required_files" in source else source.get("ground_truth", {})
        for key in ("required_files", "sufficient_files"):
            for f in gt.get(key, []):
                if isinstance(f, str):
                    paths.add(f)
                elif isinstance(f, dict):
                    p = f.get("path", "")
                    if p:
                        paths.add(p)
    return paths


def get_pr_refs(gt_json):
    refs = []
    if not gt_json:
        return refs
    for key in ("fix_pr", "candidate_ref"):
        val = gt_json.get(key, "")
        if val:
            refs.append(val)
    return refs


def get_checkpoints(task_toml):
    if not task_toml:
        return []
    return task_toml.get("checkpoints", [])


def check_gt_leakage(instruction, gt_paths, pr_refs, gt_json):
    """Check if instruction leaks ground truth information.

    Path matching here is exact-full-path only. Basename/stem-tier disclosure
    ("realms.py", "the avatar helper") is invisible to this check by design --
    naming files can be legitimate scenario setup for execution tasks. Tasks
    scored on retrieval are held to the stricter tiered gate in
    scripts/audit_disclosure.py (CI-enforced via configs/retrieval_slice.json).
    """
    issues = []

    # Check for PR numbers/URLs
    for ref in pr_refs:
        # Check for full URL
        if ref in instruction:
            issues.append(f"FAIL: Instruction contains GT PR reference: {ref}")
        # Check for PR number (e.g., #12345)
        pr_match = re.search(r'#(\d+)', ref)
        if pr_match:
            pr_num = pr_match.group(1)
            # Look for the PR number in instruction
            if re.search(rf'#\s*{pr_num}\b', instruction) or re.search(rf'pull/\s*{pr_num}', instruction):
                issues.append(f"FAIL: Instruction mentions GT PR number #{pr_num}")

    # Check for exact GT file paths
    generic_paths = {"go.mod", "go.sum", "package.json", "requirements.txt", "setup.py",
                     "setup.cfg", "pom.xml", "build.gradle", "Cargo.toml"}
    leaked_paths = []
    for path in gt_paths:
        if path in instruction:
            if path in generic_paths:
                continue
            leaked_paths.append(path)

    # Severity depends on how many non-generic GT paths are leaked
    if len(leaked_paths) >= 3:
        for path in leaked_paths:
            issues.append(f"FAIL: Instruction reveals GT file path: {path}")
        issues.append(f"FAIL: Instruction leaks {len(leaked_paths)} ground-truth file paths — gives away the answer")
    else:
        for path in leaked_paths:
            issues.append(f"WARN: Instruction mentions GT file path: {path}")

    # Check for direct answers from GT
    if gt_json:
        # Check error_chain items
        for chain_item in gt_json.get("error_chain", []):
            # If a specific function or file from the chain is mentioned verbatim
            if len(chain_item) > 30 and chain_item in instruction:
                issues.append(f"WARN: Instruction contains GT error chain detail: {chain_item[:60]}...")

        # Check trigger_conditions
        for cond in gt_json.get("trigger_conditions", []):
            if len(cond) > 30 and cond in instruction:
                issues.append(f"WARN: Instruction contains GT trigger condition: {cond[:60]}...")

    return issues


def check_realism(instruction, task_id):
    """Check if instruction reads as a realistic enterprise scenario."""
    issues = []

    # Positive indicators of realism
    realistic_patterns = [
        r'(?i)(support ticket|customer report|incident|we noticed|our team|we\'re running|hi team|hi,)',
        r'(?i)(priority:\s*(high|medium|low|critical))',
        r'(?i)(submitted by|reported by|from:)',
        r'(?i)(product:|service:|component:)',
    ]

    # Negative indicators (test-prompt-like)
    test_prompt_patterns = [
        r'^(?:Find|List|Identify|Determine|Analyze)\s',  # starts with imperative command
    ]

    has_realistic = any(re.search(p, instruction) for p in realistic_patterns)
    starts_imperative = any(re.search(p, instruction.strip(), re.MULTILINE) for p in test_prompt_patterns)

    # Check if it has some narrative framing
    has_narrative = bool(re.search(r'(?i)(we |our |I |the team |my )', instruction))

    if not has_realistic and not has_narrative:
        issues.append("WARN: Instruction lacks enterprise scenario framing (no ticket/report/narrative)")

    # Check for markdown heading (structured format)
    if not instruction.strip().startswith("#"):
        issues.append("WARN: Instruction does not start with markdown heading")

    return issues


def check_completeness(instruction, task_toml):
    """Check if instruction provides enough context."""
    issues = []

    # Check workspace/repo mention
    workspace_patterns = [
        r'/workspace/',
        r'available (under|at|in)',
        r'repository.*cloned',
        r'codebase.*at',
    ]
    has_workspace = any(re.search(p, instruction) for p in workspace_patterns)
    if not has_workspace:
        issues.append("WARN: Instruction does not mention workspace/repository location")

    # Check output format specification
    output_patterns = [
        r'(?i)(format|output|produce|deliver|write|create|generate).*(?:report|patch|answer|config|runbook|script|assessment|JSON|YAML|markdown)',
        r'(?i)(we need|please provide|expected output|your answer|respond with)',
        r'(?i)(answer\.(?:md|json|txt|yaml))',
    ]
    has_output_format = any(re.search(p, instruction) for p in output_patterns)

    # Check if at least the questions are clear
    has_questions = bool(re.search(r'\d+\.\s+', instruction))  # numbered list

    if not has_output_format and not has_questions:
        issues.append("WARN: Instruction does not specify expected output format or clear deliverables")

    return issues


def check_checkpoint_alignment(instruction, checkpoints):
    """Check if instruction asks for what checkpoints verify."""
    issues = []
    if not checkpoints:
        return issues

    for cp in checkpoints:
        cp_name = cp.get("name", "")
        cp_desc = cp.get("description", "")

        # Extract key concepts from checkpoint description
        # These should be somehow mentioned or implied in the instruction
        key_concepts = []
        if "error" in cp_name.lower() and "source" in cp_name.lower():
            key_concepts.append(r'(?i)(where.*error|source.*error|which.*file|what.*generates)')
        if "error" in cp_name.lower() and "chain" in cp_name.lower():
            key_concepts.append(r'(?i)(chain|propagat|trace|flow|path)')
        if "trigger" in cp_name.lower():
            key_concepts.append(r'(?i)(trigger|condition|when|cause)')
        if "impact" in cp_name.lower():
            key_concepts.append(r'(?i)(impact|affect|consequence)')
        if "boundary" in cp_name.lower():
            key_concepts.append(r'(?i)(boundary|module|package|scope)')
        if "topo" in cp_name.lower() or "order" in cp_name.lower():
            key_concepts.append(r'(?i)(order|sequence|depend)')
        if "dead" in cp_name.lower() or "code" in cp_name.lower():
            key_concepts.append(r'(?i)(dead|unused|unreachable|remove)')
        if "severity" in cp_name.lower():
            key_concepts.append(r'(?i)(severity|priority|critical)')
        if "drift" in cp_name.lower() or "config" in cp_name.lower():
            key_concepts.append(r'(?i)(config|drift|discrepanc|differ)')
        if "schema" in cp_name.lower() or "migration" in cp_name.lower():
            key_concepts.append(r'(?i)(schema|migration|database|table|column)')
        if "contract" in cp_name.lower() or "api" in cp_name.lower():
            key_concepts.append(r'(?i)(api|contract|interface|endpoint)')

        for concept_pattern in key_concepts:
            if not re.search(concept_pattern, instruction):
                issues.append(f"WARN: Checkpoint '{cp_name}' concept may not be addressed in instruction")
                break

    return issues


def rate_task(issues):
    """Rate a task based on its issues."""
    if any("FAIL" in i for i in issues):
        return "FAIL"
    if any("WARN" in i for i in issues):
        return "WARN"
    return "PASS"


def main():
    task_dirs = find_task_dirs()
    print(f"Auditing {len(task_dirs)} tasks...")

    results = []
    for td in task_dirs:
        task_id = f"{td.parent.name}/{td.name}"
        toml_data = load_task_toml(td)
        gt_data = load_ground_truth(td)
        instruction = load_instruction(td)

        if not instruction:
            results.append({
                "task_id": task_id,
                "rating": "FAIL",
                "issues": ["FAIL: No instruction.md found"],
            })
            continue

        gt_paths = get_gt_file_paths(toml_data, gt_data)
        pr_refs = get_pr_refs(gt_data)
        checkpoints = get_checkpoints(toml_data)

        issues = []
        issues.extend(check_gt_leakage(instruction, gt_paths, pr_refs, gt_data))
        issues.extend(check_realism(instruction, task_id))
        issues.extend(check_completeness(instruction, toml_data))
        issues.extend(check_checkpoint_alignment(instruction, checkpoints))

        rating = rate_task(issues)
        results.append({
            "task_id": task_id,
            "rating": rating,
            "issues": issues,
        })

    # Generate report
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = RESULTS_DIR / "instruction_quality.md"

    lines = [
        "# Instruction Quality Audit Report",
        "",
        f"**Date:** 2026-03-29",
        f"**Tasks audited:** {len(results)}",
        "",
    ]

    # Summary
    by_rating = defaultdict(int)
    for r in results:
        by_rating[r["rating"]] += 1

    lines.append("## Summary")
    lines.append("")
    lines.append("| Rating | Count |")
    lines.append("|--------|-------|")
    for rating in ["PASS", "WARN", "FAIL"]:
        lines.append(f"| {rating} | {by_rating.get(rating, 0)} |")
    lines.append("")

    # Issue frequency
    issue_counts = defaultdict(int)
    for r in results:
        for issue in r["issues"]:
            # Normalize issue to category
            if "GT PR reference" in issue or "GT PR number" in issue:
                cat = "GT leakage: PR reference"
            elif "reveals GT file path" in issue:
                cat = "GT leakage: FAIL-level file path leak (3+ paths)"
            elif "leaks" in issue and "ground-truth" in issue:
                continue  # summary line, skip
            elif "GT file path" in issue:
                cat = "GT leakage: file path mentioned (1-2 paths)"
            elif "GT error chain" in issue:
                cat = "GT leakage: error chain detail"
            elif "GT trigger" in issue:
                cat = "GT leakage: trigger condition"
            elif "scenario framing" in issue:
                cat = "Realism: missing enterprise framing"
            elif "markdown heading" in issue:
                cat = "Realism: no markdown heading"
            elif "workspace" in issue:
                cat = "Completeness: no workspace location"
            elif "output format" in issue:
                cat = "Completeness: no output format"
            elif "Checkpoint" in issue:
                cat = "Alignment: checkpoint concept missing"
            else:
                cat = issue[:50]
            issue_counts[cat] += 1

    lines.append("## Issue Frequency")
    lines.append("")
    lines.append("| Issue Category | Count |")
    lines.append("|---------------|-------|")
    for cat, count in sorted(issue_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| {cat} | {count} |")
    lines.append("")

    # Detailed results by rating
    for rating in ["FAIL", "WARN", "PASS"]:
        rated = [r for r in results if r["rating"] == rating]
        if not rated:
            continue

        lines.append(f"## {rating} Tasks ({len(rated)})")
        lines.append("")

        for r in rated:
            lines.append(f"### {r['task_id']}")
            lines.append("")
            if r["issues"]:
                for issue in r["issues"]:
                    lines.append(f"- {issue}")
            else:
                lines.append("- No issues found")
            lines.append("")

    report_content = "\n".join(lines)
    report_path.write_text(report_content)
    print(f"Report written to: {report_path}")
    print(f"\nPASS: {by_rating.get('PASS', 0)}, WARN: {by_rating.get('WARN', 0)}, FAIL: {by_rating.get('FAIL', 0)}")

    # Print FAIL tasks
    for r in results:
        if r["rating"] == "FAIL":
            print(f"  FAIL: {r['task_id']}")
            for issue in r["issues"]:
                if "FAIL" in issue:
                    print(f"    {issue}")


if __name__ == "__main__":
    main()
