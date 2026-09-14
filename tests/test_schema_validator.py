"""Tests for eb_verify.schema_validator."""

from __future__ import annotations

from pathlib import Path

import pytest

from eb_verify.schema_validator import (
    ValidationError,
    ValidationResult,
    _validate_semantic_layer,
    validate_task,
)


# ---------------------------------------------------------------------------
# Valid tasks pass
# ---------------------------------------------------------------------------

class TestValidTaskPasses:
    def test_example_task_valid(self, example_task_path):
        result = validate_task(str(example_task_path))
        assert result.valid is True, f"Unexpected errors: {result.errors}"
        assert result.errors == []

    def test_minimal_valid_task(self, valid_task_path):
        result = validate_task(str(valid_task_path))
        assert result.valid is True, f"Unexpected errors: {result.errors}"

    def test_chain_task_valid(self, chain_task_path):
        result = validate_task(str(chain_task_path))
        assert result.valid is True, f"Unexpected errors: {result.errors}"


# ---------------------------------------------------------------------------
# File not found
# ---------------------------------------------------------------------------

class TestFileNotFound:
    def test_nonexistent_file(self, tmp_path):
        result = validate_task(str(tmp_path / "nope.toml"))
        assert result.valid is False
        assert any("not found" in e.message.lower() or "File not found" in e.message for e in result.errors)

    def test_invalid_toml_parse_error(self, tmp_path):
        bad = tmp_path / "bad.toml"
        bad.write_bytes(b"[task\ngarbage = [[[")
        result = validate_task(str(bad))
        assert result.valid is False
        assert any("parse" in e.message.lower() or "TOML" in e.message for e in result.errors)


# ---------------------------------------------------------------------------
# Semantic layer — direct unit tests
# ---------------------------------------------------------------------------

class TestSemanticWeights:
    def test_weights_sum_to_one_passes(self):
        data = {
            "task": {"session_type": "single"},
            "checkpoints": [
                {"name": "a", "weight": 0.5},
                {"name": "b", "weight": 0.5},
            ],
        }
        errors, _ = _validate_semantic_layer(data)
        weight_errors = [e for e in errors if "weight" in e.field]
        assert weight_errors == []

    def test_weights_not_sum_to_one_fails(self):
        data = {
            "task": {"session_type": "single"},
            "checkpoints": [
                {"name": "a", "weight": 0.3},
                {"name": "b", "weight": 0.3},
            ],
        }
        errors, _ = _validate_semantic_layer(data)
        weight_errors = [e for e in errors if "weight" in e.field]
        assert len(weight_errors) == 1
        assert "0.6" in weight_errors[0].message

    def test_single_checkpoint_weight_1(self):
        data = {
            "task": {"session_type": "single"},
            "checkpoints": [{"name": "only", "weight": 1.0}],
        }
        errors, _ = _validate_semantic_layer(data)
        weight_errors = [e for e in errors if "weight" in e.field]
        assert weight_errors == []

    def test_empty_checkpoints_no_weight_error(self):
        data = {"task": {"session_type": "single"}, "checkpoints": []}
        errors, _ = _validate_semantic_layer(data)
        weight_errors = [e for e in errors if "weight" in e.field]
        assert weight_errors == []


class TestSemanticSessionType:
    def test_chain_with_session_count_passes(self):
        data = {
            "task": {"session_type": "chain", "session_count": 3},
            "checkpoints": [{"name": "a", "weight": 1.0}],
        }
        errors, _ = _validate_semantic_layer(data)
        session_errors = [e for e in errors if "session_count" in e.field]
        assert session_errors == []

    def test_chain_without_session_count_fails(self):
        data = {
            "task": {"session_type": "chain"},
            "checkpoints": [{"name": "a", "weight": 1.0}],
        }
        errors, _ = _validate_semantic_layer(data)
        session_errors = [e for e in errors if "session_count" in e.field]
        assert len(session_errors) == 1

    def test_single_with_session_count_fails(self):
        data = {
            "task": {"session_type": "single", "session_count": 2},
            "checkpoints": [{"name": "a", "weight": 1.0}],
        }
        errors, _ = _validate_semantic_layer(data)
        session_errors = [e for e in errors if "session_count" in e.field]
        assert len(session_errors) == 1

    def test_event_replay_without_events_fails(self):
        data = {
            "task": {"session_type": "event_replay"},
            "checkpoints": [{"name": "a", "weight": 1.0}],
        }
        errors, _ = _validate_semantic_layer(data)
        event_errors = [e for e in errors if e.field == "events"]
        assert len(event_errors) == 1

    def test_single_with_events_section_fails(self):
        data = {
            "task": {"session_type": "single"},
            "events": {"event_file": "events.json"},
            "checkpoints": [{"name": "a", "weight": 1.0}],
        }
        errors, _ = _validate_semantic_layer(data)
        event_errors = [e for e in errors if e.field == "events"]
        assert len(event_errors) == 1

    def test_resume_without_resume_state_fails(self):
        data = {
            "task": {"session_type": "resume"},
            "checkpoints": [{"name": "a", "weight": 1.0}],
        }
        errors, _ = _validate_semantic_layer(data)
        rs_errors = [e for e in errors if e.field == "resume_state"]
        assert len(rs_errors) == 1

    def test_single_with_resume_state_fails(self):
        data = {
            "task": {"session_type": "single"},
            "resume_state": {"branch": "feat/x"},
            "checkpoints": [{"name": "a", "weight": 1.0}],
        }
        errors, _ = _validate_semantic_layer(data)
        rs_errors = [e for e in errors if e.field == "resume_state"]
        assert len(rs_errors) == 1


