"""Tests for the eb_verify F2 QA adapter (lib/eb_verify/qa_adapter.py).

Covers: vendored benchmark_qa_core leakage import, finding shape, the F2
oracle-path-leak check with word-boundary matching, the generic-manifest
basename skip, and [[qa.waivers]] downgrade semantics.

This is the F2 subset of the fuller qa_adapter that lives on the
fix/eb-f2-leak-triage branch; oracle-coherence and scoring-honesty checks
were not ported.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from eb_verify.qa_adapter import (
    QaReport,
    load_task_inputs,
    run_qa_checks,
)


def _write_task(
    tmp_path: Path,
    *,
    ground_truth: dict | None = None,
    instruction: str | None = None,
    qa_waivers: list[dict] | None = None,
) -> Path:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    ground_truth = ground_truth if ground_truth is not None else {
        "required_files": [{"path": "src/foo.py", "repo": "r"}],
    }

    lines: list[str] = []
    lines.append("[task]")
    lines.append('id = "demo-task-001"')
    lines.append('suite = "dependency_management"')
    lines.append("")
    if qa_waivers:
        for w in qa_waivers:
            lines.append("[[qa.waivers]]")
            for k, v in w.items():
                lines.append(f"{k} = {json.dumps(v)}")
            lines.append("")
    lines.append("[ground_truth]")
    for section in ("required_files", "sufficient_files"):
        for entry in ground_truth.get(section, []):
            lines.append(f"[[ground_truth.{section}]]")
            for k, v in entry.items():
                lines.append(f"{k} = {json.dumps(v)}")
    task_toml = task_dir / "task.toml"
    task_toml.write_text("\n".join(lines) + "\n")
    if instruction is not None:
        (task_dir / "instruction.md").write_text(instruction)
    return task_toml


class TestLoadTaskInputs:
    def test_merges_task_toml_and_ground_truth_json(self, tmp_path: Path):
        task_toml = _write_task(
            tmp_path,
            ground_truth={"required_files": [{"path": "a.py", "repo": "r"}]},
        )
        (task_toml.parent / "ground_truth.json").write_text(
            json.dumps({"breakage_type": "compile_only"})
        )
        inputs = load_task_inputs(task_toml)
        assert inputs.ground_truth["required_files"][0]["path"] == "a.py"
        assert inputs.ground_truth["breakage_type"] == "compile_only"

    def test_task_toml_ground_truth_wins_over_json(self, tmp_path: Path):
        task_toml = _write_task(
            tmp_path,
            ground_truth={"required_files": [{"path": "a.py", "repo": "r"}]},
        )
        (task_toml.parent / "ground_truth.json").write_text(
            json.dumps({"required_files": [{"path": "b.py", "repo": "r"}]})
        )
        inputs = load_task_inputs(task_toml)
        assert inputs.ground_truth["required_files"][0]["path"] == "a.py"

    def test_missing_instruction_is_none(self, tmp_path: Path):
        task_toml = _write_task(tmp_path)
        inputs = load_task_inputs(task_toml)
        assert inputs.instruction_path is None


class TestLeakageDetection:
    def test_oracle_path_in_instruction_emits_f2_warning(self, tmp_path: Path):
        task_toml = _write_task(
            tmp_path,
            ground_truth={
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
        f2 = [f for f in report.findings if f.code == "F2"]
        assert len(f2) == 1
        # F2 is downgraded from the lib's error to warning for EB.
        assert f2[0].severity == "warning"
        assert report.warnings == f2

    def test_no_leak_when_path_absent_from_instruction(self, tmp_path: Path):
        task_toml = _write_task(
            tmp_path,
            ground_truth={
                "required_files": [{"path": "very/specific/path.py", "repo": "r"}],
            },
            instruction="Generic instruction with no concrete paths.",
        )
        report = run_qa_checks(load_task_inputs(task_toml))
        assert "F2" not in {f.code for f in report.findings}

    def test_partial_path_is_not_a_word_boundary_match(self, tmp_path: Path):
        # "src/foo.py" inside "other/src/foo.py" must not match: \b matching
        # is on the whole token.
        task_toml = _write_task(
            tmp_path,
            ground_truth={
                "required_files": [{"path": "foo.py", "repo": "r"}],
            },
            instruction="Look at foo.pyx for the cython shim.",
        )
        report = run_qa_checks(load_task_inputs(task_toml))
        assert "F2" not in {f.code for f in report.findings}

    def test_sufficient_files_also_checked(self, tmp_path: Path):
        task_toml = _write_task(
            tmp_path,
            ground_truth={
                "required_files": [],
                "sufficient_files": [{"path": "deep/oracle.go", "repo": "r"}],
            },
            instruction="The answer path deep/oracle.go is right here.",
        )
        report = run_qa_checks(load_task_inputs(task_toml))
        assert "F2" in {f.code for f in report.findings}

    def test_generic_manifest_basename_does_not_emit_f2(self, tmp_path: Path):
        task_toml = _write_task(
            tmp_path,
            ground_truth={
                "required_files": [{"path": "go.mod", "repo": "r"}],
            },
            instruction="Trace the dependency starting from go.mod.",
        )
        report = run_qa_checks(load_task_inputs(task_toml))
        assert "F2" not in {f.code for f in report.findings}

    def test_specific_path_to_generic_manifest_still_emits_f2(
        self, tmp_path: Path
    ):
        task_toml = _write_task(
            tmp_path,
            ground_truth={
                "required_files": [{"path": "envoy/go.mod", "repo": "r"}],
            },
            instruction="The relevant manifest is envoy/go.mod.",
        )
        report = run_qa_checks(load_task_inputs(task_toml))
        assert "F2" in {f.code for f in report.findings}

    def test_no_instruction_no_findings(self, tmp_path: Path):
        task_toml = _write_task(
            tmp_path,
            ground_truth={
                "required_files": [{"path": "src/deep/foo.py", "repo": "r"}],
            },
        )
        report = run_qa_checks(load_task_inputs(task_toml))
        assert report.findings == []


class TestWaivers:
    def _leaky_task(self, tmp_path: Path, qa_waivers: list[dict] | None = None):
        return _write_task(
            tmp_path,
            ground_truth={
                "required_files": [{"path": "very/specific/path.py", "repo": "r"}],
            },
            instruction=(
                "Investigate the bug; the file in question is at "
                "very/specific/path.py and breaks under load."
            ),
            qa_waivers=qa_waivers,
        )

    def test_f2_waiver_downgrades_to_info(self, tmp_path: Path):
        task_toml = self._leaky_task(
            tmp_path,
            qa_waivers=[
                {
                    "code": "F2",
                    "token": "very/specific/path.py",
                    "decision": "waive",
                    "rationale": "given investigation entry point",
                }
            ],
        )
        report = run_qa_checks(load_task_inputs(task_toml))
        f2 = next(f for f in report.findings if f.code == "F2")
        assert f2.severity == "info"
        assert "waived" in f2.message
        assert "given investigation entry point" in f2.message
        assert report.warnings == []

    def test_f2_waiver_for_other_token_still_warns(self, tmp_path: Path):
        task_toml = self._leaky_task(
            tmp_path,
            qa_waivers=[
                {
                    "code": "F2",
                    "token": "some/other/file.py",
                    "decision": "waive",
                    "rationale": "unrelated",
                }
            ],
        )
        report = run_qa_checks(load_task_inputs(task_toml))
        f2 = next(f for f in report.findings if f.code == "F2")
        assert f2.severity == "warning"

    def test_waiver_with_wrong_code_does_not_suppress(self, tmp_path: Path):
        task_toml = self._leaky_task(
            tmp_path,
            qa_waivers=[
                {
                    "code": "F3",
                    "token": "very/specific/path.py",
                    "decision": "waive",
                    "rationale": "wrong code",
                }
            ],
        )
        report = run_qa_checks(load_task_inputs(task_toml))
        f2 = next(f for f in report.findings if f.code == "F2")
        assert f2.severity == "warning"

    def test_waiver_partial_token_cannot_match_longer_path(self, tmp_path: Path):
        # Token matching is by repr; a waiver for "path.py" must not suppress
        # the F2 for "very/specific/path.py".
        task_toml = self._leaky_task(
            tmp_path,
            qa_waivers=[
                {
                    "code": "F2",
                    "token": "path.py",
                    "decision": "waive",
                    "rationale": "partial",
                }
            ],
        )
        report = run_qa_checks(load_task_inputs(task_toml))
        f2 = next(f for f in report.findings if f.code == "F2")
        assert f2.severity == "warning"


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

    def test_missing_aux_file_emits_f3_not_crash(self, tmp_path: Path):
        from eb_verify._vendor.benchmark_qa_core.leakage import (
            check_aux_file_leakage,
        )

        findings = check_aux_file_leakage(
            ["some/path.py"], [tmp_path / "nope.md"]
        )
        assert [f.code for f in findings] == ["F3"]


class TestSchemaWaiverBlock:
    """The task schema documents and constrains [[qa.waivers]]."""

    def _schema(self):
        root = Path(__file__).parent.parent
        return json.loads((root / "schemas" / "task.schema.json").read_text())

    def test_schema_declares_qa_waivers(self):
        schema = self._schema()
        qa = schema["properties"]["qa"]
        waiver_props = qa["properties"]["waivers"]["items"]["properties"]
        assert {"code", "token", "decision", "rationale"} <= set(waiver_props)

    def test_schema_rejects_bad_decision(self):
        import jsonschema

        schema = self._schema()
        doc = {
            "qa": {
                "waivers": [
                    {
                        "code": "F2",
                        "token": "x.py",
                        "decision": "ignore",
                        "rationale": "r",
                    }
                ]
            }
        }
        validator = jsonschema.Draft202012Validator(schema)
        errors = list(validator.iter_errors(doc))
        assert errors, "schema must reject decision values outside accept/waive"
