"""Tests for agent conversation trace capture from container.

Covers the _copy_agent_trace() function that extracts Claude Code session
JSONL from /home/agent/.claude/projects/ inside the Docker container.

The container-side find emits NUL-delimited `<mtime> <path>` records and the
newest is chosen in Python, so these mocks use that shape. Newline framing is
not a valid payload to mock — see TestTraceRecordForgery for why.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.orchestration.run_task import (
    TRACE_ROOT,
    _copy_agent_trace,
    _newest_trace_path,
)


def _find_stdout(*entries: tuple[str, str]) -> str:
    """Build a NUL-delimited `<mtime> <path>` find payload from (mtime, path)."""
    return "".join(f"{mtime} {path}\0" for mtime, path in entries)


def _cp_writer(contents: dict[str, str]):
    """Patch for _docker_cp that materialises the staged file, as the real one
    does. ``contents`` maps a container path to the JSONL body copied out."""

    def fake_cp(src: str, dest: str) -> None:
        container_path = src.split(":", 1)[1]
        body = contents.get(container_path)
        if body is not None:
            Path(dest).write_text(body)

    return fake_cp


class TestCopyAgentTrace:
    """Unit tests for _copy_agent_trace()."""

    def test_copies_trace_file_when_found(self, tmp_path: Path) -> None:
        """Should find and copy the JSONL trace file from the container."""
        output_dir = tmp_path / "results"
        output_dir.mkdir()

        traced = f"{TRACE_ROOT}/abc123/conversation.jsonl"
        find_result = MagicMock()
        find_result.returncode = 0
        find_result.stdout = _find_stdout(("1720000000.0", traced))

        with patch(
            "scripts.orchestration.run_task._docker_exec", return_value=find_result
        ) as mock_exec, patch(
            "scripts.orchestration.run_task._docker_cp",
            side_effect=_cp_writer({traced: '{"n": 1}\n'}),
        ):
            result = _copy_agent_trace("container-123", output_dir)

        assert result is True
        assert (output_dir / "agent_trace.jsonl").read_text() == '{"n": 1}\n'
        assert "find" in " ".join(mock_exec.call_args_list[0][0][1])

    def test_find_command_emits_nul_delimited_mtime_records(
        self, tmp_path: Path
    ) -> None:
        """find must emit NUL-delimited mtime records and not sort in the shell.

        Shell-side `sort -rn` would rank a forged mtime field, and newline
        framing would let a crafted filename forge the record in the first
        place. Ordering is decided in Python over validated records instead.
        """
        output_dir = tmp_path / "results"
        output_dir.mkdir()

        find_result = MagicMock()
        find_result.returncode = 0
        find_result.stdout = _find_stdout(
            ("1720000000.0", f"{TRACE_ROOT}/abc/conversation.jsonl")
        )

        cp_result = MagicMock()
        cp_result.returncode = 0
        cp_result.stderr = ""

        with patch("scripts.orchestration.run_task.subprocess.run") as mock_run:
            mock_run.side_effect = [find_result, cp_result]
            _copy_agent_trace("container-123", output_dir)

        shell_cmd = mock_run.call_args_list[0][0][0][-1]
        assert "-printf" in shell_cmd
        assert "%T@" in shell_cmd
        assert r"\0" in shell_cmd
        assert "sort" not in shell_cmd

    def test_returns_false_when_no_trace_found(self, tmp_path: Path) -> None:
        """Should return False and not error when no trace file exists."""
        output_dir = tmp_path / "results"
        output_dir.mkdir()

        find_result = MagicMock()
        find_result.returncode = 0
        find_result.stdout = ""

        with patch("scripts.orchestration.run_task.subprocess.run") as mock_run:
            mock_run.return_value = find_result
            result = _copy_agent_trace("container-123", output_dir)

        assert result is False

    def test_returns_false_when_find_fails(self, tmp_path: Path) -> None:
        """Should return False gracefully when docker exec find fails."""
        output_dir = tmp_path / "results"
        output_dir.mkdir()

        find_result = MagicMock()
        find_result.returncode = 1
        find_result.stdout = ""
        find_result.stderr = "No such file or directory"

        with patch("scripts.orchestration.run_task.subprocess.run") as mock_run:
            mock_run.return_value = find_result
            result = _copy_agent_trace("container-123", output_dir)

        assert result is False

    def test_returns_false_when_docker_cp_fails(self, tmp_path: Path) -> None:
        """Should return False when docker cp fails (e.g., permission denied)."""
        output_dir = tmp_path / "results"
        output_dir.mkdir()

        find_result = MagicMock()
        find_result.returncode = 0
        find_result.stdout = _find_stdout(
            ("1720000000.0", f"{TRACE_ROOT}/abc/conversation.jsonl")
        )

        cp_result = MagicMock()
        cp_result.returncode = 1
        cp_result.stderr = "permission denied"

        with patch("scripts.orchestration.run_task.subprocess.run") as mock_run:
            mock_run.side_effect = [find_result, cp_result]
            result = _copy_agent_trace("container-123", output_dir)

        assert result is False

    def test_merges_subagent_transcripts_with_the_session_file(
        self, tmp_path: Path
    ) -> None:
        """Subagent turns live in <session>/subagents/agent-*.jsonl, not in the
        session file. Copying only the newest file dropped every tool call a
        delegating run made, so its retrieval recall measured as if it had
        searched nothing. All files are merged, oldest first."""
        output_dir = tmp_path / "results"
        output_dir.mkdir()

        session = f"{TRACE_ROOT}/abc/conversation.jsonl"
        sub = f"{TRACE_ROOT}/abc/subagents/agent-deadbeef.jsonl"
        find_result = MagicMock()
        find_result.returncode = 0
        find_result.stdout = _find_stdout(
            ("1720000000.0", session), ("1720009999.5", sub)
        )

        with patch(
            "scripts.orchestration.run_task._docker_exec", return_value=find_result
        ), patch(
            "scripts.orchestration.run_task._docker_cp",
            side_effect=_cp_writer({session: '{"main": 1}', sub: '{"sub": 1}\n'}),
        ):
            result = _copy_agent_trace("container-123", output_dir)

        assert result is True
        lines = (output_dir / "agent_trace.jsonl").read_text().splitlines()
        # Oldest first, and the session file's missing trailing newline must
        # not glue two records into one unparseable line.
        assert lines == ['{"main": 1}', '{"sub": 1}']

    def test_output_filename_is_agent_trace_jsonl(self, tmp_path: Path) -> None:
        """Output file should be named agent_trace.jsonl."""
        output_dir = tmp_path / "results"
        output_dir.mkdir()

        find_result = MagicMock()
        find_result.returncode = 0
        find_result.stdout = _find_stdout(
            ("1720000000.0", f"{TRACE_ROOT}/abc/conversation.jsonl")
        )

        traced = f"{TRACE_ROOT}/abc/conversation.jsonl"
        with patch(
            "scripts.orchestration.run_task._docker_exec", return_value=find_result
        ), patch(
            "scripts.orchestration.run_task._docker_cp",
            side_effect=_cp_writer({traced: '{"n": 1}\n'}),
        ):
            _copy_agent_trace("container-123", output_dir)

        assert (output_dir / "agent_trace.jsonl").is_file()

    def test_handles_timeout_gracefully(self, tmp_path: Path) -> None:
        """Should return False if docker exec times out."""
        output_dir = tmp_path / "results"
        output_dir.mkdir()

        with patch("scripts.orchestration.run_task.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="docker", timeout=30)
            result = _copy_agent_trace("container-123", output_dir)

        assert result is False


class TestNewestTracePath:
    """Record selection over the NUL-delimited find payload."""

    def test_picks_highest_mtime(self) -> None:
        stdout = _find_stdout(
            ("1720000000.0", f"{TRACE_ROOT}/a/conversation.jsonl"),
            ("1720009999.5", f"{TRACE_ROOT}/b/conversation.jsonl"),
            ("1719000000.0", f"{TRACE_ROOT}/c/conversation.jsonl"),
        )

        assert _newest_trace_path(stdout) == f"{TRACE_ROOT}/b/conversation.jsonl"

    def test_preserves_spaces_in_path(self) -> None:
        """Only the mtime field is split off — a path with spaces stays intact."""
        traced = f"{TRACE_ROOT}/my repo/conversation.jsonl"

        assert _newest_trace_path(_find_stdout(("1720000000.0", traced))) == traced

    def test_returns_none_for_empty_payload(self) -> None:
        assert _newest_trace_path("") is None

    def test_skips_record_with_unparseable_mtime(self) -> None:
        stdout = (
            f"not-a-float {TRACE_ROOT}/forged/conversation.jsonl\0"
            f"1720000000.0 {TRACE_ROOT}/real/conversation.jsonl\0"
        )

        assert _newest_trace_path(stdout) == f"{TRACE_ROOT}/real/conversation.jsonl"

    def test_skips_record_with_no_separator(self) -> None:
        stdout = "garbage-with-no-space\0" + _find_stdout(
            ("1720000000.0", f"{TRACE_ROOT}/a/c.jsonl")
        )

        assert _newest_trace_path(stdout) == f"{TRACE_ROOT}/a/c.jsonl"

    @pytest.mark.parametrize(
        "hostile_path",
        [
            "/etc/passwd",
            "/home/agent/.claude/../../../etc/shadow",
            "/workspace/planted.jsonl",
            f"{TRACE_ROOT}-not-really/conversation.jsonl",
        ],
    )
    def test_rejects_path_outside_the_trace_root(self, hostile_path: str) -> None:
        """A path claiming to live outside TRACE_ROOT is dropped, not copied."""
        stdout = _find_stdout(("9999999999.0", hostile_path))

        assert _newest_trace_path(stdout) is None


# A filename whose own bytes spell out a second, newer-looking find record.
# Legal on Linux (every byte but NUL and '/' is), and the agent under test owns
# the directory it would live in.
HOSTILE_NAME = f"real.jsonl\n9999999999.0 {TRACE_ROOT}/x/fabricated.jsonl"
FABRICATED = f"{TRACE_ROOT}/x/fabricated.jsonl"


class TestTraceRecordForgery:
    """The agent under test must not be able to choose its own trace.

    Newline-framed records let a crafted filename smuggle in an extra
    `<mtime> <path>` record; a huge mtime then made it sort first, so the
    forged path was copied out as the authoritative trace. NUL framing removes
    the ambiguity, and the TRACE_ROOT prefix check is the backstop.
    """

    def test_forged_record_loses_to_the_genuine_newest(self) -> None:
        stdout = _find_stdout(
            ("1720000000.0", f"{TRACE_ROOT}/x/{HOSTILE_NAME}"),
            ("1720000001.0", f"{TRACE_ROOT}/x/genuine.jsonl"),
        )

        picked = _newest_trace_path(stdout)

        # The hostile entry stays one record carrying its real mtime, so the
        # genuine newer session wins and the smuggled path is never selected.
        assert picked == f"{TRACE_ROOT}/x/genuine.jsonl"
        assert picked != FABRICATED

    def test_forged_record_is_not_extractable_when_alone(self) -> None:
        stdout = _find_stdout(("1720000000.0", f"{TRACE_ROOT}/x/{HOSTILE_NAME}"))

        picked = _newest_trace_path(stdout)

        # Resolves to the single real (weirdly-named) file, never to the path
        # its name was trying to smuggle in.
        assert picked == f"{TRACE_ROOT}/x/{HOSTILE_NAME}"
        assert picked != FABRICATED
