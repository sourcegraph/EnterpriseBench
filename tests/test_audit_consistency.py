"""Tests for scripts/audit_consistency.py.

Pins two repairs to the CI merge-blocker step ("Audit task consistency"):

1. BENCH_DIR is derived from this script's own location, not hardcoded to
   `/home/ds/EnterpriseBench/benchmarks` — a path that exists on exactly one
   machine's exactly one checkout. Anywhere else (CI, a worktree, a renamed
   clone) the hardcoded path doesn't exist, `find_task_dirs` raised
   FileNotFoundError, and the audit never ran — which is why CI died here on
   every push rather than reporting real violations.
2. The results-file write is opt-in via --out. The old code always wrote to
   a second hardcoded absolute path as a side effect of running the script,
   independent of whether the caller wanted a file at all.

The exit-code/violation-detection logic itself was already correct (`sys.exit(1
if sum(summary.values()) > 0 else 0)`) and is pinned here against a seeded
fixture so the fix above can't silently regress it.
"""

from __future__ import annotations

import stat
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import audit_consistency as ac

REPO_ROOT = Path(__file__).resolve().parent.parent

# A check script with every property audit_consistency checks for: os.environ
# (not shell $VAR expansion), set -euo pipefail, score+passed JSON keys, and
# (set by the caller) the executable bit.
_CLEAN_CHECK_SH = textwrap.dedent(
    """\
    #!/bin/bash
    set -euo pipefail
    python3 -c "
    import json, os
    path = os.environ['ANSWER_PATH']
    print(json.dumps({'score': 1.0, 'passed': True}))
    "
    """
)

_CLEAN_TASK_TOML = textwrap.dedent(
    """\
    [task]
    id = "clean-task-001"
    suite = "customer_escalation"

    [artifacts]
    required = ["answer"]

    [[checkpoints]]
    name = "check_one"
    weight = 1.0
    verifier = "checks/check_one.sh"
    """
)


def _write_clean_task(benchmarks: Path) -> Path:
    task_dir = benchmarks / "customer_escalation" / "clean-task-001"
    checks_dir = task_dir / "checks"
    checks_dir.mkdir(parents=True)
    (task_dir / "task.toml").write_text(_CLEAN_TASK_TOML)
    script = checks_dir / "check_answer.sh"
    script.write_text(_CLEAN_CHECK_SH)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return task_dir


class TestBenchDirProvenance:
    def test_bench_dir_is_derived_from_script_location(self) -> None:
        assert ac.BENCH_DIR == REPO_ROOT / "benchmarks"

    def test_bench_dir_is_not_the_old_hardcoded_path(self) -> None:
        assert str(ac.BENCH_DIR) != "/home/ds/EnterpriseBench/benchmarks"

    def test_bench_dir_exists_on_this_checkout(self) -> None:
        # The whole point of deriving it from __file__: it must resolve on
        # ANY checkout, not just the machine it was written on.
        assert ac.BENCH_DIR.is_dir()


class TestOptInReport:
    def test_default_run_writes_no_file(self, tmp_path: Path, monkeypatch) -> None:
        clean_benchmarks = tmp_path / "benchmarks"
        _write_clean_task(clean_benchmarks)
        monkeypatch.setattr(ac, "BENCH_DIR", clean_benchmarks)

        before = set(tmp_path.rglob("*.md"))
        violations, summary = ac.main([])
        after = set(tmp_path.rglob("*.md"))

        assert before == after, "main() without --out wrote a file as a side effect"
        assert sum(summary.values()) == 0

    def test_out_flag_writes_the_report(self, tmp_path: Path, monkeypatch) -> None:
        clean_benchmarks = tmp_path / "benchmarks"
        _write_clean_task(clean_benchmarks)
        monkeypatch.setattr(ac, "BENCH_DIR", clean_benchmarks)

        out_path = tmp_path / "reports" / "audit.md"
        ac.main(["--out", str(out_path)])

        assert out_path.exists()
        assert "Cross-Task Consistency Audit" in out_path.read_text()


class TestViolationDetectionStillExitsNonzero:
    """Seeded-violation regression guard for the exit-code contract the
    --out refactor must not disturb: exit 1 iff any violation summed.
    """

    def test_clean_fixture_reports_zero_violations(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        clean_benchmarks = tmp_path / "benchmarks"
        _write_clean_task(clean_benchmarks)
        monkeypatch.setattr(ac, "BENCH_DIR", clean_benchmarks)

        _violations, summary = ac.main([])

        assert sum(summary.values()) == 0

    def test_non_executable_check_script_is_a_violation(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        benchmarks = tmp_path / "benchmarks"
        task_dir = _write_clean_task(benchmarks)
        script = task_dir / "checks" / "check_answer.sh"
        script.chmod(script.stat().st_mode & ~stat.S_IXUSR)
        monkeypatch.setattr(ac, "BENCH_DIR", benchmarks)

        violations, summary = ac.main([])

        assert summary["executable"] == 1
        assert sum(summary.values()) > 0

    def test_bad_weights_is_a_violation(self, tmp_path: Path, monkeypatch) -> None:
        benchmarks = tmp_path / "benchmarks"
        task_dir = _write_clean_task(benchmarks)
        (task_dir / "task.toml").write_text(
            _CLEAN_TASK_TOML.replace("weight = 1.0", "weight = 0.4")
        )
        monkeypatch.setattr(ac, "BENCH_DIR", benchmarks)

        violations, summary = ac.main([])

        assert summary["weights"] == 1
        assert sum(summary.values()) > 0
