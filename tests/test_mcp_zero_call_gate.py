"""Tests for the zero-MCP-call gate and trace/status integrity in run_task.py.

``mcp_only`` denies local source at the filesystem, so a run there that made 0
MCP tool calls never retrieved source at all. It is marked INVALID and routed to
the infra-error re-run channel.

The run is marked ``status="invalid"``; the analysis normalizes the case before
comparison (``aggregate_mcp_clean.load_mode``, EnterpriseBench-te9ah), so a
routed run classes INVALID and is excluded from the S6.2 headline. These tests
assert the routing this module owns; the exclusion itself is te9ah's to prove.

``hybrid`` is deliberately NOT gated: that mode grants both toolsets, so 0 MCP
calls is a legitimate agent choice. Invalidating those runs would delete
exactly the cases where the agent preferred local tools and would bias the
hybrid mean upward.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "scripts" / "orchestration")
)

import run_task
from agents.harnesses import registry
from run_task import (
    RUN_STATUS_INVALID,
    TaskRunConfig,
    TaskRunResult,
    _record_agent_trace,
    _route_zero_mcp_run,
    _save_results,
)


def _result(mcp_calls: int, mcp_errors: int = 0) -> TaskRunResult:
    return TaskRunResult(
        task_id="incident-investigation-002",
        phase="",
        tool_usage={"mcp_tool_calls": mcp_calls, "mcp_tool_errors": mcp_errors},
    )


class TestAllMcpCallsErrored:
    """Calls the server refused retrieved nothing, so they cannot clear the gate.

    A depleted-credit or expired-token backend returns 403 to every call while
    the agent keeps emitting tool_use blocks. Counting attempts would let a
    total retrieval outage score as a measured agent failure.
    """

    def test_every_call_errored_is_invalid(self) -> None:
        result = _result(5, mcp_errors=5)

        _route_zero_mcp_run(result, "mcp_only")

        assert result.status == RUN_STATUS_INVALID
        assert result.success is False
        assert result.failure_class == "infra_mcp_backend_error"
        assert result.tool_usage["mcp_used"] is False
        assert "never live" in result.error

    def test_reason_is_distinct_from_the_unused_gate(self) -> None:
        errored = _result(5, mcp_errors=5)
        unused = _result(0)

        _route_zero_mcp_run(errored, "mcp_only")
        _route_zero_mcp_run(unused, "mcp_only")

        assert errored.failure_class == "infra_mcp_backend_error"
        assert unused.failure_class == "infra_mcp_unused"

    def test_one_served_call_still_clears_the_gate(self) -> None:
        result = _result(5, mcp_errors=4)

        _route_zero_mcp_run(result, "mcp_only")

        assert result.status != RUN_STATUS_INVALID
        assert result.failure_class is None
        assert result.tool_usage["mcp_used"] is True

    def test_a_more_specific_root_cause_is_not_relabelled(self) -> None:
        result = _result(3, mcp_errors=3)
        result.failure_class = "timeout"

        _route_zero_mcp_run(result, "mcp_only")

        assert result.status == RUN_STATUS_INVALID
        assert result.failure_class == "timeout"


class TestMcpErrorAccounting:
    """mcp_tool_errors joins errored results back to the MCP calls that made them."""

    def test_errored_mcp_result_is_counted(self, tmp_path: Path) -> None:
        stream = _assistant(_tool_use("mcp__toast__toast_search")) + "\n" + _record(
            "user",
            {
                "type": "tool_result",
                "tool_use_id": "toolu_01ABC",
                "is_error": True,
                "content": "PermissionDeniedError: Error code: 403",
            },
        )

        usage = _usage(tmp_path, stream)

        assert usage["mcp_tool_calls"] == 1
        assert usage["mcp_tool_errors"] == 1

    def test_a_failing_non_mcp_tool_is_not_charged_to_mcp(
        self, tmp_path: Path
    ) -> None:
        stream = _assistant(
            {"type": "tool_use", "id": "toolu_01BASH", "name": "Bash", "input": {}}
        ) + "\n" + _record(
            "user",
            {
                "type": "tool_result",
                "tool_use_id": "toolu_01BASH",
                "is_error": True,
                "content": "Permission denied",
            },
        )

        usage = _usage(tmp_path, stream)

        assert usage["mcp_tool_errors"] == 0

    def test_a_served_mcp_call_records_no_error(self, tmp_path: Path) -> None:
        stream = _assistant(_tool_use("mcp__toast__toast_search")) + "\n" + _record(
            "user",
            {
                "type": "tool_result",
                "tool_use_id": "toolu_01ABC",
                "content": "src/lib.rs:1",
            },
        )

        usage = _usage(tmp_path, stream)

        assert usage["mcp_tool_calls"] == 1
        assert usage["mcp_tool_errors"] == 0


class TestZeroMcpGateMcpOnly:
    """mcp_only with 0 MCP calls is an infra failure, not a score."""

    def test_marks_run_invalid(self) -> None:
        result = _result(0)

        _route_zero_mcp_run(result, "mcp_only")

        assert result.status == RUN_STATUS_INVALID
        assert result.failure_class == "infra_mcp_unused"
        assert result.success is False
        assert result.tool_usage["mcp_used"] is False

    def test_phase_lands_outside_complete(self) -> None:
        """phase must land in NON_COMPLETE_PHASES, which is what run_task keys on.

        The phase is what stops run_task's own save-time override from
        rewriting this result to phase="complete", success=True. That is the
        whole of what this test claims.

        Headline exclusion is a separate question, owned elsewhere: the locked
        clean-set path (recompute_headline_uu17.locked_clean, via
        aggregate_mcp_clean.load_mode) classifies on ``status`` and, since
        EnterpriseBench-te9ah normalized the case, an ``"invalid"`` run classes
        INVALID and is excluded. This test asserts only the phase override guard.
        """
        result = _result(0)

        _route_zero_mcp_run(result, "mcp_only")

        assert result.phase == "agent_infra_error"
        assert result.phase != "complete"

    def test_records_a_reason_on_the_result(self) -> None:
        result = _result(0)

        _route_zero_mcp_run(result, "mcp_only")

        assert "mcp_only" in result.error
        assert result.error != ""

    def test_does_not_relabel_an_already_invalid_run(self) -> None:
        """A broken MCP config is the root cause; 0 calls is just its symptom.

        The config gate runs first and classifies the run infra_mcp_config. The
        agent then makes 0 MCP calls *because* of it, and relabelling the run
        infra_mcp_unused would bury the actual cause in the re-run channel.
        """
        result = _result(0)
        result.status = RUN_STATUS_INVALID
        result.failure_class = "infra_mcp_config"
        result.phase = "agent_infra_error"

        _route_zero_mcp_run(result, "mcp_only")

        assert result.failure_class == "infra_mcp_config"
        assert result.status == RUN_STATUS_INVALID
        assert result.tool_usage["mcp_used"] is False

    @pytest.mark.parametrize(
        "root_cause",
        ["infra_oom", "infra_timeout", "agent_error"],
    )
    def test_preserves_a_root_cause_that_never_set_status(
        self, root_cause: str
    ) -> None:
        """OOM / timeout / crash set failure_class but NOT status.

        Each of those trivially produces 0 MCP calls, so a status-only guard
        would relabel them infra_mcp_unused and send triage chasing a phantom
        MCP problem. The run must still be excluded, but keep its real cause.
        """
        result = _result(0)
        result.failure_class = root_cause

        _route_zero_mcp_run(result, "mcp_only")

        assert result.failure_class == root_cause
        assert result.status == RUN_STATUS_INVALID  # still excluded
        assert result.phase == "agent_infra_error"
        assert result.success is False

    def test_run_with_mcp_calls_is_untouched(self) -> None:
        result = _result(7)

        _route_zero_mcp_run(result, "mcp_only")

        assert result.status == ""
        assert result.phase == ""
        assert result.failure_class is None
        assert result.tool_usage["mcp_used"] is True


class TestZeroMcpGateHybrid:
    """hybrid grants both toolsets — 0 MCP calls stays a scoreable run."""

    def test_zero_calls_flagged_but_still_scored(self) -> None:
        result = _result(0)

        _route_zero_mcp_run(result, "hybrid")

        assert result.tool_usage["mcp_used"] is False
        assert result.status == ""
        assert result.phase == ""
        assert result.failure_class is None
        assert result.success is False  # untouched default, set later by the run

    def test_calls_present_marks_mcp_used(self) -> None:
        result = _result(3)

        _route_zero_mcp_run(result, "hybrid")

        assert result.tool_usage["mcp_used"] is True
        assert result.status == ""


class TestZeroMcpGateBaseline:
    """baseline has no MCP at all — the gate must not touch it."""

    @pytest.mark.parametrize("mcp_calls", [0, 4])
    def test_baseline_is_never_gated_or_flagged(self, mcp_calls: int) -> None:
        result = _result(mcp_calls)

        _route_zero_mcp_run(result, "baseline")

        assert result.status == ""
        assert result.phase == ""
        assert result.failure_class is None
        assert "mcp_used" not in result.tool_usage


class TestRecordAgentTrace:
    """The trace-capture result must be recorded, not discarded."""

    def test_successful_capture_records_true(self, tmp_path: Path) -> None:
        result = _result(1)

        with patch.object(run_task, "_copy_agent_trace", return_value=True):
            _record_agent_trace(result, "container-123", tmp_path)

        assert result.tool_usage["trace_captured"] is True

    def test_missing_trace_records_false(self, tmp_path: Path) -> None:
        """A missing trace must be visible on the result, not silently pass."""
        result = _result(1)

        with patch.object(run_task, "_copy_agent_trace", return_value=False):
            _record_agent_trace(result, "container-123", tmp_path)

        assert result.tool_usage["trace_captured"] is False


class TestStatusIsPersisted:
    """An INVALID run must be identifiable from the artifacts alone."""

    @staticmethod
    def _save(tmp_path: Path, result: TaskRunResult) -> Path:
        config = TaskRunConfig(task_toml=tmp_path / "task.toml", mode="mcp_only")
        _save_results(result, {}, tmp_path, config)
        return tmp_path

    def test_invalid_status_lands_in_both_artifacts(self, tmp_path: Path) -> None:
        result = _result(0)
        _route_zero_mcp_run(result, "mcp_only")

        out = self._save(tmp_path, result)

        results = json.loads((out / "results.json").read_text())
        metrics = json.loads((out / "task_metrics.json").read_text())
        assert results["status"] == RUN_STATUS_INVALID
        assert metrics["status"] == RUN_STATUS_INVALID
        assert results["failure_class"] == "infra_mcp_unused"
        assert results["tool_usage"]["mcp_used"] is False

    def test_valid_run_persists_empty_status(self, tmp_path: Path) -> None:
        result = _result(5)
        _route_zero_mcp_run(result, "mcp_only")
        result.phase = "complete"
        result.success = True

        out = self._save(tmp_path, result)

        results = json.loads((out / "results.json").read_text())
        assert results["status"] == ""
        assert results["phase"] == "complete"

    @pytest.mark.parametrize(
        "phase",
        [
            # inline-flagged phases that reach the completion guard
            "agent_infra_error",
            "verifier_infra_error",
            # early-returning failure phases that set only phase/success — these
            # are exactly the ones an allow-list version of the field missed
            "agent_preflight_failed",
            "preflight_failed",
            "build_failed",
            "setup_failed",
            "mcp_infra_error",
            "error",
        ],
    )
    def test_infra_phase_persists_invalid_even_when_status_unset(
        self, tmp_path: Path, phase: str
    ) -> None:
        """status is derived from phase/success, so any failed-short-of-complete
        run lands as INVALID on disk without its branch remembering to set it.

        OOM, timeout, build-failed and setup-failed branches set only
        phase/success. Persisting the raw status field would leave those runs
        looking scoreable in the artifacts.
        """
        result = _result(3)
        result.phase = phase
        result.success = False
        assert result.status == ""  # nothing set it

        out = self._save(tmp_path, result)

        results = json.loads((out / "results.json").read_text())
        metrics = json.loads((out / "task_metrics.json").read_text())
        assert results["status"] == RUN_STATUS_INVALID
        assert metrics["status"] == RUN_STATUS_INVALID

    def test_dry_run_is_not_marked_invalid(self, tmp_path: Path) -> None:
        """A dry run completes with success=True — excluded from scoring but not
        an infra failure, so it must NOT persist status=invalid.

        This is the case a blanket 'phase != complete' rule would misclassify.
        """
        result = _result(0)
        result.phase = "dry_run_complete"
        result.success = True

        out = self._save(tmp_path, result)

        results = json.loads((out / "results.json").read_text())
        assert results["status"] == ""


def _record(kind: str, *blocks: dict) -> str:
    """A stream-json record of the given type, carrying content blocks."""
    return json.dumps({"type": kind, "message": {"content": list(blocks)}})


def _assistant(*blocks: dict) -> str:
    return _record("assistant", *blocks)


def _tool_use(name: str) -> dict:
    return {"type": "tool_use", "id": "toolu_01ABC", "name": name, "input": {}}


def _usage(tmp_path: Path, stdout: str) -> dict:
    (tmp_path / "agent_stdout.log").write_text(stdout)
    return run_task._extract_tool_usage(tmp_path)


class TestMcpToolCallCounting:
    """mcp_tool_calls counts genuine tool_use blocks — never a mention of one.

    The count feeds _route_zero_mcp_run, which invalidates an mcp_only run that
    made 0 MCP calls. Counting a mention would slip such a run past the gate.
    """

    @pytest.mark.parametrize(
        "stream",
        [
            _assistant(
                {
                    "type": "text",
                    "text": "I could use mcp__sourcegraph__search_code, but Grep is faster.",
                }
            ),
            _record(
                "user",
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": "Error: mcp__sourcegraph__search_code is not available",
                },
            ),
        ],
        ids=["narrated_in_text", "echoed_in_tool_result"],
    )
    def test_name_outside_a_tool_use_block_is_not_a_call(
        self, tmp_path: Path, stream: str
    ) -> None:
        assert _usage(tmp_path, stream)["mcp_tool_calls"] == 0

    @pytest.mark.parametrize(
        "tool_name",
        ["mcp__sourcegraph__search_code", "mcp__sourcegraph__brand_new_tool"],
        ids=["known_tool", "tool_added_after_this_shipped"],
    )
    def test_sourcegraph_tool_use_counts(self, tmp_path: Path, tool_name: str) -> None:
        assert _usage(tmp_path, _assistant(_tool_use(tool_name)))["mcp_tool_calls"] == 1

    @pytest.mark.parametrize(
        "tool_name",
        [
            "mcp__other__do_thing",
            "mcp__sourcegraphql__query",
            "mcp__sourcegraph_public__search",
            "Grep",
        ],
    )
    def test_other_tool_does_not_count(self, tmp_path: Path, tool_name: str) -> None:
        assert _usage(tmp_path, _assistant(_tool_use(tool_name)))["mcp_tool_calls"] == 0

    def test_multiple_blocks_in_one_message_all_count(self, tmp_path: Path) -> None:
        stream = _assistant(
            {"type": "text", "text": "searching now"},
            _tool_use("mcp__sourcegraph__search_code"),
            _tool_use("mcp__sourcegraph__read_file"),
        )

        assert _usage(tmp_path, stream)["mcp_tool_calls"] == 2

    def test_tool_breakdown_records_code_finder_by_name(self, tmp_path: Path) -> None:
        usage = _usage(
            tmp_path,
            _assistant(
                _tool_use("mcp__sourcegraph__code_finder"),
                _tool_use("mcp__sourcegraph__read_file"),
            ),
        )

        assert usage["mcp_tool_breakdown"] == {
            "code_finder": 1,
            "read_file": 1,
        }

    def test_direct_mcp_arm_rejects_code_finder_contamination(
        self,
        tmp_path: Path,
    ) -> None:
        result = _result(1)
        result.tool_usage["mcp_tool_breakdown"] = {"code_finder": 1}

        run_task._route_code_finder_run(result, "mcp_only")

        assert result.status == RUN_STATUS_INVALID
        assert result.failure_class == "invalid_arm_contamination"
        assert "mcp_only" in result.error


class TestUsageParsingAcrossOutputFormats:
    """Token/turn accounting must survive whatever --output-format was used."""

    def test_whole_file_json_parses_usage(self, tmp_path: Path) -> None:
        """--output-format json emits one result object and no tool_use records."""
        payload = json.dumps(
            {
                "modelUsage": {"claude": {"inputTokens": 10, "outputTokens": 5}},
                "numTurns": 3,
                "total_cost_usd": 0.25,
                "result": "used mcp__sourcegraph__search_code then read the file",
            }
        )

        usage = _usage(tmp_path, payload)

        assert usage["mcp_tool_calls"] == 0
        assert usage["num_turns"] == 3
        assert usage["total_input_tokens"] == 10
        assert usage["total_output_tokens"] == 5
        assert usage["cost_usd"] == 0.25  # falls back to total_cost_usd

    def test_stream_json_parses_usage_alongside_calls(self, tmp_path: Path) -> None:
        stream = (
            _assistant(_tool_use("mcp__sourcegraph__search_code"))
            + "\n"
            + json.dumps(
                {
                    "modelUsage": {"claude": {"inputTokens": 12, "outputTokens": 7}},
                    "num_turns": 2,
                }
            )
            + "\n"
        )

        usage = _usage(tmp_path, stream)

        assert usage["mcp_tool_calls"] == 1
        assert usage["num_turns"] == 2
        assert usage["total_input_tokens"] == 12
        assert usage["total_output_tokens"] == 7

    def test_non_json_noise_lines_are_skipped(self, tmp_path: Path) -> None:
        """Container logs interleave plain-text lines with the JSON stream."""
        stream = (
            "starting agent...\n"
            + _assistant(_tool_use("mcp__sourcegraph__read_file"))
            + "\n"
            "not json at all\n"
            '{"broken": \n'
            + json.dumps({"num_turns": 4})
            + "\n"
        )

        usage = _usage(tmp_path, stream)

        assert usage["mcp_tool_calls"] == 1
        assert usage["num_turns"] == 4

    def test_missing_log_reports_zeroes(self, tmp_path: Path) -> None:
        usage = run_task._extract_tool_usage(tmp_path)

        assert usage["mcp_tool_calls"] == 0
        assert usage["num_turns"] == 0
        assert usage["cost_usd"] == 0.0


class TestMcpToastArm:
    """The mcp_toast arm registers a different MCP server, `toast`.

    Its tool names carry the `mcp__toast__` prefix, so the call counter has to
    recognize them; otherwise every toast run counts 0 MCP calls and the gate
    above invalidates the whole arm.
    """

    def test_toast_tool_use_counts_as_an_mcp_call(self) -> None:
        record = {
            "message": {
                "content": [
                    {"type": "tool_use", "name": "mcp__toast__toast_search"},
                ]
            }
        }

        assert run_task._count_mcp_tool_calls(record) == 1
        assert run_task._mcp_tool_names(record) == ["toast_search"]

    def test_toast_mcp_tool_call_item_counts(self) -> None:
        record = {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "server": "toast",
                "tool": "toast_search",
            },
        }

        assert run_task._count_mcp_tool_calls(record) == 1

    def test_unregistered_server_is_not_counted(self) -> None:
        record = {
            "message": {
                "content": [{"type": "tool_use", "name": "mcp__github__search_code"}]
            }
        }

        assert run_task._count_mcp_tool_calls(record) == 0

    def test_zero_toast_calls_is_invalid(self) -> None:
        """Toast is this arm's only path to code, exactly as MCP is for mcp_only."""
        result = _result(0)

        _route_zero_mcp_run(result, "mcp_toast")

        assert result.status == RUN_STATUS_INVALID
        assert result.failure_class == "infra_mcp_unused"

    def test_toast_calls_present_is_untouched(self) -> None:
        result = _result(3)

        _route_zero_mcp_run(result, "mcp_toast")

        assert result.status != RUN_STATUS_INVALID
        assert result.tool_usage["mcp_used"] is True


