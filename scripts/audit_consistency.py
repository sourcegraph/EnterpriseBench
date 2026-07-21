#!/usr/bin/env python3
"""
audit_consistency.py — Cross-task consistency audit for EnterpriseBench.

Checks ALL active tasks for:
1. Check scripts use os.environ (not '$VAR' in python3 -c blocks)
2. Check scripts have set -euo pipefail or set -uo pipefail
3. Check scripts produce JSON with 'score' and 'passed' keys
4. Checkpoint weights sum to 1.0 (±0.01)
5. Check scripts are chmod +x
6. Artifact types in task.toml match what check scripts read
"""

import argparse
import os
import re
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.tasks import find_task_dirs

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

# Repo root derived from this script's own location, not a hardcoded absolute
# path — a hardcoded path silently no-ops (FileNotFoundError, or worse, a
# stale sibling checkout) on any machine/CI runner/worktree other than the
# one it was written on.
REPO_ROOT = Path(__file__).resolve().parent.parent
BENCH_DIR = REPO_ROOT / "benchmarks"
ARCHIVED = {"_archived"}

# Collect all active task directories (skip _archived, examples, mined)
SKIP_DIRS = {"_archived", "mined"}


def find_active_tasks() -> list[Path]:
    """Find all active task directories with task.toml files."""
    return find_task_dirs(BENCH_DIR)


