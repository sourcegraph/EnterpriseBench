"""Tests for eb_verify.plugins."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from eb_verify.plugins import (
    FileTooLargeError,
    MAX_ARTIFACT_BYTES,
    ValidationResult,
    get_validator,
    list_validators,
    register,
    safe_read,
)
from eb_verify.plugins.answer import (
    AnswerValidator,
    file_path_match_score,
    fuzzy_match_score,
    keyword_match_score,
    oracle_score,
    symbol_match_score,
)
from eb_verify.plugins.code_patch import (
    CodePatchValidator,
    check_diff_size,
    check_patch_applies,
)
from eb_verify.plugins.config_validator import ConfigValidator
from eb_verify.plugins.incident_report import (
    IncidentReportValidator,
    check_cross_references,
    check_sections_completeness,
    check_timeline_ordering,
)
from eb_verify.plugins.reproduction_script import ReproductionScriptValidator
from eb_verify.plugins.runbook import RunbookValidator
from eb_verify.plugins.security_assessment import SecurityAssessmentValidator


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_all_expected_types_registered(self):
        registered = list_validators()
        expected = {
            "code_patch",
            "config",
            "incident_report",
            "runbook",
            "reproduction_script",
            "security_assessment",
            "answer",
        }
        for t in expected:
            assert t in registered, f"'{t}' not in registry"

    def test_get_validator_returns_instance(self):
        for name in list_validators():
            v = get_validator(name)
            assert v is not None
            assert hasattr(v, "validate")

    def test_get_validator_unknown_returns_none(self):
        result = get_validator("totally_unknown_xyz_999")
        assert result is None

    def test_artifact_type_attribute(self):
        v = get_validator("code_patch")
        assert v.artifact_type == "code_patch"

    def test_register_custom_validator(self):
        class FakeValidator:
            artifact_type = "_test_fake_xyz"

            def validate(self, workspace: Path) -> ValidationResult:
                return ValidationResult(valid=True, detail="fake")

        fake = FakeValidator()
        register(fake)
        assert get_validator("_test_fake_xyz") is fake


# ---------------------------------------------------------------------------
# ValidationResult dataclass
# ---------------------------------------------------------------------------

class TestValidationResult:
    def test_valid_true(self):
        r = ValidationResult(valid=True)
        assert r.valid is True
        assert r.detail == ""

    def test_valid_false_with_detail(self):
        r = ValidationResult(valid=False, detail="something wrong")
        assert r.valid is False
        assert r.detail == "something wrong"


# ---------------------------------------------------------------------------
# ConfigValidator
# ---------------------------------------------------------------------------

class TestConfigValidator:
    @pytest.fixture
    def workspace(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        return ws

    def test_no_artifacts_passes(self, workspace):
        validator = ConfigValidator()
        result = validator.validate(workspace)
        assert result.valid is True
        assert "No config" in result.detail

    def test_valid_json_in_output_dir(self, workspace):
        output = workspace / "output"
        output.mkdir()
        (output / "config.json").write_text('{"key": "value"}')

        validator = ConfigValidator()
        result = validator.validate(workspace)
        assert result.valid is True

    def test_invalid_json_fails(self, workspace):
        output = workspace / "output"
        output.mkdir()
        (output / "config.json").write_text("{bad json:::}")

        validator = ConfigValidator()
        result = validator.validate(workspace)
        assert result.valid is False
        assert "config.json" in result.detail

    def test_valid_json_in_artifacts_dir(self, workspace):
        arts = workspace / "artifacts"
        arts.mkdir()
        (arts / "settings.json").write_text('{"a": 1}')

        validator = ConfigValidator()
        result = validator.validate(workspace)
        assert result.valid is True

    def test_invalid_json_at_workspace_root_is_checked(self, workspace):
        # Regression: only the first CONFIG_GLOBS pattern (**/*.yaml) used to be
        # checked at the workspace-root tier, so a malformed top-level .json
        # (or .yml/.toml/.hcl) was silently never validated.
        (workspace / "bad.json").write_text("{bad json:::}")

        validator = ConfigValidator()
        result = validator.validate(workspace)
        assert result.valid is False
        assert "bad.json" in result.detail

    def test_invalid_yaml_and_toml_at_workspace_root_are_checked(self, workspace):
        # The original bug meant only the first pattern (**/*.yaml) ever ran at
        # workspace root; cover .yml and .toml too so the fix isn't proven for
        # .json alone.
        (workspace / "bad.yml").write_text("key: [unclosed\n")
        (workspace / "bad.toml").write_bytes(b"[section\nbad toml [[[")

        validator = ConfigValidator()
        result = validator.validate(workspace)
        assert result.valid is False
        assert "bad.yml" in result.detail
        assert "bad.toml" in result.detail

    def test_valid_json_in_nested_output_subdir(self, workspace):
        # Guards the recursive/non-recursive split this fix introduces:
        # output/ and artifacts/ must keep scanning recursively, unlike the
        # workspace-root tier.
        nested = workspace / "output" / "nested" / "deep"
        nested.mkdir(parents=True)
        (nested / "config.json").write_text('{"a": 1}')

        validator = ConfigValidator()
        result = validator.validate(workspace)
        assert result.valid is True
        assert "Validated 1 config files" in result.detail

    def test_workspace_root_does_not_recurse_into_cloned_repos(self, workspace):
        # EnterpriseBench sandboxes clone full OSS repos as direct children of
        # workspace (see docs/TASK_AUTHORING_GUIDE.md). The workspace-root tier
        # must only check files directly in workspace, not walk into those
        # repos — otherwise a cloned repo's own malformed test fixtures would
        # cause spurious validation failures unrelated to the agent's work.
        nested = workspace / "some-cloned-repo" / "testdata"
        nested.mkdir(parents=True)
        (nested / "bad.json").write_text("{intentionally malformed for repo's own tests")

        validator = ConfigValidator()
        result = validator.validate(workspace)
        assert result.valid is True
        assert "No config" in result.detail

    def test_nonexistent_workspace(self, tmp_path):
        validator = ConfigValidator()
        result = validator.validate(tmp_path / "nonexistent")
        # No dirs found → no files → passes as "no artifacts"
        assert result.valid is True

    def test_validate_file_valid_json(self, tmp_path):
        f = tmp_path / "good.json"
        f.write_text('{"x": 1}')
        validator = ConfigValidator()
        result = validator._validate_file(f, tmp_path)
        assert result.valid is True

    def test_validate_file_invalid_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{invalid}")
        validator = ConfigValidator()
        result = validator._validate_file(f, tmp_path)
        assert result.valid is False

    def test_validate_file_valid_toml(self, tmp_path):
        f = tmp_path / "good.toml"
        f.write_text('[section]\nkey = "value"\n')
        validator = ConfigValidator()
        result = validator._validate_file(f, tmp_path)
        assert result.valid is True

    def test_validate_file_invalid_toml(self, tmp_path):
        f = tmp_path / "bad.toml"
        f.write_bytes(b"[section\nbad toml [[[")
        validator = ConfigValidator()
        result = validator._validate_file(f, tmp_path)
        # If toml parser available, should fail; otherwise passes (skip)
        # Either outcome is valid — we just assert no exception is raised
        assert isinstance(result.valid, bool)


# ---------------------------------------------------------------------------
# AnswerValidator
# ---------------------------------------------------------------------------

class TestAnswerValidator:
    def test_no_answer_file_fails(self, tmp_path):
        validator = AnswerValidator()
        result = validator.validate(tmp_path)
        assert result.valid is False
        assert "No answer file" in result.detail

    def test_valid_answer_json(self, tmp_path):
        (tmp_path / "answer.json").write_text('{"answer": "foo"}')
        validator = AnswerValidator()
        result = validator.validate(tmp_path)
        assert result.valid is True
        assert "answer.json" in result.detail

    def test_invalid_answer_json_syntax(self, tmp_path):
        (tmp_path / "answer.json").write_text("{invalid json")
        validator = AnswerValidator()
        result = validator.validate(tmp_path)
        assert result.valid is False

    def test_answer_json_not_object_fails(self, tmp_path):
        (tmp_path / "answer.json").write_text("[1, 2, 3]")
        validator = AnswerValidator()
        result = validator.validate(tmp_path)
        assert result.valid is False
        assert "JSON object" in result.detail

    def test_valid_answer_txt(self, tmp_path):
        (tmp_path / "answer.txt").write_text("The answer is 42.")
        validator = AnswerValidator()
        result = validator.validate(tmp_path)
        assert result.valid is True
        assert "answer.txt" in result.detail

    def test_empty_answer_txt_fails(self, tmp_path):
        (tmp_path / "answer.txt").write_text("")
        validator = AnswerValidator()
        result = validator.validate(tmp_path)
        assert result.valid is False
        assert "empty" in result.detail

    def test_json_takes_precedence_over_txt(self, tmp_path):
        (tmp_path / "answer.json").write_text('{"a": 1}')
        (tmp_path / "answer.txt").write_text("some text")
        validator = AnswerValidator()
        result = validator.validate(tmp_path)
        assert "answer.json" in result.detail

    def test_answer_json_in_subdir(self, tmp_path):
        subdir = tmp_path / "output"
        subdir.mkdir()
        (subdir / "answer.json").write_text('{"result": "ok"}')
        validator = AnswerValidator()
        result = validator.validate(tmp_path)
        assert result.valid is True

    def test_oversized_answer_json_fails(self, tmp_path):
        (tmp_path / "answer.json").write_text(
            json.dumps({"answer": "x" * (MAX_ARTIFACT_BYTES + 1)})
        )
        validator = AnswerValidator()
        result = validator.validate(tmp_path)
        assert result.valid is False
        assert "too large" in result.detail.lower()

    def test_oversized_answer_txt_fails(self, tmp_path):
        (tmp_path / "answer.txt").write_text("x" * (MAX_ARTIFACT_BYTES + 1))
        validator = AnswerValidator()
        result = validator.validate(tmp_path)
        assert result.valid is False
        assert "too large" in result.detail.lower()


# ---------------------------------------------------------------------------
# CodePatchValidator
# ---------------------------------------------------------------------------

class TestCodePatchValidator:
    def test_nonexistent_workspace_fails(self, tmp_path):
        validator = CodePatchValidator()
        result = validator.validate(tmp_path / "nonexistent")
        assert result.valid is False
        assert "Workspace not found" in result.detail

    def test_workspace_no_repos_fails(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        validator = CodePatchValidator()
        result = validator.validate(workspace)
        assert result.valid is False
        assert "No code changes" in result.detail

    def test_workspace_with_non_git_dirs_fails(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "not_a_repo").mkdir()
        validator = CodePatchValidator()
        result = validator.validate(workspace)
        assert result.valid is False


# ---------------------------------------------------------------------------
# IncidentReportValidator
# ---------------------------------------------------------------------------

class TestIncidentReportValidator:
    def test_artifact_type(self):
        v = IncidentReportValidator()
        assert v.artifact_type == "incident_report"

    def test_validate_returns_validation_result(self, tmp_path):
        v = IncidentReportValidator()
        result = v.validate(tmp_path)
        assert isinstance(result, ValidationResult)
        assert isinstance(result.valid, bool)


# ---------------------------------------------------------------------------
# RunbookValidator
# ---------------------------------------------------------------------------

class TestRunbookValidator:
    def test_artifact_type(self):
        v = RunbookValidator()
        assert v.artifact_type == "runbook"

    def test_validate_returns_validation_result(self, tmp_path):
        v = RunbookValidator()
        result = v.validate(tmp_path)
        assert isinstance(result, ValidationResult)


# ---------------------------------------------------------------------------
# ReproductionScriptValidator
# ---------------------------------------------------------------------------

class TestReproductionScriptValidator:
    def test_artifact_type(self):
        v = ReproductionScriptValidator()
        assert v.artifact_type == "reproduction_script"

    def test_validate_returns_validation_result(self, tmp_path):
        v = ReproductionScriptValidator()
        result = v.validate(tmp_path)
        assert isinstance(result, ValidationResult)


# ---------------------------------------------------------------------------
# SecurityAssessmentValidator
# ---------------------------------------------------------------------------

class TestSecurityAssessmentValidator:
    def test_artifact_type(self):
        v = SecurityAssessmentValidator()
        assert v.artifact_type == "security_assessment"

    def test_no_file_fails(self, tmp_path):
        v = SecurityAssessmentValidator()
        result = v.validate(tmp_path)
        assert result.valid is False
        assert "No security assessment" in result.detail

    def test_valid_assessment(self, tmp_path):
        data = {
            "vulnerabilities": [{"id": "CVE-2024-001", "severity": "high"}],
            "severity_summary": {"high": 1},
            "recommendations": ["patch immediately"],
        }
        (tmp_path / "security_assessment.json").write_text(json.dumps(data))
        v = SecurityAssessmentValidator()
        result = v.validate(tmp_path)
        assert result.valid is True

    def test_missing_required_field(self, tmp_path):
        data = {"vulnerabilities": [], "severity_summary": {}}
        (tmp_path / "security_assessment.json").write_text(json.dumps(data))
        v = SecurityAssessmentValidator()
        result = v.validate(tmp_path)
        assert result.valid is False
        assert "recommendations" in result.detail

    def test_invalid_json(self, tmp_path):
        (tmp_path / "security_assessment.json").write_text("{bad json")
        v = SecurityAssessmentValidator()
        result = v.validate(tmp_path)
        assert result.valid is False
        assert "Invalid JSON" in result.detail

    def test_non_object_json_fails_cleanly(self, tmp_path):
        # A scalar/array at the top level must be an explicit failure, not a
        # TypeError from the required-fields membership check.
        (tmp_path / "security_assessment.json").write_text("42")
        v = SecurityAssessmentValidator()
        result = v.validate(tmp_path)
        assert result.valid is False
        assert "JSON object" in result.detail

    def test_hyphen_filename_found(self, tmp_path):
        data = {
            "vulnerabilities": [],
            "severity_summary": {},
            "recommendations": [],
        }
        (tmp_path / "security-assessment.json").write_text(json.dumps(data))
        v = SecurityAssessmentValidator()
        result = v.validate(tmp_path)
        assert result.valid is True


# ---------------------------------------------------------------------------
# IncidentReportValidator (detailed)
# ---------------------------------------------------------------------------

class TestIncidentReportValidatorDetailed:
    def test_valid_report(self, tmp_path):
        data = {
            "timeline": [{"time": "00:00", "event": "alert fired"}],
            "root_cause": "Memory leak in service X",
            "remediation": "Restart service X",
            "affected_services": ["service-x", "service-y"],
        }
        (tmp_path / "incident_report.json").write_text(json.dumps(data))
        v = IncidentReportValidator()
        result = v.validate(tmp_path)
        assert result.valid is True
        assert "incident_report.json" in result.detail

    def test_no_file_fails(self, tmp_path):
        v = IncidentReportValidator()
        result = v.validate(tmp_path)
        assert result.valid is False
        assert "No incident report" in result.detail

    def test_missing_required_fields(self, tmp_path):
        data = {"timeline": [], "root_cause": "x"}
        (tmp_path / "incident_report.json").write_text(json.dumps(data))
        v = IncidentReportValidator()
        result = v.validate(tmp_path)
        assert result.valid is False
        assert "Missing required fields" in result.detail

    def test_invalid_json(self, tmp_path):
        (tmp_path / "incident_report.json").write_text("{not valid")
        v = IncidentReportValidator()
        result = v.validate(tmp_path)
        assert result.valid is False
        assert "Invalid JSON" in result.detail

    def test_not_dict_fails(self, tmp_path):
        (tmp_path / "incident_report.json").write_text("[1, 2, 3]")
        v = IncidentReportValidator()
        result = v.validate(tmp_path)
        assert result.valid is False
        assert "JSON object" in result.detail

    def test_oversized_incident_report_fails(self, tmp_path):
        data = {
            "timeline": [{"time": "00:00", "event": "alert fired"}],
            "root_cause": "x" * (MAX_ARTIFACT_BYTES + 1),
            "remediation": "Restart service X",
            "affected_services": ["service-x"],
        }
        (tmp_path / "incident_report.json").write_text(json.dumps(data))
        v = IncidentReportValidator()
        result = v.validate(tmp_path)
        assert result.valid is False
        assert "too large" in result.detail.lower()

    def test_timeline_not_list_fails(self, tmp_path):
        data = {
            "timeline": "not a list",
            "root_cause": "x",
            "remediation": "y",
            "affected_services": [],
        }
        (tmp_path / "incident_report.json").write_text(json.dumps(data))
        v = IncidentReportValidator()
        result = v.validate(tmp_path)
        assert result.valid is False
        assert "timeline" in result.detail

    def test_affected_services_not_list_fails(self, tmp_path):
        data = {
            "timeline": [],
            "root_cause": "x",
            "remediation": "y",
            "affected_services": "not a list",
        }
        (tmp_path / "incident_report.json").write_text(json.dumps(data))
        v = IncidentReportValidator()
        result = v.validate(tmp_path)
        assert result.valid is False

    def test_hyphen_filename_found(self, tmp_path):
        data = {
            "timeline": [],
            "root_cause": "x",
            "remediation": "y",
            "affected_services": [],
        }
        (tmp_path / "incident-report.json").write_text(json.dumps(data))
        v = IncidentReportValidator()
        result = v.validate(tmp_path)
        assert result.valid is True


# ---------------------------------------------------------------------------
# RunbookValidator (detailed)
# ---------------------------------------------------------------------------

class TestRunbookValidatorDetailed:
    def test_valid_runbook(self, tmp_path):
        content = "# Overview\nSome overview.\n\n# Steps\n1. Do this.\n\n# Rollback\nRevert.\n"
        (tmp_path / "runbook.md").write_text(content)
        v = RunbookValidator()
        result = v.validate(tmp_path)
        assert result.valid is True
        assert "required sections" in result.detail

    def test_no_file_fails(self, tmp_path):
        v = RunbookValidator()
        result = v.validate(tmp_path)
        assert result.valid is False
        assert "No runbook" in result.detail

    def test_missing_section(self, tmp_path):
        content = "# Overview\nSome overview.\n\n# Steps\n1. Do this.\n"
        (tmp_path / "runbook.md").write_text(content)
        v = RunbookValidator()
        result = v.validate(tmp_path)
        assert result.valid is False
        assert "rollback" in result.detail.lower()

    def test_uppercase_runbook_found(self, tmp_path):
        content = "# Overview\nX\n# Steps\nY\n# Rollback\nZ\n"
        (tmp_path / "RUNBOOK.md").write_text(content)
        v = RunbookValidator()
        result = v.validate(tmp_path)
        assert result.valid is True


# ---------------------------------------------------------------------------
# ReproductionScriptValidator (detailed)
# ---------------------------------------------------------------------------

class TestReproductionScriptValidatorDetailed:
    def test_no_script_fails(self, tmp_path):
        v = ReproductionScriptValidator()
        result = v.validate(tmp_path)
        assert result.valid is False
        assert "No reproduction script" in result.detail

    def test_non_executable_script_fails(self, tmp_path):
        script = tmp_path / "reproduce.sh"
        script.write_text("#!/bin/bash\necho hi\n")
        # Remove execute permission
        script.chmod(0o644)
        v = ReproductionScriptValidator()
        result = v.validate(tmp_path)
        assert result.valid is False
        assert "not executable" in result.detail

    def test_executable_script_passes(self, tmp_path):
        import stat as _stat
        script = tmp_path / "reproduce.sh"
        script.write_text("#!/bin/bash\necho hi\n")
        script.chmod(script.stat().st_mode | _stat.S_IEXEC)
        v = ReproductionScriptValidator()
        result = v.validate(tmp_path)
        assert result.valid is True
        assert "reproduce.sh" in result.detail

    def test_repro_prefix_found(self, tmp_path):
        import stat as _stat
        script = tmp_path / "repro.py"
        script.write_text("print('hi')")
        script.chmod(script.stat().st_mode | _stat.S_IEXEC)
        v = ReproductionScriptValidator()
        result = v.validate(tmp_path)
        assert result.valid is True


# ---------------------------------------------------------------------------
# Answer Oracle Matching (new hardening)
# ---------------------------------------------------------------------------

class TestAnswerOracleMatching:
    """Tests for oracle matching functions in the answer validator."""

    def test_keyword_match_all_present(self):
        text = "The memory leak was caused by a missing close() call in the connection pool."
        keywords = ["memory leak", "close()", "connection pool"]
        assert keyword_match_score(text, keywords) == 1.0

    def test_keyword_match_partial(self):
        text = "The memory leak was caused by something."
        keywords = ["memory leak", "close()", "connection pool"]
        score = keyword_match_score(text, keywords)
        assert abs(score - 1 / 3) < 0.01

    def test_keyword_match_none(self):
        text = "Everything is fine."
        keywords = ["memory leak", "close()"]
        assert keyword_match_score(text, keywords) == 0.0

    def test_keyword_match_empty_keywords(self):
        assert keyword_match_score("anything", []) == 1.0

    def test_keyword_match_case_insensitive(self):
        text = "The MEMORY LEAK was found."
        keywords = ["memory leak"]
        assert keyword_match_score(text, keywords) == 1.0

    def test_symbol_match_all_present(self):
        text = "The function handleRequest in class ConnectionPool was the issue."
        symbols = ["handleRequest", "ConnectionPool"]
        assert symbol_match_score(text, symbols) == 1.0

    def test_symbol_match_partial(self):
        text = "The function handleRequest was the issue."
        symbols = ["handleRequest", "ConnectionPool"]
        assert symbol_match_score(text, symbols) == 0.5

    def test_symbol_match_no_partial_word(self):
        """Symbol should match as whole word, not substring."""
        text = "The handleRequestExtra function was fine."
        symbols = ["handleRequest"]
        # Should NOT match because it's a prefix, not exact word
        # Actually re \b will match at the boundary between 't' and 'E'
        # since they are both word chars, \b won't match there... let me check
        # \bhandleRequest\b matches handleRequest but not handleRequestExtra
        assert symbol_match_score(text, symbols) == 0.0

    def test_symbol_match_empty(self):
        assert symbol_match_score("anything", []) == 1.0

    def test_file_path_match_full_path(self):
        text = "The bug is in src/server/handler.go"
        files = ["src/server/handler.go"]
        assert file_path_match_score(text, files) == 1.0

    def test_file_path_match_basename_only(self):
        text = "Check handler.go for the issue."
        files = ["src/server/handler.go"]
        assert file_path_match_score(text, files) == 1.0

    def test_file_path_match_none(self):
        text = "The issue is somewhere in the codebase."
        files = ["src/server/handler.go"]
        assert file_path_match_score(text, files) == 0.0

    def test_file_path_match_empty(self):
        assert file_path_match_score("anything", []) == 1.0

    def test_fuzzy_match_identical(self):
        text = "The root cause is a memory leak in the connection pool."
        assert fuzzy_match_score(text, text) == pytest.approx(1.0)

    def test_fuzzy_match_similar(self):
        text = "The root cause is a memory leak in the connection pool."
        expected = "Root cause: memory leak in connection pool"
        score = fuzzy_match_score(text, expected)
        assert score > 0.6

    def test_fuzzy_match_below_threshold(self):
        text = "Everything is fine and working well."
        expected = "The root cause is a memory leak."
        score = fuzzy_match_score(text, expected, threshold=0.6)
        assert score == 0.0

    def test_fuzzy_match_custom_threshold(self):
        text = "memory leak found"
        expected = "memory leak detected"
        score = fuzzy_match_score(text, expected, threshold=0.5)
        assert score > 0.5

    def test_oracle_score_all_dimensions(self):
        text = "The handleRequest function in src/server/handler.go has a memory leak in the connection pool."
        ground_truth = {
            "keywords": ["memory leak", "connection pool"],
            "symbols": ["handleRequest"],
            "expected_files": ["src/server/handler.go"],
            "expected_answer": "memory leak in the connection pool in handleRequest",
        }
        score, dims = oracle_score(text, ground_truth)
        assert score > 0.5
        assert "keyword" in dims
        assert "symbol" in dims
        assert "file_path" in dims
        assert "fuzzy" in dims

    def test_oracle_score_no_ground_truth(self):
        score, dims = oracle_score("anything", {})
        assert score == 1.0
        assert dims == {}

    def test_oracle_score_keywords_only(self):
        text = "The issue is a deadlock."
        gt = {"keywords": ["deadlock"]}
        score, dims = oracle_score(text, gt)
        assert dims["keyword"] == 1.0
        assert score == 1.0

    def test_answer_validator_with_oracle(self, tmp_path):
        data = {"answer": "The bug is in handleRequest in src/server/handler.go causing a memory leak."}
        (tmp_path / "answer.json").write_text(json.dumps(data))
        v = AnswerValidator()
        gt = {
            "keywords": ["memory leak"],
            "symbols": ["handleRequest"],
            "expected_files": ["src/server/handler.go"],
        }
        result = v.validate(tmp_path, ground_truth=gt)
        assert result.valid is True
        assert "oracle_score" in result.detail

    def test_answer_validator_oracle_low_score(self, tmp_path):
        data = {"answer": "I don't know."}
        (tmp_path / "answer.json").write_text(json.dumps(data))
        v = AnswerValidator()
        gt = {
            "keywords": ["memory leak", "connection pool", "deadlock"],
            "symbols": ["handleRequest"],
            "expected_files": ["src/server/handler.go"],
        }
        result = v.validate(tmp_path, ground_truth=gt, thresholds={"min_score": 0.3})
        assert result.valid is False
        assert "oracle_score" in result.detail

    def test_answer_validator_without_oracle_backward_compat(self, tmp_path):
        """Ensure validator works without oracle (backward compatible)."""
        (tmp_path / "answer.json").write_text('{"answer": "foo"}')
        v = AnswerValidator()
        result = v.validate(tmp_path)
        assert result.valid is True
        assert "found and valid" in result.detail

    def test_answer_validator_txt_with_oracle(self, tmp_path):
        (tmp_path / "answer.txt").write_text("The memory leak is in handleRequest")
        v = AnswerValidator()
        gt = {"keywords": ["memory leak"], "symbols": ["handleRequest"]}
        result = v.validate(tmp_path, ground_truth=gt)
        assert result.valid is True
        assert "oracle_score" in result.detail


# ---------------------------------------------------------------------------
# Code Patch Hardening (new)
# ---------------------------------------------------------------------------

class TestCodePatchHardening:
    """Tests for new code_patch validator features."""

    def test_check_patch_applies_on_git_repo(self, tmp_path):
        """Test git apply --check on a repo with changes."""
        import subprocess
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(repo), capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(repo), capture_output=True,
        )
        (repo / "file.txt").write_text("hello\n")
        subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True)

        # Make a change
        (repo / "file.txt").write_text("hello world\n")
        applies, detail = check_patch_applies(repo)
        # The diff is against HEAD, and git apply --check on the same tree
        # should work fine for a valid diff
        assert isinstance(applies, bool)
        assert isinstance(detail, str)

    def test_check_diff_size_reasonable(self, tmp_path):
        """Test diff size checker on a repo with a small change."""
        import subprocess
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(repo), capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(repo), capture_output=True,
        )
        (repo / "file.txt").write_text("hello\n")
        subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True)

        # Small change
        (repo / "file.txt").write_text("hello world\n")
        ok, detail = check_diff_size(repo, max_lines=10_000, min_lines=1)
        assert ok is True
        assert "OK" in detail

    def test_check_diff_size_too_large(self, tmp_path):
        """Flag a suspiciously large diff."""
        import subprocess
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(repo), capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(repo), capture_output=True,
        )
        (repo / "file.txt").write_text("hello\n")
        subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True)

        # Large change
        (repo / "file.txt").write_text("\n".join(f"line {i}" for i in range(500)) + "\n")
        ok, detail = check_diff_size(repo, max_lines=10, min_lines=1)
        assert ok is False
        assert "large" in detail

    def test_validate_with_check_applies(self, tmp_path):
        """Test the full validator with check_applies=True."""
        import subprocess
        workspace = tmp_path / "ws"
        workspace.mkdir()
        repo = workspace / "myrepo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(repo), capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(repo), capture_output=True,
        )
        (repo / "file.txt").write_text("hello\n")
        subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True)

        (repo / "file.txt").write_text("hello world\n")

        v = CodePatchValidator()
        result = v.validate(workspace, check_applies=True)
        assert result.valid is True
        assert "myrepo" in result.detail

    def test_validate_backward_compat(self, tmp_path):
        """Validator still works with no extra args."""
        v = CodePatchValidator()
        result = v.validate(tmp_path / "nonexistent")
        assert result.valid is False


# ---------------------------------------------------------------------------
# Incident Report Semantic Checks (new)
# ---------------------------------------------------------------------------

class TestIncidentReportSemantics:
    """Tests for new semantic checks in incident_report validator."""

    def test_sections_completeness_all_filled(self):
        data = {
            "timeline": [{"time": "00:00", "event": "alert"}],
            "root_cause": "Memory leak",
            "remediation": "Restart service",
        }
        issues = check_sections_completeness(data)
        assert issues == []

    def test_sections_completeness_empty_root_cause(self):
        data = {
            "timeline": [{"time": "00:00", "event": "alert"}],
            "root_cause": "",
            "remediation": "Restart service",
        }
        issues = check_sections_completeness(data)
        assert len(issues) == 1
        assert "root_cause" in issues[0]

    def test_sections_completeness_empty_timeline(self):
        data = {
            "timeline": [],
            "root_cause": "Something",
            "remediation": "Fix it",
        }
        issues = check_sections_completeness(data)
        assert len(issues) == 1
        assert "timeline" in issues[0]

    def test_timeline_ordering_chronological(self):
        timeline = [
            {"time": "2024-01-01T00:00:00", "event": "first"},
            {"time": "2024-01-01T01:00:00", "event": "second"},
            {"time": "2024-01-01T02:00:00", "event": "third"},
        ]
        issues = check_timeline_ordering(timeline)
        assert issues == []

    def test_timeline_ordering_out_of_order(self):
        timeline = [
            {"time": "2024-01-01T02:00:00", "event": "third"},
            {"time": "2024-01-01T00:00:00", "event": "first"},
        ]
        issues = check_timeline_ordering(timeline)
        assert len(issues) == 1
        assert "before" in issues[0]

    def test_timeline_ordering_short_format(self):
        timeline = [
            {"time": "10:00", "event": "first"},
            {"time": "11:00", "event": "second"},
        ]
        issues = check_timeline_ordering(timeline)
        assert issues == []

    def test_timeline_ordering_no_timestamps(self):
        timeline = [
            {"event": "first"},
            {"event": "second"},
        ]
        issues = check_timeline_ordering(timeline)
        assert issues == []

    def test_timeline_ordering_single_event(self):
        timeline = [{"time": "10:00", "event": "only"}]
        issues = check_timeline_ordering(timeline)
        assert issues == []

    def test_cross_references_valid_services(self):
        data = {"affected_services": ["api-gateway", "user-service"]}
        known = ["api-gateway", "user-service", "payment-service"]
        issues = check_cross_references(data, known_services=known)
        assert issues == []

    def test_cross_references_unknown_service(self):
        data = {"affected_services": ["api-gateway", "ghost-service"]}
        known = ["api-gateway", "user-service"]
        issues = check_cross_references(data, known_services=known)
        assert len(issues) == 1
        assert "ghost-service" in issues[0]

    def test_cross_references_no_known_services(self):
        """When no known_services provided, skip check."""
        data = {"affected_services": ["anything"]}
        issues = check_cross_references(data)
        assert issues == []

    def test_cross_references_file_in_root_cause(self):
        data = {
            "affected_services": [],
            "root_cause": "Bug in src/handler.go caused the crash",
        }
        known_files = ["src/handler.go", "src/main.go"]
        issues = check_cross_references(data, known_files=known_files)
        assert issues == []

    def test_cross_references_unknown_file_in_root_cause(self):
        data = {
            "affected_services": [],
            "root_cause": "Bug in nonexistent/file.py caused the crash",
        }
        known_files = ["src/handler.go"]
        issues = check_cross_references(data, known_files=known_files)
        assert len(issues) >= 1
        assert "nonexistent/file.py" in issues[0]

    def test_validator_warns_on_empty_sections(self, tmp_path):
        """Validator should still pass but include warnings for empty sections."""
        data = {
            "timeline": [],
            "root_cause": "Something",
            "remediation": "Fix it",
            "affected_services": ["svc-a"],
        }
        (tmp_path / "incident_report.json").write_text(json.dumps(data))
        v = IncidentReportValidator()
        result = v.validate(tmp_path)
        assert result.valid is True
        assert "WARNINGS" in result.detail
        assert "timeline" in result.detail

    def test_validator_warns_on_out_of_order_timeline(self, tmp_path):
        data = {
            "timeline": [
                {"time": "2024-01-01T10:00:00", "event": "second"},
                {"time": "2024-01-01T08:00:00", "event": "first"},
            ],
            "root_cause": "Something",
            "remediation": "Fix it",
            "affected_services": ["svc-a"],
        }
        (tmp_path / "incident_report.json").write_text(json.dumps(data))
        v = IncidentReportValidator()
        result = v.validate(tmp_path)
        assert result.valid is True
        assert "WARNINGS" in result.detail
        assert "before" in result.detail

    def test_validator_warns_on_unknown_services(self, tmp_path):
        data = {
            "timeline": [{"time": "10:00", "event": "alert"}],
            "root_cause": "Something",
            "remediation": "Fix it",
            "affected_services": ["ghost-svc"],
        }
        (tmp_path / "incident_report.json").write_text(json.dumps(data))
        v = IncidentReportValidator()
        result = v.validate(tmp_path, known_services=["real-svc"])
        assert result.valid is True
        assert "WARNINGS" in result.detail
        assert "ghost-svc" in result.detail

    def test_validator_backward_compat(self, tmp_path):
        """Existing valid report should still pass without extra args."""
        data = {
            "timeline": [{"time": "00:00", "event": "alert fired"}],
            "root_cause": "Memory leak in service X",
            "remediation": "Restart service X",
            "affected_services": ["service-x"],
        }
        (tmp_path / "incident_report.json").write_text(json.dumps(data))
        v = IncidentReportValidator()
        result = v.validate(tmp_path)
        assert result.valid is True
        assert "incident_report.json" in result.detail


# ---------------------------------------------------------------------------
# safe_read max_bytes guard
# ---------------------------------------------------------------------------


class TestSafeReadMaxBytes:
    def test_within_limit_reads_normally(self, tmp_path):
        f = tmp_path / "small.txt"
        f.write_text("hello")
        assert safe_read(f, tmp_path, max_bytes=1024) == "hello"

    def test_at_exact_limit_reads(self, tmp_path):
        f = tmp_path / "exact.txt"
        f.write_text("12345")
        assert safe_read(f, tmp_path, max_bytes=5) == "12345"

    def test_over_limit_raises_file_too_large(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("123456")
        with pytest.raises(FileTooLargeError):
            safe_read(f, tmp_path, max_bytes=5)

    def test_file_too_large_is_a_value_error(self):
        assert issubclass(FileTooLargeError, ValueError)

    def test_default_none_is_unlimited(self, tmp_path):
        f = tmp_path / "any.txt"
        f.write_text("x" * 4096)
        assert len(safe_read(f, tmp_path)) == 4096

    def test_containment_checked_before_size(self, tmp_path):
        # A path escaping the workspace must raise the escape error, never
        # FileTooLargeError — the file outside must not be stat'd for size.
        ws = tmp_path / "ws"
        ws.mkdir()
        outside = tmp_path / "huge_outside.txt"
        outside.write_text("x" * 100)
        with pytest.raises(ValueError, match="escapes workspace") as exc_info:
            safe_read(ws / ".." / "huge_outside.txt", ws, max_bytes=5)
        assert not isinstance(exc_info.value, FileTooLargeError)

    def test_swap_after_open_returns_original_fd_content(self, tmp_path, monkeypatch):
        # Regression test for the TOCTOU window between the containment check
        # and the read: safe_read must read from the fd it already opened,
        # not re-open by path, so re-pointing the *path* at a different file
        # after the open (simulated here via the realpath call safe_read
        # makes right after opening) must not affect the returned content.
        # In-place mutation of the same inode (e.g. write_text on the same
        # path) doesn't model this attack — an already-open fd sees that
        # kind of change too. The attack is swapping the directory entry
        # (unlink + symlink, as done here, or a rename) to a different inode.
        ws = tmp_path / "ws"
        ws.mkdir()
        target = ws / "artifact.json"
        target.write_text("original-content")
        swapped = ws / "swapped.json"
        swapped.write_text("swapped-content-attacker-controlled")

        real_realpath = os.path.realpath

        def swap_after_open(path_arg, *args, **kwargs):
            result = real_realpath(path_arg, *args, **kwargs)
            # Only the fd-derived realpath call (made after os.open already
            # has the original file's fd) simulates the attacker's swap —
            # not the earlier workspace.resolve() call, which happens before
            # the target is even opened.
            if str(path_arg).startswith("/proc/self/fd/"):
                target.unlink()
                target.symlink_to(swapped)
            return result

        monkeypatch.setattr(os.path, "realpath", swap_after_open)
        assert safe_read(target, ws) == "original-content"


# ---------------------------------------------------------------------------
# AnswerValidator grounded-citations gate
# ---------------------------------------------------------------------------

# Verbatim source line, comfortably over the 20-normalized-char minimum.
CITED_SPAN = "raise RemoteProtocolError(msg) from exc  # translate h11 event"


@pytest.fixture
def cited_ws(tmp_path):
    """Workspace with one repo-style source file to cite against."""
    ws = tmp_path / "ws"
    src = ws / "httpcore" / "core" / "http11.py"
    src.parent.mkdir(parents=True)
    src.write_text(f"# connection layer\n{CITED_SPAN}\n")
    return ws


def _write_answer(ws, citations=None, extra=None):
    data = {"source_files": [{"path": "httpcore/core/http11.py"}]}
    if citations is not None:
        data["citations"] = citations
    if extra:
        data.update(extra)
    (ws / "answer.json").write_text(json.dumps(data))


class TestAnswerGroundedCitations:
    def test_grounded_citations_pass(self, cited_ws):
        _write_answer(
            cited_ws,
            citations=[
                {"repo": "httpcore", "file": "core/http11.py", "evidence_span": CITED_SPAN}
            ],
        )
        result = AnswerValidator().validate(cited_ws, require_grounded_citations=True)
        assert result.valid is True, result.detail

    def test_fabricated_citation_fails_with_reason(self, cited_ws):
        _write_answer(
            cited_ws,
            citations=[
                {
                    "repo": "httpcore",
                    "file": "core/http11.py",
                    "evidence_span": "this span was fabricated and appears nowhere",
                }
            ],
        )
        result = AnswerValidator().validate(cited_ws, require_grounded_citations=True)
        assert result.valid is False
        assert "span_not_found" in result.detail
        assert "core/http11.py" in result.detail

    def test_missing_citations_field_fails_when_required(self, cited_ws):
        _write_answer(cited_ws, citations=None)
        result = AnswerValidator().validate(cited_ws, require_grounded_citations=True)
        assert result.valid is False
        assert "citations" in result.detail

    def test_malformed_citation_entry_fails_with_explicit_issue(self, cited_ws):
        _write_answer(cited_ws, citations=[{"file": "core/http11.py"}])
        result = AnswerValidator().validate(cited_ws, require_grounded_citations=True)
        assert result.valid is False
        assert "evidence_span" in result.detail

    def test_citations_not_a_list_fails(self, cited_ws):
        _write_answer(cited_ws, citations="see core/http11.py")
        result = AnswerValidator().validate(cited_ws, require_grounded_citations=True)
        assert result.valid is False
        assert "list" in result.detail

    def test_answer_txt_cannot_satisfy_gate(self, cited_ws):
        (cited_ws / "answer.txt").write_text("the error comes from http11.py")
        result = AnswerValidator().validate(cited_ws, require_grounded_citations=True)
        assert result.valid is False
        assert "answer.json" in result.detail

    def test_gate_runs_before_oracle_scoring(self, cited_ws):
        # Even with a ground_truth oracle that would score well, fabricated
        # citations must fail the artifact when the gate is on.
        _write_answer(
            cited_ws,
            citations=[
                {
                    "repo": "httpcore",
                    "file": "core/http11.py",
                    "evidence_span": "fabricated span long enough to be checked",
                }
            ],
        )
        result = AnswerValidator().validate(
            cited_ws,
            ground_truth={"expected_files": ["httpcore/core/http11.py"]},
            require_grounded_citations=True,
        )
        assert result.valid is False
        assert "span_not_found" in result.detail

    def test_default_off_ignores_fabricated_citations(self, cited_ws):
        """Without the flag, behavior is unchanged (least-invasive integration)."""
        _write_answer(
            cited_ws,
            citations=[
                {
                    "repo": "httpcore",
                    "file": "core/http11.py",
                    "evidence_span": "fabricated but ignored because the gate is off",
                }
            ],
        )
        result = AnswerValidator().validate(cited_ws)
        assert result.valid is True


# ---------------------------------------------------------------------------
# SecurityAssessmentValidator grounded-citations gate (per-finding)
# ---------------------------------------------------------------------------


def _write_assessment(ws, vulnerabilities):
    data = {
        "vulnerabilities": vulnerabilities,
        "severity_summary": {"high": len(vulnerabilities)},
        "recommendations": ["patch the parser"],
    }
    (ws / "security_assessment.json").write_text(json.dumps(data))


def _finding(citations=..., **extra):
    finding = {"id": "VULN-1", "severity": "high", "description": "header parser flaw"}
    if citations is not ...:
        finding["citations"] = citations
    finding.update(extra)
    return finding


class TestSecurityAssessmentGroundedCitations:
    def test_grounded_per_finding_citations_pass(self, cited_ws):
        _write_assessment(
            cited_ws,
            [
                _finding(
                    citations=[
                        {
                            "repo": "httpcore",
                            "file": "core/http11.py",
                            "evidence_span": CITED_SPAN,
                        }
                    ]
                )
            ],
        )
        result = SecurityAssessmentValidator().validate(
            cited_ws, require_grounded_citations=True
        )
        assert result.valid is True, result.detail

    def test_fabricated_finding_citation_fails_with_finding_index(self, cited_ws):
        _write_assessment(
            cited_ws,
            [
                _finding(
                    citations=[
                        {
                            "repo": "httpcore",
                            "file": "core/http11.py",
                            "evidence_span": "fabricated evidence that appears nowhere",
                        }
                    ]
                )
            ],
        )
        result = SecurityAssessmentValidator().validate(
            cited_ws, require_grounded_citations=True
        )
        assert result.valid is False
        assert "vulnerabilities[0]" in result.detail
        assert "span_not_found" in result.detail

    def test_finding_missing_citations_fails(self, cited_ws):
        _write_assessment(cited_ws, [_finding()])
        result = SecurityAssessmentValidator().validate(
            cited_ws, require_grounded_citations=True
        )
        assert result.valid is False
        assert "vulnerabilities[0]" in result.detail
        assert "citations" in result.detail

    def test_finding_empty_citations_fails(self, cited_ws):
        # Per-finding gate: the finding IS the claim, so an empty citations
        # list (claim with zero evidence) must not pass.
        _write_assessment(cited_ws, [_finding(citations=[])])
        result = SecurityAssessmentValidator().validate(
            cited_ws, require_grounded_citations=True
        )
        assert result.valid is False
        assert "vulnerabilities[0]" in result.detail

    def test_malformed_finding_citation_fails_with_explicit_issue(self, cited_ws):
        _write_assessment(
            cited_ws, [_finding(citations=[{"file": "core/http11.py"}])]
        )
        result = SecurityAssessmentValidator().validate(
            cited_ws, require_grounded_citations=True
        )
        assert result.valid is False
        assert "vulnerabilities[0]" in result.detail
        assert "evidence_span" in result.detail

    def test_non_object_finding_fails(self, cited_ws):
        _write_assessment(cited_ws, ["sql injection in login form"])
        result = SecurityAssessmentValidator().validate(
            cited_ws, require_grounded_citations=True
        )
        assert result.valid is False
        assert "vulnerabilities[0]" in result.detail

    def test_vulnerabilities_not_a_list_fails_when_gated(self, cited_ws):
        data = {
            "vulnerabilities": "header parser flaw",
            "severity_summary": {},
            "recommendations": [],
        }
        (cited_ws / "security_assessment.json").write_text(json.dumps(data))
        result = SecurityAssessmentValidator().validate(
            cited_ws, require_grounded_citations=True
        )
        assert result.valid is False
        assert "vulnerabilities" in result.detail

    def test_no_findings_nothing_to_ground_passes(self, cited_ws):
        _write_assessment(cited_ws, [])
        result = SecurityAssessmentValidator().validate(
            cited_ws, require_grounded_citations=True
        )
        assert result.valid is True

    def test_second_finding_failure_reported_by_index(self, cited_ws):
        good = _finding(
            citations=[
                {"repo": "httpcore", "file": "core/http11.py", "evidence_span": CITED_SPAN}
            ]
        )
        bad = _finding(
            citations=[
                {
                    "repo": "httpcore",
                    "file": "core/missing.py",
                    "evidence_span": "span in a file that does not even exist",
                }
            ],
            id="VULN-2",
        )
        _write_assessment(cited_ws, [good, bad])
        result = SecurityAssessmentValidator().validate(
            cited_ws, require_grounded_citations=True
        )
        assert result.valid is False
        assert "vulnerabilities[1]" in result.detail
        assert "file_missing" in result.detail
        assert "vulnerabilities[0]" not in result.detail

    def test_default_off_ignores_fabricated_citations(self, cited_ws):
        """Without the flag, behavior is unchanged (least-invasive integration)."""
        _write_assessment(
            cited_ws,
            [
                _finding(
                    citations=[
                        {
                            "repo": "httpcore",
                            "file": "core/http11.py",
                            "evidence_span": "fabricated but ignored because the gate is off",
                        }
                    ]
                )
            ],
        )
        result = SecurityAssessmentValidator().validate(cited_ws)
        assert result.valid is True