class TestTelemetryProxyStaging:
    """The Finder arms' capture proxy must install as root, not as the agent.

    /usr/local/lib is root-owned on some base images (node) and agent-owned on
    others (python), so an unprivileged mkdir made mcp_code_finder runnable or
    not according to which language a task's repos happened to need --
    refactor-orchestration-tri-babel-001 failed MCP pre-flight for exactly this
    reason while the python-repo tasks in the same sweep passed.
    """

    def test_install_dir_is_created_as_root(self) -> None:
        calls: list[dict] = []

        def fake_exec(container_id, argv, user=None, **kwargs):
            calls.append({"argv": argv, "user": user})
            return SimpleNamespace(returncode=1, stdout="", stderr="denied")

        with patch.object(run_task, "_docker_exec", side_effect=fake_exec):
            assert run_task._start_mcp_telemetry_proxy("c1") is False

        mkdirs = [c for c in calls if c["argv"][0] == "mkdir"]
        assert mkdirs, "proxy staging never created its install directory"
        assert mkdirs[0]["user"] == "root"


class TestMcpAuthHeaderStaging:
    """The staged auth header must be readable by the user curl runs as.

    docker cp preserves the host tempfile's 0600 mode and maps it to the host
    uid (1000). `agent` is uid 1000 on the python base images but 1001 on the
    node ones, where the copy lands owned by `node` and curl silently sends no
    Authorization header -- surfacing as a 401 that reads as an expired token.
    refactor-orchestration-tri-babel-001 failed MCP pre-flight this way while
    python-repo tasks in the same sweep passed.
    """

    def test_header_file_is_chowned_to_the_exec_user_as_root(self) -> None:
        calls: list[dict] = []

        def fake_exec(container_id, argv, user=None, **kwargs):
            calls.append({"argv": argv, "user": user})
            if argv[:2] == ["id", "-u"]:
                return SimpleNamespace(returncode=0, stdout="1001\n", stderr="")
            if argv[:2] == ["id", "-g"]:
                return SimpleNamespace(returncode=0, stdout="1001\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="200", stderr="")

        with (
            patch.object(run_task, "_docker_exec", side_effect=fake_exec),
            patch.object(run_task, "_docker_cp"),
        ):
            assert run_task._verify_mcp_endpoint("c1", "tok") is True

        chowns = [c for c in calls if c["argv"][0] == "chown"]
        assert chowns, "auth header was never handed to the exec user"
        assert chowns[0]["argv"][1] == "1001:1001"
        assert chowns[0]["user"] == "root"

        chmods = [c for c in calls if c["argv"][0] == "chmod"]
        assert chmods and chmods[0]["argv"][1] == "600", "header must stay 0600"
        assert chmods[0]["user"] == "root"


class TestTreatmentOverlayStaging:
    """The overlay module must reach the container, and be switched on there.

    The proxy imports it by name from its own directory. Staged without the
    env flag it would be inert; the flag set without the file would abort the
    proxy at startup. Either way the arm would stop measuring the server
    change, so both halves are asserted.
    """

    @staticmethod
    def _run(mode: str) -> tuple[list[tuple[str, str]], dict]:
        copies: list[tuple[str, str]] = []
        env: dict = {}

        def fake_exec(container_id, argv, user=None, **kwargs):
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        def fake_detached(container_id, argv, env=None, **kwargs):
            env_seen.update(env or {})
            return SimpleNamespace(returncode=1, stdout="", stderr="stopped here")

        env_seen = env
        with (
            patch.object(run_task, "_docker_exec", side_effect=fake_exec),
            patch.object(
                run_task,
                "_docker_cp",
                side_effect=lambda src, dst: copies.append((src, dst)),
            ),
            patch.object(run_task, "_docker_exec_detached", side_effect=fake_detached),
        ):
            run_task._start_mcp_telemetry_proxy("c1", mode)
        return copies, env

    def test_treatment_mode_stages_the_overlay_and_enables_it(self) -> None:
        copies, env = self._run("mcp_treatment")

        assert any(
            destination.endswith("sourcegraph_server_treatment.py")
            for _source, destination in copies
        )
        assert env["MCP_PROXY_TREATMENT"] == "1"

    def test_finder_modes_stage_no_overlay_and_leave_it_off(self) -> None:
        copies, env = self._run("mcp_code_finder")

        assert not any(
            destination.endswith("sourcegraph_server_treatment.py")
            for _source, destination in copies
        )
        assert env["MCP_PROXY_TREATMENT"] == "0"


class TestDeferredToolSchemaGate:
    """An MCP arm must not be scored when the agent never saw the tool text.

    Claude Code defers MCP tool schemas by default: the model gets names and
    loads descriptions on request. Every run in the first retrieval sweep did
    exactly that and none selected code_finder, so "the agent never chose Code
    Finder" was measured against a toolset whose descriptions were absent from
    its context. The env in full_tool_schema_env prevents it; this gate proves
    the prevention held rather than assuming it.
    """

    @staticmethod
    def _trace(tmp_path: Path, tool_name: str) -> Path:
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": tool_name, "input": {}},
                    ]
                },
            }
        )
        (tmp_path / "agent_trace.jsonl").write_text(line + "\n")
        return tmp_path

    def test_a_deferred_schema_run_is_invalid(self, tmp_path) -> None:
        result = _result(5)
        run_task._route_deferred_tool_run(
            result, "mcp_default", "claude", self._trace(tmp_path, "ToolSearch")
        )

        assert result.status == RUN_STATUS_INVALID
        assert result.failure_class == "invalid_deferred_tool_schemas"
        assert result.success is False

    def test_an_undeferred_run_is_untouched(self, tmp_path) -> None:
        result = _result(5)
        run_task._route_deferred_tool_run(
            result, "mcp_default", "claude", self._trace(tmp_path, "Bash")
        )

        assert result.status != RUN_STATUS_INVALID
        assert result.failure_class is None

    def test_baseline_is_not_gated(self, tmp_path) -> None:
        result = _result(5)
        run_task._route_deferred_tool_run(
            result, "baseline", "claude", self._trace(tmp_path, "ToolSearch")
        )

        assert result.status != RUN_STATUS_INVALID

    def test_a_harness_without_deferral_passes_without_a_trace(self, tmp_path) -> None:
        result = _result(5)
        run_task._route_deferred_tool_run(result, "mcp_default", "codex", tmp_path)

        assert result.status != RUN_STATUS_INVALID

    def test_every_harness_declares_both_halves_of_the_contract(self) -> None:
        for harness in registry.HARNESS_NAMES:
            registry.full_tool_schema_env(harness)
            registry.deferred_tool_markers(harness)

    def test_claude_runs_are_launched_with_deferral_off(self) -> None:
        assert registry.full_tool_schema_env("claude") == {
            "ENABLE_TOOL_SEARCH": "false"
        }
