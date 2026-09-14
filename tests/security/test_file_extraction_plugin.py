"""Plugin-level tests for ``eb_verify.plugins.file_extraction`` (bead 4aq).

These tests exercise the centralized plugin directly — both via in-process
function calls (fast, hermetic) and via subprocess invocation of the
``python3 -m`` CLI entry point (proves the CLI contract that 37 wrapper
scripts depend on).

Companion file: ``tests/security/test_check_scripts_injection.py`` keeps
the per-script subprocess sweep that proves every wrapper is correctly
delegating to this plugin.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIB_DIR = REPO_ROOT / "lib"

# The host's editable eb_verify install points at a non-existent dir, so
# every test that imports the plugin or invokes its CLI needs to add the
# in-tree ``lib/`` directory to PYTHONPATH.
sys.path.insert(0, str(LIB_DIR))

from eb_verify.plugins import file_extraction  # noqa: E402


# ---------------------------------------------------------------------------
# In-process tests — pure functions
# ---------------------------------------------------------------------------


@pytest.mark.security
class TestExtractGtFiles:
    def test_dict_form(self) -> None:
        gt = {"required_files": [{"path": "a.py"}, {"path": "b.py"}]}
        assert file_extraction._extract_gt_files(gt) == ["a.py", "b.py"]

    def test_flat_string_list(self) -> None:
        gt = {"required_files": ["a.py", "b.py"]}
        assert file_extraction._extract_gt_files(gt) == ["a.py", "b.py"]

    def test_mixed_form(self) -> None:
        gt = {"required_files": [{"path": "a.py"}, "b.py", {"file": "ignored"}]}
        # {"file": ...} is not unwrapped here (only ``path`` keys count for GT).
        assert file_extraction._extract_gt_files(gt) == ["a.py", "b.py"]

    def test_missing_key(self) -> None:
        assert file_extraction._extract_gt_files({}) == []

    def test_non_dict_input(self) -> None:
        assert file_extraction._extract_gt_files("not-a-dict") == []
        assert file_extraction._extract_gt_files(None) == []

    def test_skip_empty_paths(self) -> None:
        gt = {"required_files": [{"path": ""}, {"path": "kept.py"}, ""]}
        assert file_extraction._extract_gt_files(gt) == ["kept.py"]


@pytest.mark.security
class TestExtractAgentFiles:
    KEYS = ["source_files", "files", "error_source.files"]

    def test_source_files_string_list(self) -> None:
        ans = {"source_files": ["a.py", "b.py"]}
        assert file_extraction._extract_agent_files(ans, self.KEYS) == ["a.py", "b.py"]

    def test_source_files_dict_list(self) -> None:
        ans = {"source_files": [{"path": "a.py"}, {"file": "b.py"}]}
        assert file_extraction._extract_agent_files(ans, self.KEYS) == ["a.py", "b.py"]

    def test_falls_back_to_files(self) -> None:
        ans = {"files": ["b.py"]}
        assert file_extraction._extract_agent_files(ans, self.KEYS) == ["b.py"]

    def test_falls_back_to_nested_error_source(self) -> None:
        ans = {"error_source": {"files": ["c.py"]}}
        assert file_extraction._extract_agent_files(ans, self.KEYS) == ["c.py"]

    def test_first_present_key_wins(self) -> None:
        # source_files is present (and a list), so files / error_source must be
        # ignored even if they would otherwise also resolve.
        ans = {"source_files": ["a.py"], "files": ["b.py"], "error_source": {"files": ["c.py"]}}
        assert file_extraction._extract_agent_files(ans, self.KEYS) == ["a.py"]

    def test_type_confused_source_files_returns_empty(self) -> None:
        # source_files is a string, not a list — the safe template MUST NOT
        # crash on this and MUST NOT score it.
        assert file_extraction._extract_agent_files({"source_files": "single.py"}, self.KEYS) == []

    def test_error_source_non_dict_does_not_crash(self) -> None:
        # Denial-of-scoring vector from the security review: a non-dict
        # error_source previously raised AttributeError mid-extraction.
        assert (
            file_extraction._extract_agent_files({"error_source": "malicious"}, self.KEYS)
            == []
        )

    def test_skip_empty_paths_in_dict_entries(self) -> None:
        ans = {"source_files": [{"path": ""}, {"path": "kept.py"}, {"file": ""}]}
        assert file_extraction._extract_agent_files(ans, self.KEYS) == ["kept.py"]

    def test_non_dict_answer(self) -> None:
        assert file_extraction._extract_agent_files("not-a-dict", self.KEYS) == []
        assert file_extraction._extract_agent_files(None, self.KEYS) == []


@pytest.mark.security
class TestMatchPolicies:
    def test_suffix_exact_match(self) -> None:
        assert file_extraction._matches("a/b/c.py", "a/b/c.py", "suffix")

    def test_suffix_endswith_with_separator(self) -> None:
        assert file_extraction._matches("/workspace/repo/a/b/c.py", "a/b/c.py", "suffix")

    def test_suffix_does_not_match_unanchored_substring(self) -> None:
        # Path-spam soft-cheat from bead 0rv.23 — must remain blocked.
        assert not file_extraction._matches("xa/b/c.pyy", "a/b/c.py", "suffix")
        assert not file_extraction._matches("prefix_a/b/c.py_suffix", "a/b/c.py", "suffix")

    def test_suffix_does_not_match_partial_dir_prefix(self) -> None:
        # Without the leading "/", "a/b/c.py" must not match a longer dir.
        assert not file_extraction._matches("zzza/b/c.py", "a/b/c.py", "suffix")

    def test_substring_policy_matches_unanchored(self) -> None:
        # Cousin behavior, kept for the migration of legacy scripts.
        assert file_extraction._matches("xa/b/c.pyy", "a/b/c.py", "substring")
        assert file_extraction._matches("/workspace/repo/a/b/c.py", "a/b/c.py", "substring")

    def test_unknown_policy_raises(self) -> None:
        with pytest.raises(ValueError):
            file_extraction._matches("a", "a", "fictitious")


@pytest.mark.security
class TestScore:
    def test_perfect_score(self) -> None:
        gt = ["a.py", "b.py"]
        agent = ["a.py", "b.py"]
        score, found, total = file_extraction._score(agent, gt, "suffix")
        assert (score, found, total) == (1.0, 2, 2)

    def test_partial_score_two_of_three(self) -> None:
        gt = ["a.py", "b.py", "c.py"]
        agent = ["a.py", "b.py", "unrelated.py"]
        score, found, total = file_extraction._score(agent, gt, "suffix")
        assert score == pytest.approx(2 / 3)
        assert (found, total) == (2, 3)

    def test_zero_when_no_overlap(self) -> None:
        score, found, total = file_extraction._score(["foo"], ["bar"], "suffix")
        assert (score, found, total) == (0.0, 0, 1)

    def test_empty_gt_returns_zero(self) -> None:
        score, found, total = file_extraction._score(["a"], [], "suffix")
        assert (score, found, total) == (0.0, 0, 0)


# ---------------------------------------------------------------------------
# CLI subprocess tests — adversarial security suite
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_env() -> dict[str, str]:
    """Base environment for ``python3 -m eb_verify.plugins.file_extraction``
    that includes the in-tree lib path."""
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{LIB_DIR}{os.pathsep}{env.get('PYTHONPATH', '')}".rstrip(os.pathsep)
    return env


def _write_fixtures(
    tmp_path: Path,
    *,
    answer: object | None,
    ground_truth: object | None = None,
    answer_raw: str | None = None,
) -> tuple[Path, Path]:
    """Write fixture files and return (ANSWER_FILE, GT_FILE) paths.

    Pass ``answer=None`` to leave the answer file missing.
    Pass ``answer_raw='...'`` to write malformed text instead of JSON.
    Pass ``ground_truth=None`` to leave the GT file missing.
    """
    answer_path = tmp_path / "answer.json"
    gt_path = tmp_path / "ground_truth.json"
    if answer_raw is not None:
        answer_path.write_text(answer_raw)
    elif answer is not None:
        answer_path.write_text(json.dumps(answer))
    if ground_truth is not None:
        gt_path.write_text(json.dumps(ground_truth))
    return answer_path, gt_path


def _run_cli(
    answer_path: Path,
    gt_path: Path,
    *,
    env: dict[str, str],
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable,
        "-m",
        "eb_verify.plugins.file_extraction",
        "--keys",
        "source_files,files,error_source.files",
        "--policy",
        "suffix",
    ] + (extra_args or [])
    sub_env = dict(env)
    sub_env["ANSWER_FILE"] = str(answer_path)
    sub_env["GT_FILE"] = str(gt_path)
    return subprocess.run(args, env=sub_env, capture_output=True, text=True, timeout=30)


def _parse_score(proc: subprocess.CompletedProcess[str]) -> dict:
    stdout = proc.stdout.strip()
    assert stdout, f"no stdout (stderr={proc.stderr!r})"
    return json.loads(stdout.splitlines()[-1])


_GT_THREE = {
    "required_files": [
        {"path": "src/hyper/conn/http1.rs"},
        {"path": "tokio/runtime/scheduler.rs"},
        {"path": "hyper/server/builder.rs"},
    ]
}


@pytest.mark.security
class TestCli:
    def test_benign_two_of_three(self, tmp_path: Path, cli_env: dict[str, str]) -> None:
        answer_path, gt_path = _write_fixtures(
            tmp_path,
            answer={
                "source_files": [
                    "/workspace/hyper/src/hyper/conn/http1.rs",
                    "/workspace/tokio/tokio/runtime/scheduler.rs",
                    "/workspace/unrelated/file.rs",
                ]
            },
            ground_truth=_GT_THREE,
        )
        proc = _run_cli(answer_path, gt_path, env=cli_env)
        result = _parse_score(proc)
        assert result["score"] == pytest.approx(0.67, abs=0.01)
        assert result["passed"] is True
        assert proc.returncode == 0

    def test_missing_answer_returns_zero_exit_zero(
        self, tmp_path: Path, cli_env: dict[str, str]
    ) -> None:
        # Write ONLY ground truth — answer.json deliberately absent.
        _, gt_path = _write_fixtures(tmp_path, answer=None, ground_truth=_GT_THREE)
        answer_path = tmp_path / "answer.json"
        assert not answer_path.exists()
        proc = _run_cli(answer_path, gt_path, env=cli_env)
        result = _parse_score(proc)
        assert result == {"score": 0.0, "passed": False, "detail": "No answer.json found"}
        assert proc.returncode == 0  # agent failure, not infra

    def test_missing_gt_is_infra_failure(
        self, tmp_path: Path, cli_env: dict[str, str]
    ) -> None:
        answer_path, _ = _write_fixtures(
            tmp_path, answer={"source_files": ["a.rs"]}, ground_truth=None
        )
        gt_path = tmp_path / "ground_truth.json"
        assert not gt_path.exists()
        proc = _run_cli(answer_path, gt_path, env=cli_env)
        # Infra failure: error JSON on stderr, non-zero exit.
        assert proc.returncode != 0
        assert "ground_truth" in proc.stderr or "ground_truth" in proc.stdout

    def test_malformed_json_does_not_crash(
        self, tmp_path: Path, cli_env: dict[str, str]
    ) -> None:
        answer_path, gt_path = _write_fixtures(
            tmp_path,
            answer=None,
            answer_raw="{not: valid, json",
            ground_truth=_GT_THREE,
        )
        proc = _run_cli(answer_path, gt_path, env=cli_env)
        result = _parse_score(proc)
        assert result["score"] == 0.0
        assert result["detail"] == "Parse error"
        assert proc.returncode == 0

    def test_triple_quote_payload_is_opaque_string(
        self, tmp_path: Path, cli_env: dict[str, str]
    ) -> None:
        """The vulnerable template let an agent close a Python triple-quoted
        string and execute code.  The plugin reads the file in a single
        Python process — the payload is just a string here, so we just
        verify it doesn't score and doesn't crash."""
        canary = tmp_path / "pwned"
        injected = "'''+str(__import__('os').system('touch " + str(canary) + "'))+'''"
        answer_path, gt_path = _write_fixtures(
            tmp_path,
            answer={
                "source_files": [injected, "/workspace/hyper/src/hyper/conn/http1.rs"]
            },
            ground_truth=_GT_THREE,
        )
        proc = _run_cli(answer_path, gt_path, env=cli_env)
        assert not canary.exists(), (
            f"plugin executed injected code: canary {canary} created. "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
        result = _parse_score(proc)
        # 1 of 3 — only the legit suffix-anchored path counts.
        assert result["score"] == pytest.approx(1 / 3, abs=0.01)

    def test_path_spam_substring_cheat_blocked(
        self, tmp_path: Path, cli_env: dict[str, str]
    ) -> None:
        answer_path, gt_path = _write_fixtures(
            tmp_path,
            answer={
                "source_files": [
                    "xsrc/hyper/conn/http1.rsy",
                    "prefix_tokio/runtime/scheduler.rs_suffix",
                    "totally/unrelated/file.rs",
                ]
            },
            ground_truth=_GT_THREE,
        )
        proc = _run_cli(answer_path, gt_path, env=cli_env)
        result = _parse_score(proc)
        assert result["score"] == 0.0

    def test_type_confusion_is_zero_not_crash(
        self, tmp_path: Path, cli_env: dict[str, str]
    ) -> None:
        answer_path, gt_path = _write_fixtures(
            tmp_path,
            answer={
                "source_files": "not-a-list",
                "files": 42,
                "error_source": "also-not-a-dict",
            },
            ground_truth=_GT_THREE,
        )
        proc = _run_cli(answer_path, gt_path, env=cli_env)
        result = _parse_score(proc)
        assert result["score"] == 0.0
        assert proc.returncode == 0

    def test_pass_threshold_flag_lifts_passed_to_true(
        self, tmp_path: Path, cli_env: dict[str, str]
    ) -> None:
        # Score 1/3 ≈ 0.33 — fails the default 0.5 threshold but passes 0.3.
        answer_path, gt_path = _write_fixtures(
            tmp_path,
            answer={"source_files": ["/workspace/hyper/src/hyper/conn/http1.rs"]},
            ground_truth=_GT_THREE,
        )
        strict = _run_cli(answer_path, gt_path, env=cli_env)
        loose = _run_cli(answer_path, gt_path, env=cli_env, extra_args=["--pass-threshold", "0.3"])
        assert _parse_score(strict)["passed"] is False
        assert _parse_score(loose)["passed"] is True

    def test_substring_policy_matches_unanchored(
        self, tmp_path: Path, cli_env: dict[str, str]
    ) -> None:
        answer_path, gt_path = _write_fixtures(
            tmp_path,
            answer={"source_files": ["xa/b/c.pyy"]},
            ground_truth={"required_files": [{"path": "a/b/c.py"}]},
        )
        suffix = _run_cli(answer_path, gt_path, env=cli_env)
        substring = _run_cli(
            answer_path,
            gt_path,
            env=cli_env,
            extra_args=["--policy", "substring"],
        )
        assert _parse_score(suffix)["score"] == 0.0
        assert _parse_score(substring)["score"] == 1.0