def parse_toml(path):
    """Parse a TOML file."""
    if tomllib is not None:
        with open(path, "rb") as f:
            return tomllib.load(f)
    # Fallback: basic parsing
    import subprocess
    result = subprocess.run(
        ["python3", "-c", f"""
import tomllib
import json
with open("{path}", "rb") as f:
    print(json.dumps(tomllib.load(f)))
"""],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        import json
        return json.loads(result.stdout)
    return None


def check_env_usage(script_path):
    """Check that python3 -c blocks use os.environ, not '$VAR' shell expansion."""
    violations = []
    content = script_path.read_text()

    # Look for python3 -c blocks that use shell variable expansion like open('$
    # This pattern catches: open('$VAR'), open("$VAR"), open(f'$VAR')
    if re.search(r"python3\s+-c\s+['\"].*open\(['\"]?\$", content):
        violations.append(f"Uses shell var expansion in python3 -c open() call")

    # Also check for inline python blocks with $VARIABLE (not os.environ)
    # Match python3 -c "..." blocks that contain $VARIABLE patterns
    # But allow heredoc-style python blocks that use os.environ
    python3_c_blocks = re.findall(
        r'python3\s+-c\s+["\'](.+?)["\']', content, re.DOTALL
    )
    for block in python3_c_blocks:
        if "$" in block and "os.environ" not in block:
            # Check if $VAR is used for file paths in the python block
            dollar_vars = re.findall(r'\$\w+', block)
            if dollar_vars:
                violations.append(
                    f"python3 -c block uses shell variables {dollar_vars} instead of os.environ"
                )

    return violations


def check_set_flags(script_path):
    """Check for set -euo pipefail or set -uo pipefail."""
    content = script_path.read_text()
    if re.search(r'set\s+-[eu]*o\s+pipefail', content):
        return []
    return ["Missing 'set -euo pipefail' or 'set -uo pipefail'"]


# A check that delegates scoring to a shared eb_verify module (e.g.
# `exec python3 -m eb_verify.scorers.file_extraction`) emits no JSON of its
# own — the library module prints the {"score", "passed", "detail"} verdict.
# The wrapper's text therefore legitimately contains none of those keys.
_EB_VERIFY_DELEGATION_RE = re.compile(r"python3?\s+-m\s+eb_verify\.")


def check_json_output(script_path):
    """Check that scripts produce JSON with 'score' and 'passed' keys.

    Delegation to an eb_verify module counts as JSON-emitting: the delegated
    scorer owns the verdict (its output contract is pinned by the eb_verify
    test suite), so the shell wrapper is not required to spell the keys.
    """
    content = script_path.read_text()
    violations = []

    if _EB_VERIFY_DELEGATION_RE.search(content):
        return violations

    # Look for output patterns — printf/echo with JSON
    has_score = bool(re.search(r'["\']score["\']', content))
    has_passed = bool(re.search(r'["\']passed["\']', content))

    # Some scripts use 'detail' instead of 'passed' — check for that
    has_detail = bool(re.search(r'["\']detail["\']', content))

    if not has_score:
        violations.append("Missing 'score' key in JSON output")
    if not has_passed and not has_detail:
        violations.append("Missing 'passed' (or 'detail') key in JSON output")

    return violations


def check_weights(task_dir, toml_data):
    """Check that checkpoint weights sum to 1.0 (±0.01)."""
    checkpoints = toml_data.get("checkpoints", [])
    if not checkpoints:
        return ["No checkpoints defined"]

    total = sum(cp.get("weight", 0) for cp in checkpoints)
    if abs(total - 1.0) > 0.01:
        return [f"Checkpoint weights sum to {total:.4f} (expected 1.0 ±0.01)"]
    return []


def check_executable(script_path):
    """Check that script is chmod +x."""
    mode = script_path.stat().st_mode
    if not (mode & stat.S_IXUSR):
        return [f"Not executable (chmod +x needed)"]
    return []


def check_artifact_match(task_dir, toml_data):
    """Check that artifact types match what check scripts read."""
    violations = []
    artifacts = toml_data.get("artifacts", {})
    required_artifacts = set(artifacts.get("required", []))
    optional_artifacts = set(artifacts.get("optional", []))
    all_artifacts = required_artifacts | optional_artifacts

    checks_dir = task_dir / "checks"
    if not checks_dir.exists():
        return ["No checks/ directory"]

    # Determine what files check scripts actually read
    scripts_read_files = set()
    for script in checks_dir.glob("check_*.sh"):
        content = script.read_text()

        # Look for answer.json references
        if "answer.json" in content:
            scripts_read_files.add("answer")
        if "dead_code_report.json" in content:
            scripts_read_files.add("answer")  # dead_code uses answer artifact type
        if "INCIDENT_REPORT" in content:
            scripts_read_files.add("incident_report")
        if "DRIFT_REPORT" in content:
            scripts_read_files.add("answer")  # config_drift uses answer
        if "BLAST_RADIUS" in content:
            scripts_read_files.add("answer")  # dep_traversal uses answer
        if "IMPACT_REPORT" in content:
            scripts_read_files.add("answer")
        if "SCHEMA_IMPACT" in content:
            scripts_read_files.add("answer")
        if "REFACTOR_PLAN" in content:
            scripts_read_files.add("answer")
        if "review.json" in content:
            scripts_read_files.add("review")
        if "security_assessment" in content.lower():
            scripts_read_files.add("security_assessment")
        if "ordering.json" in content:
            scripts_read_files.add("topological_order")

    # Check: if scripts read answer.json but task doesn't list "answer" in artifacts
    if "answer" in scripts_read_files and "answer" not in all_artifacts:
        # Check if any other artifact covers it
        if not all_artifacts & scripts_read_files:
            violations.append(
                f"Scripts read 'answer' files but artifacts are required={required_artifacts}, optional={optional_artifacts}"
            )

    if "incident_report" in scripts_read_files and "incident_report" not in all_artifacts:
        violations.append(
            f"Scripts read incident report but 'incident_report' not in required artifacts"
        )

    return violations


def write_report(
    out_path: Path,
    tasks_checked: int,
    total_scripts: int,
    all_violations: dict,
    summary: dict,
) -> None:
    """Write the markdown consistency-audit report to an explicit path."""
    import datetime

    out_path.parent.mkdir(parents=True, exist_ok=True)

    total_violations = sum(summary.values())
    tasks_with_issues = len(all_violations)

    with open(out_path, "w") as f:
        f.write("# Cross-Task Consistency Audit\n\n")
        f.write(
            f"Generated: {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
        )

        f.write("## Summary\n\n")
        f.write(f"| Metric | Count |\n")
        f.write(f"|--------|-------|\n")
        f.write(f"| Tasks scanned | {tasks_checked} |\n")
        f.write(f"| Check scripts scanned | {total_scripts} |\n")
        f.write(f"| Tasks with violations | {tasks_with_issues} |\n")
        f.write(f"| Total violations | {total_violations} |\n\n")

        f.write("## Violations by Category\n\n")
        f.write("| Category | Count | Description |\n")
        f.write("|----------|-------|-------------|\n")
        f.write(f"| env_usage | {summary['env_usage']} | Shell var expansion in python3 -c blocks |\n")
        f.write(f"| set_flags | {summary['set_flags']} | Missing set -euo pipefail |\n")
        f.write(f"| json_output | {summary['json_output']} | Missing score/passed keys in output |\n")
        f.write(f"| weights | {summary['weights']} | Checkpoint weights not summing to 1.0 |\n")
        f.write(f"| executable | {summary['executable']} | Check scripts not chmod +x |\n")
        f.write(f"| artifact_match | {summary['artifact_match']} | Artifact type mismatch |\n\n")

        if all_violations:
            f.write("## Detailed Violations\n\n")
            for task_name, violations in sorted(all_violations.items()):
                f.write(f"### {task_name}\n\n")
                for category, detail in violations:
                    f.write(f"- **[{category}]** {detail}\n")
                f.write("\n")
        else:
            f.write("## Detailed Violations\n\n")
            f.write("No violations found.\n\n")

        f.write("## Checks Performed\n\n")
        f.write("1. **env_usage**: All python3 -c blocks use `os.environ` (not `'$VAR'` shell expansion)\n")
        f.write("2. **set_flags**: All scripts have `set -euo pipefail` or `set -uo pipefail`\n")
        f.write("3. **json_output**: All scripts produce JSON with `score` and `passed` keys\n")
        f.write("4. **weights**: Checkpoint weights sum to 1.0 (±0.01)\n")
        f.write("5. **executable**: All check scripts are chmod +x\n")
        f.write("6. **artifact_match**: Artifact types in task.toml match what check scripts read\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write the markdown report to this path (opt-in; default: no file, "
        "stdout summary only)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None):
    args = parse_args(argv)
    tasks = find_active_tasks()
    print(f"Found {len(tasks)} active tasks")

    all_violations = {}
    summary = {
        "env_usage": 0,
        "set_flags": 0,
        "json_output": 0,
        "weights": 0,
        "executable": 0,
        "artifact_match": 0,
    }
    total_scripts = 0
    tasks_checked = 0

    for task_dir in tasks:
        task_name = f"{task_dir.parent.name}/{task_dir.name}"
        task_violations = []

        # Parse task.toml
        toml_path = task_dir / "task.toml"
        toml_data = parse_toml(toml_path)
        if toml_data is None:
            task_violations.append(("parse", "Failed to parse task.toml"))
            all_violations[task_name] = task_violations
            continue

        tasks_checked += 1

        # Check 4: Weights
        weight_issues = check_weights(task_dir, toml_data)
        for issue in weight_issues:
            task_violations.append(("weights", issue))
            summary["weights"] += 1

        # Check 6: Artifact match
        artifact_issues = check_artifact_match(task_dir, toml_data)
        for issue in artifact_issues:
            task_violations.append(("artifact_match", issue))
            summary["artifact_match"] += 1

        # Check scripts
        checks_dir = task_dir / "checks"
        if not checks_dir.exists():
            task_violations.append(("checks", "No checks/ directory"))
            all_violations[task_name] = task_violations
            continue

        for script in sorted(checks_dir.glob("check_*.sh")):
            total_scripts += 1
            script_name = script.name

            # Check 1: Environment variable usage
            env_issues = check_env_usage(script)
            for issue in env_issues:
                task_violations.append(("env_usage", f"{script_name}: {issue}"))
                summary["env_usage"] += 1

            # Check 2: set flags
            flag_issues = check_set_flags(script)
            for issue in flag_issues:
                task_violations.append(("set_flags", f"{script_name}: {issue}"))
                summary["set_flags"] += 1

            # Check 3: JSON output
            json_issues = check_json_output(script)
            for issue in json_issues:
                task_violations.append(("json_output", f"{script_name}: {issue}"))
                summary["json_output"] += 1

            # Check 5: Executable
            exec_issues = check_executable(script)
            for issue in exec_issues:
                task_violations.append(("executable", f"{script_name}: {issue}"))
                summary["executable"] += 1

        if task_violations:
            all_violations[task_name] = task_violations

    total_violations = sum(summary.values())
    tasks_with_issues = len(all_violations)

    if args.out is not None:
        write_report(args.out, tasks_checked, total_scripts, all_violations, summary)

    print(f"\nAudit complete: {tasks_checked} tasks, {total_scripts} scripts")
    print(f"Violations: {total_violations} across {tasks_with_issues} tasks")
    if args.out is not None:
        print(f"Report: {args.out}")

    return all_violations, summary


if __name__ == "__main__":
    violations, summary = main()
    # Exit with error if violations found (useful for CI)
    sys.exit(1 if sum(summary.values()) > 0 else 0)
