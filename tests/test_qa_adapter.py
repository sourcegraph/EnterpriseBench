"""Tests for the eb_verify QA adapter (lib/eb_verify/qa_adapter.py).

Smoke-level coverage: vendored benchmark_qa_core import, finding shape,
strict vs warn-only, scoring-method synthesis, and end-to-end on a
representative real EB task.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eb_verify.qa_adapter import (
    EB_SCORING_METHOD_TIERS,
    QaReport,
    load_task_inputs,
    run_qa_checks,
)
from eb_verify.schema_validator import validate_task


def _write_task(
    tmp_path: Path,
    *,
    verification_modes: list[str] | None = None,
    repos: list[dict] | None = None,
    ground_truth: dict | None = None,
    instruction: str | None = None,
    expected_solution: dict | None = None,
    languages: list[str] | None = None,
    extra_task_keys: dict | None = None,
) -> Path:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    repos = repos or [{"url": "https://example.com/r.git", "rev": "v1", "path": "r"}]
    ground_truth = ground_truth if ground_truth is not None else {
        "tiers": ["deterministic"],
        "required_files": [{"path": "src/foo.py", "repo": "r"}],
    }
    languages = languages or ["python"]
    task_section = {
        "id": "demo-task-001",
        "suite": "dependency_management",
        "difficulty": "medium",
        "session_type": "single",
        "description": "demo",
        "prompt": "p",
        "estimated_duration_minutes": 30,
    }
    if extra_task_keys:
        task_section.update(extra_task_keys)

    lines: list[str] = []
    # Top-level keys must come BEFORE any [section] header in TOML.
    if verification_modes is not None:
        lines.append("verification_modes = " + json.dumps(verification_modes))
        lines.append("")
    lines.append("[task]")
    for k, v in task_section.items():
        if isinstance(v, str):
            lines.append(f'{k} = "{v}"')
        else:
            lines.append(f"{k} = {v}")
    lines.append("")
    lines.append("[metadata]")
    lines.append("languages = " + json.dumps(languages))
    lines.append("")
    for r in repos:
        lines.append("[[repos]]")
        for k, v in r.items():
            lines.append(f'{k} = "{v}"')
        lines.append("")
    # checkpoints (one with full weight)
    lines.append("[[checkpoints]]")
    lines.append('name = "cp1"')
    lines.append("weight = 1.0")
    lines.append('verifier = "checks/cp1.sh"')
    lines.append("")
    lines.append("[artifacts]")
    lines.append("required = []")
    lines.append("optional = []")
    lines.append("")
    # embedded ground_truth
    lines.append("[ground_truth]")
    if "tiers" in ground_truth:
        lines.append("tiers = " + json.dumps(ground_truth["tiers"]))
    for entry in ground_truth.get("required_files", []):
        lines.append("[[ground_truth.required_files]]")
        for k, v in entry.items():
            if isinstance(v, str):
                lines.append(f'{k} = "{v}"')
            else:
                lines.append(f"{k} = {v}")
    for entry in ground_truth.get("sufficient_files", []):
        lines.append("[[ground_truth.sufficient_files]]")
        for k, v in entry.items():
            if isinstance(v, str):
                lines.append(f'{k} = "{v}"')
            else:
                lines.append(f"{k} = {v}")
    task_toml = task_dir / "task.toml"
    task_toml.write_text("\n".join(lines) + "\n")
    if instruction is not None:
        (task_dir / "instruction.md").write_text(instruction)
    if expected_solution is not None:
        (task_dir / "expected_solution.json").write_text(json.dumps(expected_solution))
    return task_toml


class TestLoadTaskInputs:
    def test_merges_task_toml_and_ground_truth_json(self, tmp_path: Path):
        task_toml = _write_task(
            tmp_path,
            ground_truth={
                "tiers": ["deterministic"],
                "required_files": [{"path": "a.py", "repo": "r"}],
            },
        )
        # Add a separate ground_truth.json with task-type-specific keys.
        (task_toml.parent / "ground_truth.json").write_text(
            json.dumps({"breakage_type": "compile_only"})
        )
        inputs = load_task_inputs(task_toml)
        assert inputs.ground_truth["required_files"][0]["path"] == "a.py"
        # JSON-only key is also visible to the adapter.
        assert inputs.ground_truth["breakage_type"] == "compile_only"

    def test_missing_optional_files_become_empty_dicts(self, tmp_path: Path):
        task_toml = _write_task(tmp_path)
        inputs = load_task_inputs(task_toml)
        assert isinstance(inputs.expected_solution, dict)


class TestScoringMethodSynthesis:
    def test_no_verification_modes_emits_e1(self, tmp_path: Path):
        task_toml = _write_task(tmp_path, verification_modes=[])
        report = run_qa_checks(load_task_inputs(task_toml))
        codes = {f.code for f in report.findings}
        assert "E1" in codes

    def test_unknown_combo_emits_e2(self, tmp_path: Path):
        # Bypass the JSON Schema enum by injecting the unknown method directly
        # into the in-memory task_toml — the adapter only looks at
        # verification_modes, and any string is fair game from its POV.
        task_toml = _write_task(tmp_path, verification_modes=["deterministic"])
        inputs = load_task_inputs(task_toml)
        # Override with an unsanctioned mode the schema would reject but the
        # QA adapter treats as a valid input.
        bad_toml = dict(inputs.task_toml)
        bad_toml["verification_modes"] = ["nonexistent_mode"]
        from dataclasses import replace

        report = run_qa_checks(replace(inputs, task_toml=bad_toml))
        codes = {f.code for f in report.findings}
        assert "E2" in codes

    def test_known_single_mode_passes(self, tmp_path: Path):
        task_toml = _write_task(tmp_path, verification_modes=["deterministic"])
        report = run_qa_checks(load_task_inputs(task_toml))
        codes = {f.code for f in report.findings}
        assert "E1" not in codes
        assert "E2" not in codes

    def test_known_combination_is_normalised(self, tmp_path: Path):
        # Order should not matter — combination is sorted before lookup.
        task_toml = _write_task(
            tmp_path, verification_modes=["llm_curator", "deterministic"]
        )
        report = run_qa_checks(load_task_inputs(task_toml))
        codes = {f.code for f in report.findings}
        assert "E2" not in codes


class TestOracleFileResolution:
    def test_skips_a1_when_repo_not_cloned(self, tmp_path: Path):
        task_toml = _write_task(tmp_path)
        report = run_qa_checks(load_task_inputs(task_toml))
        codes = {f.code for f in report.findings}
        assert "EB_A0" in codes  # info, repo not cloned

    def test_finds_a1_when_workspace_supplied_and_file_missing(
        self, tmp_path: Path
    ):
        # Create a fake workspace with the repo dir but missing the oracle file.
        workspace = tmp_path / "ws"
        (workspace / "r").mkdir(parents=True)
        task_toml = _write_task(
            tmp_path,
            ground_truth={
                "tiers": ["deterministic"],
                "required_files": [{"path": "src/foo.py", "repo": "r"}],
            },
        )
        report = run_qa_checks(load_task_inputs(task_toml, workspace_root=workspace))
        codes = {f.code for f in report.findings}
        assert "A1" in codes

    def test_unknown_repo_emits_eb_a3(self, tmp_path: Path):
        task_toml = _write_task(
            tmp_path,
            ground_truth={
                "tiers": ["deterministic"],
                "required_files": [{"path": "x.py", "repo": "ghost"}],
            },
        )
        report = run_qa_checks(load_task_inputs(task_toml))
        codes = {f.code for f in report.findings}
        assert "EB_A3" in codes


class TestLeakageDetection:
    def test_oracle_path_in_instruction_emits_f2(self, tmp_path: Path):
        task_toml = _write_task(
            tmp_path,
            ground_truth={
                "tiers": ["deterministic"],
                "required_files": [
                    {"path": "very/specific/path.py", "repo": "r"}
                ],
            },
            instruction=(
                "Investigate the bug; the file in question is at "
                "very/specific/path.py and breaks under load."
            ),
        )
        report = run_qa_checks(load_task_inputs(task_toml))
        codes = {f.code for f in report.findings}
        assert "F2" in codes
        # F2 is downgraded to warning for EB by the adapter.
        f2 = next(f for f in report.findings if f.code == "F2")
        assert f2.severity == "warning"

    def test_no_leak_when_path_absent_from_instruction(self, tmp_path: Path):
        task_toml = _write_task(
            tmp_path,
            ground_truth={
                "tiers": ["deterministic"],
                "required_files": [{"path": "very/specific/path.py", "repo": "r"}],
            },
            instruction="Generic instruction with no concrete paths.",
        )
        report = run_qa_checks(load_task_inputs(task_toml))
        codes = {f.code for f in report.findings}
        assert "F2" not in codes

    def test_generic_manifest_basename_does_not_emit_f2(self, tmp_path: Path):
        # A bare ``go.mod`` is non-discriminative: naming it in the prompt does
        # not leak which file holds the answer, so it must not raise F2.
        task_toml = _write_task(
            tmp_path,
            ground_truth={
                "tiers": ["deterministic"],
                "required_files": [{"path": "go.mod", "repo": "r"}],
            },
            instruction="Trace the dependency starting from go.mod.",
        )
        report = run_qa_checks(load_task_inputs(task_toml))
        codes = {f.code for f in report.findings}
        assert "F2" not in codes

    def test_specific_path_to_generic_manifest_still_emits_f2(
        self, tmp_path: Path
    ):
        # ``envoy/go.mod`` carries directory components — it is a specific path
        # that DOES reveal the file, so it must still raise F2.
        task_toml = _write_task(
            tmp_path,
            ground_truth={
                "tiers": ["deterministic"],
                "required_files": [{"path": "envoy/go.mod", "repo": "r"}],
            },
            instruction="The relevant manifest is envoy/go.mod.",
        )
        report = run_qa_checks(load_task_inputs(task_toml))
        codes = {f.code for f in report.findings}
        assert "F2" in codes

    def _leaky_inputs(self, tmp_path: Path):
        task_toml = _write_task(
            tmp_path,
            ground_truth={
                "tiers": ["deterministic"],
                "required_files": [{"path": "very/specific/path.py", "repo": "r"}],
            },
            instruction=(
                "Investigate the bug; the file in question is at "
                "very/specific/path.py and breaks under load."
            ),
        )
        return load_task_inputs(task_toml)

    def test_f2_waiver_downgrades_to_info(self, tmp_path: Path):
        # A matching [[qa.waivers]] entry downgrades the F2 from warning to
        # info and attaches the rationale, so it no longer counts as a warning.
        from dataclasses import replace

        inputs = self._leaky_inputs(tmp_path)
        waived = replace(
            inputs,
            task_toml={
                **inputs.task_toml,
                "qa": {
                    "waivers": [
                        {
                            "code": "F2",
                            "token": "very/specific/path.py",
                            "decision": "waive",
                            "rationale": "given investigation entry point",
                        }
                    ]
                },
            },
        )
        report = run_qa_checks(waived)
        f2 = next(f for f in report.findings if f.code == "F2")
        assert f2.severity == "info"
        assert "waived" in f2.message
        assert "given investigation entry point" in f2.message

    def test_f2_waiver_for_other_token_still_warns(self, tmp_path: Path):
        # A waiver that names a different token must not suppress the real F2.
        from dataclasses import replace

        inputs = self._leaky_inputs(tmp_path)
        waived = replace(
            inputs,
            task_toml={
                **inputs.task_toml,
                "qa": {
                    "waivers": [
                        {
                            "code": "F2",
                            "token": "some/other/file.py",
                            "decision": "waive",
                            "rationale": "unrelated",
                        }
                    ]
                },
            },
        )
        report = run_qa_checks(waived)
        f2 = next(f for f in report.findings if f.code == "F2")
        assert f2.severity == "warning"


class TestSchemaValidatorIntegration:
    def test_warn_only_default_does_not_fail_on_qa_errors(self, tmp_path: Path):
        # Trigger an E1 (missing scoring_method) by omitting verification_modes.
        # The JSON Schema layer leaves the field optional; the QA layer flags it.
        task_toml = _write_task(tmp_path, verification_modes=None)
        result = validate_task(str(task_toml))
        # In warn-only mode, qa errors land in warnings and result.valid stays True.
        assert result.valid is True, (
            f"unexpected errors: {[(e.field, e.message) for e in result.errors]}"
        )
        warning_fields = {w.field for w in result.warnings}
        assert any(f.startswith("qa.E1") for f in warning_fields)

    def test_strict_mode_fails_on_qa_error(self, tmp_path: Path):
        task_toml = _write_task(tmp_path, verification_modes=None)
        result = validate_task(str(task_toml), qa_strict=True)
        assert result.valid is False
        error_fields = {e.field for e in result.errors}
        assert any(f.startswith("qa.E1") for f in error_fields)

    def test_strict_mode_keeps_warning_severity_findings_as_warnings(
        self, tmp_path: Path
    ):
        # F2 leakage is downgraded to warning by the adapter, even in strict mode.
        task_toml = _write_task(
            tmp_path,
            verification_modes=["deterministic"],
            ground_truth={
                "tiers": ["deterministic"],
                "required_files": [{"path": "very/specific/path.py", "repo": "r"}],
            },
            instruction="See very/specific/path.py for context.",
        )
        result = validate_task(str(task_toml), qa_strict=True)
        warning_fields = {w.field for w in result.warnings}
        assert any(f.startswith("qa.F2") for f in warning_fields)


class TestQaReport:
    def test_errors_and_warnings_partition(self):
        from eb_verify._vendor.benchmark_qa_core import Finding

        report = QaReport(
            task_id="t",
            findings=[
                Finding(severity="error", code="X1", message="x"),
                Finding(severity="warning", code="Y1", message="y"),
                Finding(severity="info", code="Z1", message="z"),
            ],
        )
        assert [f.code for f in report.errors] == ["X1"]
        assert [f.code for f in report.warnings] == ["Y1"]


class TestEbScoringTiers:
    def test_all_combinations_resolve_to_calibrated(self):
        for value in EB_SCORING_METHOD_TIERS.values():
            assert value == "calibrated"

    def test_table_includes_basic_modes(self):
        for mode in ("deterministic", "llm_curator"):
            assert mode in EB_SCORING_METHOD_TIERS


class TestGenericManifestFilter:
    """Direct lib-level coverage of the generic-manifest skip in leakage.py."""

    def test_bare_manifest_token_is_skipped(self, tmp_path: Path):
        from eb_verify._vendor.benchmark_qa_core.leakage import (
            check_aux_file_leakage,
        )

        aux = tmp_path / "instruction.md"
        aux.write_text("Start from go.mod and package.json.", encoding="utf-8")
        findings = check_aux_file_leakage(["go.mod", "package.json"], [aux])
        assert not [f for f in findings if f.code == "F2"]

    def test_specific_path_token_is_not_skipped(self, tmp_path: Path):
        from eb_verify._vendor.benchmark_qa_core.leakage import (
            check_aux_file_leakage,
        )

        aux = tmp_path / "instruction.md"
        aux.write_text("See contrib/go.mod for the pin.", encoding="utf-8")
        findings = check_aux_file_leakage(["contrib/go.mod"], [aux])
        assert [f.code for f in findings if f.code == "F2"] == ["F2"]
