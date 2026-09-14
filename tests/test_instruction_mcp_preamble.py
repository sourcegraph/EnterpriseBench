"""Tests for MCP instruction preamble injection in run_task.py.

Verifies:
  - instruction_mcp.md content is prepended for mcp_only and hybrid modes
  - instruction_mcp.md is NOT prepended for baseline mode
  - Mode-specific header lines are included for mcp_only and hybrid
  - Missing instruction_mcp.md is handled gracefully (header still appears)
  - Missing instruction.md returns None
  - _setup_container passes mode through correctly
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make scripts importable
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "scripts" / "orchestration")
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "infra"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from run_task import _build_instruction_text, _derive_graded_artifact_path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MCP_ONLY_HEADER = (
    "**IMPORTANT: The repositories exist in /workspace but you do not have "
    "permission to read them — every local read will fail with Permission "
    "denied. Do not try to work around this; it is enforced by the filesystem, "
    "not by instruction. You MUST use Sourcegraph MCP tools for all code "
    "access.**"
)

HYBRID_HEADER = "# Sourcegraph MCP Tools Available"


def _patch_docker_exec_ok():
    """Patch _docker_exec to report success.

    _setup_container now locks the verifier assets down and raises if any of
    those chown/chmod calls fails, so the mock has to return a real returncode
    (a bare MagicMock's attribute is truthy, i.e. a failure).
    """
    return patch(
        "run_task._docker_exec",
        return_value=MagicMock(returncode=0, stdout="", stderr=""),
    )


@pytest.fixture()
def task_dir(tmp_path: Path) -> Path:
    """Create a minimal task directory with instruction.md."""
    (tmp_path / "instruction.md").write_text("# Task\nDo the thing.\n")
    return tmp_path


@pytest.fixture()
def task_dir_with_mcp(task_dir: Path) -> Path:
    """Task directory that also has instruction_mcp.md."""
    (task_dir / "instruction_mcp.md").write_text(
        "# MCP Tools\nUse sg_keyword_search to find code.\n"
    )
    return task_dir


# ---------------------------------------------------------------------------
# Baseline mode
# ---------------------------------------------------------------------------


class TestBaselineMode:
    def test_baseline_does_not_prepend_mcp_content(
        self, task_dir_with_mcp: Path
    ) -> None:
        result = _build_instruction_text(task_dir_with_mcp, "baseline")
        assert result is not None
        assert "MCP Tools" not in result
        assert "sg_keyword_search" not in result

    def test_baseline_does_not_include_mcp_header(self, task_dir: Path) -> None:
        result = _build_instruction_text(task_dir, "baseline")
        assert result is not None
        assert MCP_ONLY_HEADER not in result
        assert HYBRID_HEADER not in result

    def test_baseline_includes_instruction_content(self, task_dir: Path) -> None:
        result = _build_instruction_text(task_dir, "baseline")
        assert result is not None
        assert "# Task" in result
        assert "Do the thing." in result

    def test_baseline_includes_output_appendix(self, task_dir: Path) -> None:
        result = _build_instruction_text(task_dir, "baseline")
        assert result is not None
        assert "## Output Requirements" in result

    def test_checker_path_supersedes_stale_instruction_path(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "instruction.md").write_text(
            "Write a detailed report to /workspace/LEGACY_REPORT.md."
        )
        checks = tmp_path / "checks"
        checks.mkdir()
        (checks / "check_answer.sh").write_text(
            'export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"\n'
        )

        result = _build_instruction_text(tmp_path, "baseline")

        assert result is not None
        assert (
            "The grader reads only `/workspace/agent_output/answer.json`"
        ) in result
        assert (
            "supersedes any other output path mentioned earlier"
        ) in result
        assert "Do not create secondary or legacy deliverables" in result

    def test_bespoke_markdown_checker_path_gets_markdown_appendix(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "instruction.md").write_text("Investigate the incident.")
        checks = tmp_path / "checks"
        checks.mkdir()
        (checks / "check_report.sh").write_text(
            'REPORT="${WORKSPACE:-/workspace}/moby/INCIDENT_REPORT.md"\n'
        )

        result = _build_instruction_text(tmp_path, "baseline")

        assert result is not None
        assert "The grader reads only `/workspace/moby/INCIDENT_REPORT.md`" in result
        assert "Write the complete report as Markdown" in result
        assert "/workspace/agent_output/answer.json" not in result
        assert "```json" not in result

    def test_bespoke_json_checker_path_does_not_impose_answer_schema(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "instruction.md").write_text(
            "Write the requested drift report using the documented schema."
        )
        checks = tmp_path / "checks"
        checks.mkdir()
        (checks / "check_drift.sh").write_text(
            'export REPORT="${WORKSPACE:-/workspace}/DRIFT_REPORT.json"\n'
        )

        result = _build_instruction_text(tmp_path, "baseline")

        assert result is not None
        assert "The grader reads only `/workspace/DRIFT_REPORT.json`" in result
        assert "Follow the task's requested JSON schema exactly" in result
        assert "/workspace/agent_output/answer.json" not in result
        assert '"source_files"' not in result

    def test_conflicting_checker_paths_fail_closed(self, tmp_path: Path) -> None:
        (tmp_path / "instruction.md").write_text("Do the task.")
        checks = tmp_path / "checks"
        checks.mkdir()
        (checks / "check_a.sh").write_text(
            'REPORT="$WORKSPACE/FIRST_REPORT.md"\n'
        )
        (checks / "check_b.sh").write_text(
            'REPORT="$WORKSPACE/SECOND_REPORT.md"\n'
        )

        with pytest.raises(ValueError, match="multiple graded artifact paths"):
            _build_instruction_text(tmp_path, "baseline")

    def test_graded_artifact_path_ignores_non_artifact_workspace_values(
        self, tmp_path: Path
    ) -> None:
        checks = tmp_path / "checks"
        checks.mkdir()
        (checks / "check_report.sh").write_text(
            'WORKSPACE="${WORKSPACE:-/workspace}"\n'
            'MAX_REPORT_BYTES=200000\n'
            'REPORT="$WORKSPACE/analysis/IMPACT_REPORT.md"\n'
            'GT_FILE="$WORKSPACE/.task/ground_truth.json"\n'
        )

        assert (
            _derive_graded_artifact_path(tmp_path)
            == "/workspace/analysis/IMPACT_REPORT.md"
        )

    def test_graded_artifact_path_rejects_parent_traversal(
        self, tmp_path: Path
    ) -> None:
        checks = tmp_path / "checks"
        checks.mkdir()
        (checks / "check_report.sh").write_text(
            'REPORT="$WORKSPACE/../escaped/REPORT.md"\n'
        )

        with pytest.raises(ValueError, match="outside /workspace"):
            _derive_graded_artifact_path(tmp_path)

    def test_graded_artifact_path_supports_event_replay_jsonl(
        self, tmp_path: Path
    ) -> None:
        checks = tmp_path / "checks"
        checks.mkdir()
        (checks / "check_actions.sh").write_text(
            'ACTIONS="$WORKSPACE/actions.jsonl"\n'
        )

        assert _derive_graded_artifact_path(tmp_path) == "/workspace/actions.jsonl"
        (tmp_path / "instruction.md").write_text("Write one action per line.")
        instruction = _build_instruction_text(tmp_path, "baseline")
        assert instruction is not None
        assert "Write the complete report as JSON Lines" in instruction

    def test_unrecognized_checker_artifact_contract_fails_closed(
        self, tmp_path: Path
    ) -> None:
        checks = tmp_path / "checks"
        checks.mkdir()
        (checks / "check_report.sh").write_text(
            'RESULT_PATH="$WORKSPACE/report.md"\n'
        )

        with pytest.raises(ValueError, match="cannot determine graded artifact"):
            _derive_graded_artifact_path(tmp_path)


# ---------------------------------------------------------------------------
# mcp_only mode
# ---------------------------------------------------------------------------


class TestMcpOnlyMode:
    def test_mcp_only_prepends_mcp_content(self, task_dir_with_mcp: Path) -> None:
        result = _build_instruction_text(task_dir_with_mcp, "mcp_only")
        assert result is not None
        assert "MCP Tools" in result
        assert "sg_keyword_search" in result

    def test_mcp_only_includes_header(self, task_dir_with_mcp: Path) -> None:
        result = _build_instruction_text(task_dir_with_mcp, "mcp_only")
        assert result is not None
        assert MCP_ONLY_HEADER in result

    def test_mcp_only_does_not_include_hybrid_header(
        self, task_dir_with_mcp: Path
    ) -> None:
        result = _build_instruction_text(task_dir_with_mcp, "mcp_only")
        assert result is not None
        assert HYBRID_HEADER not in result

    def test_mcp_only_preamble_before_instruction(
        self, task_dir_with_mcp: Path
    ) -> None:
        result = _build_instruction_text(task_dir_with_mcp, "mcp_only")
        assert result is not None
        mcp_pos = result.index("sg_keyword_search")
        task_pos = result.index("# Task")
        assert mcp_pos < task_pos, "MCP preamble must appear before instruction body"

    def test_mcp_only_includes_instruction_content(
        self, task_dir_with_mcp: Path
    ) -> None:
        result = _build_instruction_text(task_dir_with_mcp, "mcp_only")
        assert result is not None
        assert "Do the thing." in result

    def test_mcp_only_includes_output_appendix(self, task_dir_with_mcp: Path) -> None:
        result = _build_instruction_text(task_dir_with_mcp, "mcp_only")
        assert result is not None
        assert "## Output Requirements" in result

    def test_mcp_only_without_mcp_file_still_has_header(self, task_dir: Path) -> None:
        """Even without instruction_mcp.md, the mode header should appear."""
        result = _build_instruction_text(task_dir, "mcp_only")
        assert result is not None
        assert MCP_ONLY_HEADER in result


# ---------------------------------------------------------------------------
# hybrid mode
# ---------------------------------------------------------------------------


class TestHybridMode:
    def test_hybrid_prepends_mcp_content(self, task_dir_with_mcp: Path) -> None:
        result = _build_instruction_text(task_dir_with_mcp, "hybrid")
        assert result is not None
        assert "MCP Tools" in result

    def test_hybrid_includes_header(self, task_dir_with_mcp: Path) -> None:
        result = _build_instruction_text(task_dir_with_mcp, "hybrid")
        assert result is not None
        assert HYBRID_HEADER in result

    def test_hybrid_does_not_include_mcp_only_header(
        self, task_dir_with_mcp: Path
    ) -> None:
        result = _build_instruction_text(task_dir_with_mcp, "hybrid")
        assert result is not None
        assert MCP_ONLY_HEADER not in result

    def test_hybrid_preamble_before_instruction(self, task_dir_with_mcp: Path) -> None:
        result = _build_instruction_text(task_dir_with_mcp, "hybrid")
        assert result is not None
        mcp_pos = result.index("sg_keyword_search")
        task_pos = result.index("# Task")
        assert mcp_pos < task_pos

    def test_hybrid_without_mcp_file_still_has_header(self, task_dir: Path) -> None:
        result = _build_instruction_text(task_dir, "hybrid")
        assert result is not None
        assert HYBRID_HEADER in result


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_missing_instruction_md_returns_none(self, tmp_path: Path) -> None:
        result = _build_instruction_text(tmp_path, "baseline")
        assert result is None

    def test_missing_instruction_md_returns_none_mcp_mode(self, tmp_path: Path) -> None:
        result = _build_instruction_text(tmp_path, "mcp_only")
        assert result is None

    def test_separator_between_preamble_and_instruction(
        self, task_dir_with_mcp: Path
    ) -> None:
        """There should be a --- separator between preamble and instruction body."""
        result = _build_instruction_text(task_dir_with_mcp, "mcp_only")
        assert result is not None
        # Find the separator between preamble and instruction
        # The preamble ends, then ---, then instruction begins
        parts = result.split("---")
        assert len(parts) >= 2, "Expected at least one --- separator"


# ---------------------------------------------------------------------------
# _setup_container passes mode
# ---------------------------------------------------------------------------


class TestSetupContainerPassesMode:
    """Verify _setup_container calls _build_instruction_text with the correct mode."""

    def test_setup_container_passes_mode_to_build(self, task_dir: Path) -> None:
        with patch(
            "run_task._build_instruction_text", return_value=None
        ) as mock_build, _patch_docker_exec_ok(), patch("run_task._docker_cp"):
            from run_task import _setup_container

            _setup_container("fake-container", task_dir, {}, mode="hybrid")
            mock_build.assert_called_once_with(
                task_dir, "hybrid", repos=[], require_grounded_citations=False
            )

    def test_setup_container_defaults_to_baseline(self, task_dir: Path) -> None:
        with patch(
            "run_task._build_instruction_text", return_value=None
        ) as mock_build, _patch_docker_exec_ok(), patch("run_task._docker_cp"):
            from run_task import _setup_container

            _setup_container("fake-container", task_dir, {})
            mock_build.assert_called_once_with(
                task_dir, "baseline", repos=[], require_grounded_citations=False
            )

    def test_setup_container_passes_require_grounded_citations_true(
        self, task_dir: Path
    ) -> None:
        with patch(
            "run_task._build_instruction_text", return_value=None
        ) as mock_build, _patch_docker_exec_ok(), patch("run_task._docker_cp"):
            from run_task import _setup_container

            task_data = {"ground_truth": {"require_grounded_citations": True}}
            _setup_container("fake-container", task_dir, task_data)
            mock_build.assert_called_once_with(
                task_dir, "baseline", repos=[], require_grounded_citations=True
            )


# ---------------------------------------------------------------------------
# answer.json appendix citation-awareness
# ---------------------------------------------------------------------------


class TestAnswerAppendixCitations:
    """require_grounded_citations must add citations guidance to the answer.json
    appendix, mirroring the schema lib/eb_verify/plugins/answer.py's groundedness
    gate expects (top-level `citations` list of {repo, file, evidence_span})."""

    def test_flag_true_adds_citations_guidance(self, task_dir: Path) -> None:
        result = _build_instruction_text(
            task_dir, "baseline", require_grounded_citations=True
        )
        assert result is not None
        assert "answer.json" in result
        assert "citations" in result
        assert "evidence_span" in result

    def test_flag_false_omits_citations_guidance(self, task_dir: Path) -> None:
        result = _build_instruction_text(
            task_dir, "baseline", require_grounded_citations=False
        )
        assert result is not None
        assert "answer.json" in result
        assert "citations" not in result
        assert "evidence_span" not in result

    def test_flag_defaults_to_false(self, task_dir: Path) -> None:
        """Existing callers that don't pass the new param keep current behavior."""
        result = _build_instruction_text(task_dir, "baseline")
        assert result is not None
        assert "citations" not in result

    def test_flag_true_requires_verbatim_evidence(self, task_dir: Path) -> None:
        result = _build_instruction_text(
            task_dir, "baseline", require_grounded_citations=True
        )
        assert result is not None
        assert "verbatim" in result.lower()

    def test_flag_true_span_length_matches_groundedness_gate(
        self, task_dir: Path
    ) -> None:
        """The advertised minimum evidence-span length must track
        eb_verify.groundedness.MIN_SPAN_CHARS, not a hardcoded copy — otherwise
        the appendix can silently drift out of sync with what the gate enforces."""
        from eb_verify.groundedness import MIN_SPAN_CHARS

        result = _build_instruction_text(
            task_dir, "baseline", require_grounded_citations=True
        )
        assert result is not None
        assert f">={MIN_SPAN_CHARS} characters" in result


# ---------------------------------------------------------------------------
# Output appendix anchors answer paths to /workspace (Fix 3)
# ---------------------------------------------------------------------------


class TestOutputAppendixPathAnchoring:
    """The output appendix must instruct agents to write /workspace-absolute
    paths; repo-relative example paths let agents emit paths that miss the
    oracle's /workspace-anchored expected_files."""

    def test_appendix_example_paths_are_workspace_absolute(
        self, task_dir: Path
    ) -> None:
        result = _build_instruction_text(task_dir, "baseline")
        assert result is not None
        # Every example "path" value in the JSON skeleton is anchored at /workspace.
        assert '"path": "/workspace/' in result
        # No bare repo-relative example path remains.
        assert '"path": "relative/' not in result
        assert '"relative/path/to/file"' not in result

    def test_appendix_states_paths_must_be_absolute(self, task_dir: Path) -> None:
        result = _build_instruction_text(task_dir, "baseline")
        assert result is not None
        lowered = result.lower()
        assert "/workspace/" in lowered
        assert "absolute" in lowered


# ---------------------------------------------------------------------------
# _setup_container writes per-checkpoint .verifiers/<name>.meta (Fix 1)
# ---------------------------------------------------------------------------


@pytest.fixture()
def task_dir_with_checks(task_dir: Path) -> Path:
    """Task directory with a checks/ dir holding two check scripts."""
    checks = task_dir / "checks"
    checks.mkdir()
    check_body = (
        "#!/usr/bin/env bash\n"
        'ANSWER_FILE="$WORKSPACE/agent_output/answer.json"\n'
        "exit 0\n"
    )
    (checks / "check_api_migration.sh").write_text(check_body)
    (checks / "check_tests.sh").write_text(check_body)
    return task_dir


def _docker_cp_meta_capture():
    """A _docker_cp side_effect that captures .meta content by filename.

    .meta files are written via a tempfile + _docker_cp (same pattern as
    instruction.md), and the tempfile is unlinked immediately after the cp
    call returns — so content must be read from the source path here, during
    the call, rather than from call_args_list afterward.
    """
    writes: dict[str, str] = {}

    def _cp(src: str, dest: str) -> None:
        if dest.endswith(".meta"):
            writes[dest.rsplit("/", 1)[-1]] = Path(src).read_text()

    return _cp, writes


class TestSetupContainerWritesVerifierMeta:
    """_setup_container must write .verifiers/<name>.meta with the toml weight
    and timeout so test_runner.sh applies real weights instead of defaulting
    every checkpoint to 1.0 (which turns task_score into a 0-N sum)."""

    def test_meta_written_with_toml_weight_and_timeout(
        self, task_dir_with_checks: Path
    ) -> None:
        task_data = {
            "repos": [],
            "checkpoints": [
                {
                    "name": "update_apis",
                    "weight": 0.65,
                    "verifier": "checks/check_api_migration.sh",
                    "timeout_seconds": 90,
                },
                {
                    "name": "tests_pass",
                    "weight": 0.35,
                    "verifier": "checks/check_tests.sh",
                },
            ],
        }
        cp_side_effect, writes = _docker_cp_meta_capture()
        with _patch_docker_exec_ok(), patch(
            "run_task._docker_cp", side_effect=cp_side_effect
        ):
            from run_task import _setup_container

            _setup_container("fake-container", task_dir_with_checks, task_data)

        # Meta filename matches the .verifiers/<name>.sh naming (check_ stripped).
        assert "api_migration.meta" in writes
        assert "tests.meta" in writes
        assert "weight=0.65" in writes["api_migration.meta"]
        assert "timeout=90" in writes["api_migration.meta"]
        # Missing timeout_seconds falls back to the runner default (120).
        assert "weight=0.35" in writes["tests.meta"]
        assert "timeout=120" in writes["tests.meta"]

    def test_no_meta_written_without_checkpoints(
        self, task_dir_with_checks: Path
    ) -> None:
        cp_side_effect, writes = _docker_cp_meta_capture()
        with _patch_docker_exec_ok(), patch(
            "run_task._docker_cp", side_effect=cp_side_effect
        ):
            from run_task import _setup_container

            _setup_container("fake-container", task_dir_with_checks, {"repos": []})

        # No checkpoint metadata → no .meta files (runner falls back to 1.0).
        assert writes == {}
