"""Prompt->GT disclosure audit + CI gate (scripts/audit_disclosure.py).

The tier logic is the load-bearing part: audit_instructions.py's exact-path
check reported the toast_retrieval sample clean while 33/65 required files
were disclosed at basename/stem tier. These tests pin each tier boundary and
both gate failure modes (over-threshold and manifest drift).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from audit_disclosure import audit_task, disclosure, gate  # noqa: E402


class TestDisclosureTiers:
    def test_full_path_beats_lower_tiers(self):
        report = disclosure("Look at zerver/lib/avatar.py first.", ["zerver/lib/avatar.py"])
        assert report["per_file"][0]["tier"] == "full_path"
        assert report["disclosed_fraction"] == 1.0

    def test_basename_without_directory(self):
        report = disclosure("The bug is in avatar.py somewhere.", ["zerver/lib/avatar.py"])
        assert report["per_file"][0]["tier"] == "basename"

    def test_stem_at_word_boundary(self):
        report = disclosure("The avatar helper misbehaves.", ["zerver/lib/avatar.py"])
        assert report["per_file"][0]["tier"] == "stem"

    def test_stem_inside_word_does_not_match(self):
        report = disclosure("This concerns cavatappi noodles.", ["zerver/lib/avatar.py"])
        assert report["per_file"][0]["tier"] == "undisclosed"

    def test_short_stem_is_never_a_stem_match(self):
        # "io" appears constantly in prose; the >=4-char floor keeps it out.
        report = disclosure("Async io is involved.", ["src/io.rs"])
        assert report["per_file"][0]["tier"] == "undisclosed"

    def test_matching_is_case_insensitive(self):
        report = disclosure("See Avatar.PY for details.", ["zerver/lib/avatar.py"])
        assert report["per_file"][0]["tier"] == "basename"

    def test_fraction_counts_all_disclosed_tiers(self):
        report = disclosure(
            "Fix a/b.py; the avatar logic is wrong.",
            ["a/b.py", "zerver/lib/avatar.py", "hidden/thing.c"],
        )
        assert report["tiers"] == {
            "full_path": 1,
            "basename": 0,
            "stem": 1,
            "undisclosed": 1,
        }
        assert report["disclosed_fraction"] == pytest.approx(2 / 3, abs=1e-3)

    def test_empty_required_is_zero_not_crash(self):
        assert disclosure("anything", [])["disclosed_fraction"] == 0.0


def _write_task(tmp_path: Path, instruction: str, required) -> Path:
    task_dir = tmp_path / "suite" / "task-001"
    task_dir.mkdir(parents=True)
    (task_dir / "instruction.md").write_text(instruction)
    (task_dir / "ground_truth.json").write_text(
        json.dumps({"ground_truth": {"required_files": required}})
    )
    return task_dir


class TestAuditTask:
    def test_reads_through_the_scoring_extractor_dict_shape(self, tmp_path):
        # Most tasks store required_files as {path, repo} dicts; the audit
        # must flatten them exactly as scoring does.
        task_dir = _write_task(
            tmp_path,
            "Investigate the frobnicator pipeline.",
            [{"path": "src/frobnicator.c", "repo": "github.com/x/y"}],
        )
        report = audit_task(task_dir)
        assert report["task_id"] == "task-001"
        assert report["per_file"] == [{"path": "src/frobnicator.c", "tier": "stem"}]

    def test_plain_string_shape(self, tmp_path):
        task_dir = _write_task(tmp_path, "No hints here.", ["src/frobnicator.c"])
        assert audit_task(task_dir)["per_file"][0]["tier"] == "undisclosed"


class TestGate:
    # Manifest keys are suite-qualified: bare ids collide (calibration-002
    # exists in three suites with different disclosure).
    ROWS = [
        {"suite": "s1", "task_id": "clean-001", "disclosed_fraction": 0.2, "tiers": {}},
        {"suite": "s1", "task_id": "leaky-001", "disclosed_fraction": 0.5, "tiers": {}},
    ]

    def _manifest(self, tmp_path: Path, tasks: list[str]) -> Path:
        path = tmp_path / "slice.json"
        path.write_text(json.dumps({"max_disclosed_fraction": 0.334, "tasks": tasks}))
        return path

    def test_enrolled_task_within_threshold_passes(self, tmp_path):
        assert gate(self.ROWS, self._manifest(tmp_path, ["s1/clean-001"])) == []

    def test_enrolled_task_over_threshold_fails(self, tmp_path):
        violations = gate(self.ROWS, self._manifest(tmp_path, ["s1/leaky-001"]))
        assert len(violations) == 1
        assert "s1/leaky-001" in violations[0] and "0.5" in violations[0]

    def test_unenrolled_leaky_task_is_not_gated(self, tmp_path):
        # Disclosure is legitimate scenario setup for non-retrieval tasks;
        # only enrollment opts a task into enforcement.
        assert gate(self.ROWS, self._manifest(tmp_path, ["s1/clean-001"])) == []

    def test_manifest_drift_is_a_failure_not_a_silent_pass(self, tmp_path):
        violations = gate(self.ROWS, self._manifest(tmp_path, ["s1/gone-001"]))
        assert len(violations) == 1
        assert "s1/gone-001" in violations[0] and "drift" in violations[0]


def test_repo_manifest_gates_green_against_current_corpus():
    """The committed slice manifest must hold against the committed tasks."""
    from audit_disclosure import audit_corpus

    repo_root = Path(__file__).resolve().parents[1]
    manifest = repo_root / "configs" / "retrieval_slice.json"
    violations = gate(audit_corpus(), manifest)
    assert violations == []
