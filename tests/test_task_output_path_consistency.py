"""Guard against prompt↔check-script output-path drift.

Invariant: every explicit `Write ... to /workspace/<path>` instruction in a
task's `instruction.md` or `task.toml [task].prompt` must name a path that
at least one of the task's check scripts actually reads. Without this, the
agent writes to one location and the verifier reads from another, silently
scoring 0.0 at runtime.

The test intentionally ignores `/workspace/...` references that are NOT
output-write instructions (for example, repo-input paths like
`/workspace/grafana/pkg/...`), and skips tasks whose instructions are silent
on output location — in that case `scripts/orchestration/run_task.py` injects
a default `/workspace/agent_output/answer.json` appendix.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
BENCHMARKS_DIR = ROOT / "benchmarks"
sys.path.insert(0, str(ROOT / "scripts" / "orchestration"))
sys.path.insert(0, str(ROOT / "scripts" / "infra"))

from run_task import (  # noqa: E402
    _build_instruction_text,
    _derive_graded_artifact_path,
)

_WORKSPACE_PATH_IN_SHELL = re.compile(
    r"\$\{?WORKSPACE(?::-[^}]*)?\}?(/[A-Za-z0-9_./-]+)"
)
_WRITE_TO_WORKSPACE_PATH = re.compile(
    r"[Ww]rite[^.\n]*?\sto\s+`?(/workspace/[A-Za-z0-9_./-]+)",
)


def _collect_task_dirs() -> list[Path]:
    tasks: list[Path] = []
    for toml_path in sorted(BENCHMARKS_DIR.rglob("task.toml")):
        rel = toml_path.relative_to(BENCHMARKS_DIR)
        if rel.parts[0] in ("mined", "_archived"):
            continue
        tasks.append(toml_path.parent)
    return tasks


TASK_DIRS = _collect_task_dirs()
TASK_IDS = [str(d.relative_to(BENCHMARKS_DIR)) for d in TASK_DIRS]


def _check_script_read_paths(task_dir: Path) -> set[str]:
    """Return `/workspace`-prefixed paths that any check script reads."""
    paths: set[str] = set()
    checks = task_dir / "checks"
    if not checks.is_dir():
        return paths
    for sh in checks.glob("*.sh"):
        text = sh.read_text(encoding="utf-8", errors="replace")
        for m in _WORKSPACE_PATH_IN_SHELL.finditer(text):
            suffix = m.group(1)
            if suffix in {"/", ""}:
                continue
            paths.add(f"/workspace{suffix}")
    return paths


def _instruction_write_paths(task_dir: Path) -> set[str]:
    """Return every `/workspace/<path>` mentioned in a `Write ... to ...`
    instruction in `instruction.md` or `task.toml [task].prompt`."""
    paths: set[str] = set()
    texts: list[str] = []
    instr = task_dir / "instruction.md"
    if instr.exists():
        texts.append(instr.read_text(encoding="utf-8", errors="replace"))
    toml_path = task_dir / "task.toml"
    if toml_path.exists():
        try:
            data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
            prompt = data.get("task", {}).get("prompt", "")
            if isinstance(prompt, str) and prompt:
                texts.append(prompt)
        except tomllib.TOMLDecodeError:
            pass
    for text in texts:
        for m in _WRITE_TO_WORKSPACE_PATH.finditer(text):
            path = m.group(1).rstrip(".`,;:)")
            paths.add(path)
    return paths


@pytest.mark.parametrize("task_dir", TASK_DIRS, ids=TASK_IDS)
def test_effective_instruction_names_only_graded_output(task_dir: Path) -> None:
    """The runner must resolve stale task prose to the checkpoint artifact."""
    write_paths = _instruction_write_paths(task_dir)
    graded_path = _derive_graded_artifact_path(task_dir)
    effective = _build_instruction_text(task_dir, "baseline")

    assert effective is not None
    assert f"The grader reads only `{graded_path}`" in effective
    assert "Do not create secondary or legacy deliverables" in effective
    if write_paths and graded_path not in write_paths:
        assert "supersedes any other output path mentioned earlier" in effective


@pytest.mark.parametrize("task_dir", TASK_DIRS, ids=TASK_IDS)
def test_checkpoint_scripts_have_one_graded_output_path(task_dir: Path) -> None:
    """Ambiguous checkpoint destinations must fail before a paid run starts."""
    path = _derive_graded_artifact_path(task_dir)

    assert path.startswith("/workspace/")
    assert Path(path).suffix.lower() in {
        ".json",
        ".jsonl",
        ".md",
        ".txt",
        ".yaml",
        ".yml",
    }


def test_raw_instruction_audit_still_exposes_stale_paths() -> None:
    """The audit helper remains available for task-authoring remediation."""
    task_dir = BENCHMARKS_DIR / "customer_escalation/support-mapping-dual-flask-001"

    assert _instruction_write_paths(task_dir) == {"/workspace/SUPPORT_MAPPING.md"}
    assert "/workspace/agent_output/answer.json" in _check_script_read_paths(task_dir)