class TestSemanticGroundedCitations:
    """Rule 8: the flag is only enforced on the session type that runs the gate.

    The gate lives in run_task.py's scoring path (single). chain and
    event_replay score elsewhere and never reach it, so the flag there shapes
    the prompt and is then silently never checked — an authoring mistake worth
    failing loudly at lint time, since nothing at runtime would.
    """

    def _data(self, session_type: str, required: list[str] | None = None) -> dict:
        return {
            "task": {"session_type": session_type},
            "ground_truth": {"require_grounded_citations": True},
            "artifacts": {"required": ["answer"] if required is None else required},
            "checkpoints": [{"name": "a", "weight": 1.0}],
        }

    @pytest.mark.parametrize("required", [["incident_report"], ["security_assessment"], []])
    def test_flag_without_an_answer_artifact_fails(self, required):
        """The gate grades agent_output/answer.json and nothing else.

        A task demanding grounded citations while requiring some other artifact
        has no answer.json for the gate to read, so Phase 5c would force 0.0 on
        a perfectly honest run. 14 tasks in benchmarks/ already require only
        incident_report or security_assessment, so this is one edit away.
        """
        errors, _ = _validate_semantic_layer(self._data("single", required=required))
        gate_errors = [
            e for e in errors if e.field == "ground_truth.require_grounded_citations"
        ]
        assert len(gate_errors) == 1
        assert "answer" in gate_errors[0].message

    def test_flag_with_answer_among_several_artifacts_is_fine(self):
        """The gate can read answer.json; co-required artifacts are its own concern."""
        errors, _ = _validate_semantic_layer(
            self._data("single", required=["answer", "topological_order"])
        )
        assert not [
            e for e in errors if e.field == "ground_truth.require_grounded_citations"
        ]

    @pytest.mark.parametrize("session_type", ["chain", "event_replay", "resume"])
    def test_flag_on_non_single_session_fails(self, session_type):
        errors, _ = _validate_semantic_layer(self._data(session_type))
        gate_errors = [
            e for e in errors if e.field == "ground_truth.require_grounded_citations"
        ]
        assert len(gate_errors) == 1
        assert gate_errors[0].severity == "error"

    def test_flag_on_single_session_is_fine(self):
        errors, _ = _validate_semantic_layer(self._data("single"))
        assert not [
            e for e in errors if e.field == "ground_truth.require_grounded_citations"
        ]

    def test_flag_absent_is_fine_on_any_session_type(self):
        data = {
            "task": {"session_type": "chain", "session_count": 2},
            "ground_truth": {},
            "artifacts": {"required": ["incident_report"]},
            "checkpoints": [{"name": "a", "weight": 1.0}],
        }
        errors, _ = _validate_semantic_layer(data)
        assert not [
            e for e in errors if e.field == "ground_truth.require_grounded_citations"
        ]


class TestSemanticDuplicateCheckpoints:
    def test_duplicate_name_caught(self):
        data = {
            "task": {"session_type": "single"},
            "checkpoints": [
                {"name": "step", "weight": 0.5},
                {"name": "step", "weight": 0.5},
            ],
        }
        errors, _ = _validate_semantic_layer(data)
        dup_errors = [e for e in errors if "Duplicate" in e.message]
        assert len(dup_errors) == 1

    def test_unique_names_no_error(self):
        data = {
            "task": {"session_type": "single"},
            "checkpoints": [
                {"name": "step_a", "weight": 0.5},
                {"name": "step_b", "weight": 0.5},
            ],
        }
        errors, _ = _validate_semantic_layer(data)
        dup_errors = [e for e in errors if "Duplicate" in e.message]
        assert dup_errors == []


