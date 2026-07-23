"""Tests for the gated-repo-deliverable -> agent_output migration."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "authoring"))

from migrate_deliverable_to_agent_output import (  # noqa: E402
    find_gated_deliverables,
    migrate,
    migrate_check_file,
    migrate_instruction_file,
)


def _write_task(tmp_path: Path, *, repo="kubernetes", filename="INCIDENT_REPORT.md", extra_check=None):
    task_dir = tmp_path / "suite" / "task-001"
    (task_dir / "checks").mkdir(parents=True)
    (task_dir / "task.toml").write_text(
        f'[task]\nid = "task-001"\n\n[[repos]]\npath = "{repo}"\nurl = "https://x"\n'
    )
    (task_dir / "instruction.md").write_text(
        f"## Output\n\nWrite your findings to `/workspace/{repo}/{filename}` with sections.\n"
    )
    (task_dir / "checks" / "check_one.sh").write_text(
        f'#!/usr/bin/env bash\nWORKSPACE="${{WORKSPACE:-/workspace}}"\n'
        f'REPORT="$WORKSPACE/{repo}/{filename}"\n'
    )
    if extra_check:
        (task_dir / "checks" / "check_two.sh").write_text(extra_check)
    return task_dir


class TestFindGatedDeliverables:
    def test_finds_a_deliverable_inside_a_declared_repo(self, tmp_path):
        _write_task(tmp_path)
        hits = find_gated_deliverables(tmp_path)
        assert len(hits) == 1
        h = hits[0]
        assert h["repo_seg"] == "kubernetes"
        assert h["old_path"] == "/workspace/kubernetes/INCIDENT_REPORT.md"
        assert h["new_path"] == "/workspace/agent_output/INCIDENT_REPORT.md"

    def test_finds_a_curly_brace_workspace_deliverable(self, tmp_path):
        task_dir = tmp_path / "suite" / "task-001"
        (task_dir / "checks").mkdir(parents=True)
        (task_dir / "task.toml").write_text(
            '[task]\nid = "t"\n\n[[repos]]\npath = "react"\nurl = "https://x"\n'
        )
        (task_dir / "instruction.md").write_text(
            "Write to `/workspace/react/dead_code_report.json`.\n"
        )
        (task_dir / "checks" / "check_one.sh").write_text(
            'REPORT="${WORKSPACE}/react/dead_code_report.json"\n'
        )
        hits = find_gated_deliverables(tmp_path)
        assert len(hits) == 1
        assert hits[0]["old_path"] == "/workspace/react/dead_code_report.json"

    def test_ignores_a_path_whose_segment_is_not_a_declared_repo(self, tmp_path):
        task_dir = tmp_path / "suite" / "task-001"
        (task_dir / "checks").mkdir(parents=True)
        (task_dir / "task.toml").write_text(
            '[task]\nid = "t"\n\n[[repos]]\npath = "kubernetes"\nurl = "https://x"\n'
        )
        (task_dir / "instruction.md").write_text("Write to `/workspace/analysis/OUT.md`.\n")
        (task_dir / "checks" / "check_one.sh").write_text(
            'WORKSPACE="${WORKSPACE:-/workspace}"\nREPORT="$WORKSPACE/analysis/OUT.md"\n'
        )
        assert find_gated_deliverables(tmp_path) == []

    def test_already_migrated_deliverable_is_not_reflagged(self, tmp_path):
        task_dir = tmp_path / "suite" / "task-001"
        (task_dir / "checks").mkdir(parents=True)
        (task_dir / "task.toml").write_text(
            '[task]\nid = "t"\n\n[[repos]]\npath = "agent_output"\nurl = "https://x"\n'
        )
        (task_dir / "checks" / "check_one.sh").write_text(
            'REPORT="$WORKSPACE/agent_output/answer.json"\n'
        )
        # agent_output is explicitly excluded even if (absurdly) declared as a repo
        assert find_gated_deliverables(tmp_path) == []

    def test_two_deliverables_in_one_task_both_found(self, tmp_path):
        task_dir = _write_task(
            tmp_path,
            extra_check='WORKSPACE="${WORKSPACE:-/workspace}"\nCYCLE="$WORKSPACE/kubernetes/CYCLE.json"\n',
        )
        hits = find_gated_deliverables(tmp_path)
        rels = {h["rel_path"] for h in hits}
        assert rels == {"INCIDENT_REPORT.md", "CYCLE.json"}

    def test_same_file_referenced_by_two_variable_names_is_one_hit(self, tmp_path):
        task_dir = _write_task(
            tmp_path,
            extra_check='WORKSPACE="${WORKSPACE:-/workspace}"\nVERDICT="$WORKSPACE/kubernetes/INCIDENT_REPORT.md"\n',
        )
        hits = find_gated_deliverables(tmp_path)
        assert len(hits) == 1
        assert hits[0]["check_files"] == {
            task_dir / "checks" / "check_one.sh",
            task_dir / "checks" / "check_two.sh",
        }


class TestMigrateCheckFile:
    def test_rewrites_the_declared_path(self, tmp_path):
        p = tmp_path / "check.sh"
        p.write_text('REPORT="$WORKSPACE/kubernetes/INCIDENT_REPORT.md"\nother=1\n')
        new_text, n = migrate_check_file(p, "kubernetes", "INCIDENT_REPORT.md")
        assert n == 1
        assert 'REPORT="$WORKSPACE/agent_output/INCIDENT_REPORT.md"' in new_text
        assert "other=1" in new_text

    def test_rewrites_the_curly_brace_workspace_form(self, tmp_path):
        """check_dead_code.sh's real spelling -- missed by the first pass of
        this tool because only the bare $WORKSPACE form was matched."""
        p = tmp_path / "check.sh"
        p.write_text('REPORT="${WORKSPACE}/react/dead_code_report.json"\n')
        new_text, n = migrate_check_file(p, "react", "dead_code_report.json")
        assert n == 1
        assert 'REPORT="${WORKSPACE}/agent_output/dead_code_report.json"' in new_text
        assert "react" not in new_text

    def test_preserves_variable_name(self, tmp_path):
        p = tmp_path / "check.sh"
        p.write_text('ANSWER_FILE="$WORKSPACE/react/dead_code_report.json"\n')
        new_text, n = migrate_check_file(p, "react", "dead_code_report.json")
        assert n == 1
        assert new_text.startswith('ANSWER_FILE="$WORKSPACE/agent_output/')

    def test_noop_when_path_absent(self, tmp_path):
        p = tmp_path / "check.sh"
        p.write_text('REPORT="$WORKSPACE/agent_output/answer.json"\n')
        new_text, n = migrate_check_file(p, "kubernetes", "INCIDENT_REPORT.md")
        assert n == 0
        assert new_text == p.read_text()


class TestMigrateInstructionFile:
    def test_rewrites_literal_path(self, tmp_path):
        p = tmp_path / "instruction.md"
        p.write_text("Write your findings to `/workspace/kubernetes/INCIDENT_REPORT.md`.\n")
        new_text, n = migrate_instruction_file(
            p, "/workspace/kubernetes/INCIDENT_REPORT.md", "/workspace/agent_output/INCIDENT_REPORT.md"
        )
        assert n == 1
        assert "/workspace/agent_output/INCIDENT_REPORT.md" in new_text
        assert "/workspace/kubernetes/INCIDENT_REPORT.md" not in new_text

    def test_replaces_every_occurrence(self, tmp_path):
        p = tmp_path / "instruction.md"
        p.write_text(
            "See `/workspace/kubernetes/INCIDENT_REPORT.md` for format. "
            "Write to `/workspace/kubernetes/INCIDENT_REPORT.md`.\n"
        )
        new_text, n = migrate_instruction_file(
            p, "/workspace/kubernetes/INCIDENT_REPORT.md", "/workspace/agent_output/INCIDENT_REPORT.md"
        )
        assert n == 2
        assert "/workspace/kubernetes/" not in new_text


class TestMigrateEndToEnd:
    def test_dry_run_reports_but_does_not_write(self, tmp_path):
        task_dir = _write_task(tmp_path)
        hits = find_gated_deliverables(tmp_path)
        result = migrate(hits, apply=False)
        assert result["flags"] == []
        # files unchanged
        assert "kubernetes/INCIDENT_REPORT" in (task_dir / "checks" / "check_one.sh").read_text()
        assert "kubernetes/INCIDENT_REPORT" in (task_dir / "instruction.md").read_text()

    def test_apply_rewrites_both_files(self, tmp_path):
        task_dir = _write_task(tmp_path)
        hits = find_gated_deliverables(tmp_path)
        result = migrate(hits, apply=True)
        assert result["flags"] == []
        chk = (task_dir / "checks" / "check_one.sh").read_text()
        instr = (task_dir / "instruction.md").read_text()
        assert "agent_output/INCIDENT_REPORT.md" in chk
        assert "kubernetes/INCIDENT_REPORT.md" not in chk
        assert "agent_output/INCIDENT_REPORT.md" in instr
        assert "kubernetes/INCIDENT_REPORT.md" not in instr

    def test_apply_is_idempotent(self, tmp_path):
        task_dir = _write_task(tmp_path)
        hits = find_gated_deliverables(tmp_path)
        migrate(hits, apply=True)
        # second pass over the now-migrated corpus finds nothing left to do
        assert find_gated_deliverables(tmp_path) == []

    def test_flags_when_check_updated_but_instruction_silent(self, tmp_path):
        task_dir = tmp_path / "suite" / "task-001"
        (task_dir / "checks").mkdir(parents=True)
        (task_dir / "task.toml").write_text(
            '[task]\nid = "t"\n\n[[repos]]\npath = "kubernetes"\nurl = "https://x"\n'
        )
        (task_dir / "instruction.md").write_text("This instruction never names the deliverable path.\n")
        (task_dir / "checks" / "check_one.sh").write_text(
            'REPORT="$WORKSPACE/kubernetes/INCIDENT_REPORT.md"\n'
        )
        hits = find_gated_deliverables(tmp_path)
        result = migrate(hits, apply=True)
        assert len(result["flags"]) == 1
        assert "needs a human" in result["flags"][0]
        # the check file is still migrated even though flagged
        assert "agent_output" in (task_dir / "checks" / "check_one.sh").read_text()

    def test_no_flag_when_instruction_was_already_migrated_by_an_earlier_hit(
        self, tmp_path
    ):
        """Two check scripts for one task declare the same deliverable; the
        instruction was already rewritten by the first hit's pass. The second
        hit must see the NEW path already present and not flag a gap."""
        task_dir = tmp_path / "suite" / "task-001"
        (task_dir / "checks").mkdir(parents=True)
        (task_dir / "task.toml").write_text(
            '[task]\nid = "t"\n\n[[repos]]\npath = "react"\nurl = "https://x"\n'
        )
        (task_dir / "instruction.md").write_text(
            "Write to `/workspace/agent_output/report.json`.\n"  # already migrated
        )
        (task_dir / "checks" / "check_one.sh").write_text(
            'REPORT="$WORKSPACE/react/report.json"\n'
        )
        hits = find_gated_deliverables(tmp_path)
        result = migrate(hits, apply=True)
        assert result["flags"] == []
        assert "agent_output" in (task_dir / "checks" / "check_one.sh").read_text()

    def test_ambiguous_deliverable_under_two_repos_raises(self, tmp_path):
        task_dir = tmp_path / "suite" / "task-001"
        (task_dir / "checks").mkdir(parents=True)
        (task_dir / "task.toml").write_text(
            '[task]\nid = "t"\n\n[[repos]]\npath = "a"\nurl = "https://x"\n\n'
            '[[repos]]\npath = "b"\nurl = "https://y"\n'
        )
        (task_dir / "instruction.md").write_text("x\n")
        (task_dir / "checks" / "check_one.sh").write_text('REPORT_A="$WORKSPACE/a/SAME.json"\n')
        (task_dir / "checks" / "check_two.sh").write_text('REPORT_B="$WORKSPACE/b/SAME.json"\n')
        with pytest.raises(ValueError, match="ambiguous"):
            find_gated_deliverables(tmp_path)