class TestSemanticGroundTruthRepoRefs:
    def test_valid_repo_ref(self):
        data = {
            "task": {"session_type": "single"},
            "checkpoints": [{"name": "a", "weight": 1.0}],
            "repos": [{"path": "myrepo", "url": "x", "rev": "main"}],
            "ground_truth": {
                "required_files": [{"path": "src/a.py", "repo": "myrepo"}],
            },
        }
        errors, _ = _validate_semantic_layer(data)
        repo_errors = [e for e in errors if "ground_truth" in e.field]
        assert repo_errors == []

    def test_invalid_repo_ref(self):
        data = {
            "task": {"session_type": "single"},
            "checkpoints": [{"name": "a", "weight": 1.0}],
            "repos": [{"path": "myrepo", "url": "x", "rev": "main"}],
            "ground_truth": {
                "required_files": [{"path": "src/a.py", "repo": "badrepo"}],
            },
        }
        errors, _ = _validate_semantic_layer(data)
        repo_errors = [e for e in errors if "ground_truth" in e.field]
        assert len(repo_errors) == 1
        assert "badrepo" in repo_errors[0].message


class TestSemanticDifficultyStratum:
    def test_dual_repo_with_two_repos_passes(self):
        data = {
            "task": {"session_type": "single"},
            "checkpoints": [{"name": "a", "weight": 1.0}],
            "repos": [
                {"path": "r1", "url": "x", "rev": "main"},
                {"path": "r2", "url": "y", "rev": "main"},
            ],
            "difficulty_stratum": "dual_repo",
        }
        errors, _ = _validate_semantic_layer(data)
        stratum_errors = [e for e in errors if "difficulty_stratum" in e.field]
        assert stratum_errors == []

    def test_dual_repo_with_one_repo_fails(self):
        data = {
            "task": {"session_type": "single"},
            "checkpoints": [{"name": "a", "weight": 1.0}],
            "repos": [{"path": "r1", "url": "x", "rev": "main"}],
            "difficulty_stratum": "dual_repo",
        }
        errors, _ = _validate_semantic_layer(data)
        stratum_errors = [e for e in errors if "difficulty_stratum" in e.field]
        assert len(stratum_errors) == 1

    def test_multi_repo_with_three_repos_passes(self):
        data = {
            "task": {"session_type": "single"},
            "checkpoints": [{"name": "a", "weight": 1.0}],
            "repos": [
                {"path": f"r{i}", "url": "x", "rev": "main"} for i in range(3)
            ],
            "difficulty_stratum": "multi_repo",
        }
        errors, _ = _validate_semantic_layer(data)
        stratum_errors = [e for e in errors if "difficulty_stratum" in e.field]
        assert stratum_errors == []

    def test_unknown_stratum_no_error(self):
        # Unknown stratum values are not validated by rule 6
        data = {
            "task": {"session_type": "single"},
            "checkpoints": [{"name": "a", "weight": 1.0}],
            "repos": [{"path": "r1", "url": "x", "rev": "main"}],
            "difficulty_stratum": "unknown_future_stratum",
        }
        errors, _ = _validate_semantic_layer(data)
        stratum_errors = [e for e in errors if "difficulty_stratum" in e.field]
        assert stratum_errors == []


# ---------------------------------------------------------------------------
# Full validate_task with invalid fixture
# ---------------------------------------------------------------------------

class TestInvalidTaskFile:
    def test_invalid_fixture_has_errors(self, invalid_task_path):
        result = validate_task(str(invalid_task_path))
        assert result.valid is False
        assert len(result.errors) > 0

    def test_duplicate_checkpoint_detected(self, invalid_task_path):
        result = validate_task(str(invalid_task_path))
        dup_errors = [e for e in result.errors if "Duplicate" in e.message]
        assert len(dup_errors) >= 1

    def test_bad_repo_ref_detected(self, invalid_task_path):
        result = validate_task(str(invalid_task_path))
        repo_errors = [e for e in result.errors if "ground_truth" in e.field]
        assert len(repo_errors) >= 1

    def test_weight_sum_error_detected(self, invalid_task_path):
        result = validate_task(str(invalid_task_path))
        weight_errors = [e for e in result.errors if "weight" in e.field]
        assert len(weight_errors) >= 1


# ---------------------------------------------------------------------------
# ValidationError dataclass
# ---------------------------------------------------------------------------

class TestValidationError:
    def test_frozen(self):
        ve = ValidationError(field="f", message="m", severity="error")
        with pytest.raises((AttributeError, TypeError)):
            ve.field = "changed"  # type: ignore[misc]

    def test_severity_values(self):
        error = ValidationError(field="f", message="m", severity="error")
        warn = ValidationError(field="f", message="m", severity="warning")
        assert error.severity == "error"
        assert warn.severity == "warning"
