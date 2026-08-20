"""Cross-harness contract tests for the Code Finder benchmark arms."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.harnesses.claude.mcp.sourcegraph import build_system_prompt
from agents.harnesses.registry import build_harness_plan
from scripts.orchestration import mode_gate, run_task


REPOS = [
    {
        "url": "https://github.com/pallets/flask",
        "rev": "3.1.0",
        "path": "flask",
    }
]


@pytest.mark.parametrize("harness", ["codex", "opencode"])
@pytest.mark.parametrize("mode", ["mcp_code_finder", "mcp_assisted"])
def test_generated_harnesses_support_finder_modes(harness: str, mode: str) -> None:
    model = "gpt-5.6-sol" if harness == "codex" else "openrouter/moonshotai/kimi-k3"

    plan = build_harness_plan(harness, model=model, mode=mode)

    if harness == "codex":
        assert "--ignore-user-config" not in plan.command
    assert plan.model == model


@pytest.mark.parametrize("mode", ["mcp_code_finder", "mcp_assisted"])
def test_finder_modes_are_registered_and_filesystem_gated(mode: str) -> None:
    assert mode in run_task.VALID_MODES
    assert mode in mode_gate.GATED_MODES


def test_forced_finder_prompt_requires_one_call_per_repo_and_forbids_direct_tools() -> (
    None
):
    prompt = build_system_prompt("mcp_code_finder", REPOS)

    assert "`code_finder`" in prompt
    assert "exactly once per repository" in prompt
    assert "Do not call" in prompt
    assert "`read_file`" in prompt
    assert "repo:^github.com/sg-evals/flask--3.1.0$" in prompt


def test_assisted_finder_prompt_requires_bootstrap_then_allows_follow_up() -> None:
    prompt = build_system_prompt("mcp_assisted", REPOS)

    assert "`code_finder`" in prompt
    assert "at least once" in prompt
    assert "targeted follow-up" in prompt
    assert "`read_file`" in prompt


def test_direct_mcp_prompt_prohibits_code_finder_to_keep_arms_distinct() -> None:
    prompt = build_system_prompt("mcp_only", REPOS)

    assert "Do not call `code_finder`" in prompt
    assert "direct Sourcegraph retrieval" in prompt


@pytest.mark.parametrize("mode", ["mcp_code_finder", "mcp_assisted"])
def test_instruction_builder_injects_finder_prompt(
    tmp_path: Path,
    mode: str,
) -> None:
    (tmp_path / "instruction.md").write_text("Find the root cause.")

    instruction = run_task._build_instruction_text(tmp_path, mode, REPOS)

    assert instruction is not None
    assert "code_finder" in instruction
    assert "Find the root cause." in instruction


def test_forced_finder_validity_failure_routes_run_to_infra_error() -> None:
    result = run_task.TaskRunResult(
        task_id="cal-err-flask-blueprint-001",
        tool_usage={
            "mcp_tool_calls": 1,
            "retrieval": {
                "valid": False,
                "invalid_reason": "expected 1 code_finder call, observed 0",
            },
        },
    )

    run_task._route_code_finder_run(result, "mcp_code_finder")

    assert result.status == run_task.RUN_STATUS_INVALID
    assert result.failure_class == "infra_code_finder_contract"
    assert "expected 1" in result.error


@pytest.mark.parametrize("mode", ["mcp_code_finder", "mcp_assisted"])
def test_finder_modes_route_mcp_clients_through_local_proxy(mode: str) -> None:
    assert run_task._mcp_endpoint_for_mode(mode) == run_task.MCP_PROXY_ENDPOINT


def test_treatment_mode_routes_through_the_local_proxy() -> None:
    """The proxy is where the unshipped server change is applied, so the
    treatment arm must talk to it rather than to Sourcegraph directly."""
    assert run_task._mcp_endpoint_for_mode("mcp_treatment") == (
        run_task.MCP_PROXY_ENDPOINT
    )


def test_treatment_mode_is_not_held_to_the_finder_call_contract() -> None:
    """mcp_treatment measures whether the agent chooses code_finder at all, so
    a run that never calls it is a result, not an invalid run."""
    assert "mcp_treatment" not in run_task.FINDER_MODES


def test_default_and_treatment_arms_differ_only_in_the_served_server() -> None:
    assert "mcp_default" not in run_task.PROXY_MODES
    assert "mcp_treatment" in run_task.PROXY_MODES


@pytest.mark.parametrize("mode", ["mcp_only", "hybrid", "mcp_default"])
def test_existing_mcp_modes_still_use_sourcegraph_directly(mode: str) -> None:
    assert run_task._mcp_endpoint_for_mode(mode) == run_task.SOURCEGRAPH_MCP_ENDPOINT


def test_expected_sourcegraph_repositories_use_exact_mirror_names() -> None:
    repos = [
        {
            "url": "https://github.com/pallets/flask",
            "rev": "2.3.3",
            "path": "flask",
        },
        {
            "url": "https://github.com/pallets/werkzeug",
            "rev": "2.3.7",
            "path": "werkzeug",
        },
    ]

    assert run_task._expected_sourcegraph_repositories(repos) == [
        "github.com/sg-evals/flask--2.3.3",
        "github.com/sg-evals/werkzeug--2.3.7",
    ]


def test_code_finder_cost_leaves_client_cost_intact():
    """Server-side Finder charges are reported beside `cost_usd`, never inside it."""
    cost = run_task.code_finder_cost(1.00, 5)

    assert cost["calls"] == 5
    assert cost["finder_token_cost_usd"] == pytest.approx(0.10)
    assert cost["finder_credit_cost_usd"] == pytest.approx(0.55)
    assert cost["cost_usd_with_finder_tokens"] == pytest.approx(1.10)
    assert cost["cost_usd_customer_credits"] == pytest.approx(1.55)


def test_code_finder_cost_is_zero_for_arms_that_never_call_it():
    cost = run_task.code_finder_cost(0.42, 0)

    assert cost["finder_token_cost_usd"] == 0.0
    assert cost["finder_credit_cost_usd"] == 0.0
    assert cost["cost_usd_with_finder_tokens"] == pytest.approx(0.42)
    assert cost["cost_usd_customer_credits"] == pytest.approx(0.42)


def test_code_finder_cost_rejects_a_negative_call_count():
    with pytest.raises(ValueError, match="non-negative"):
        run_task.code_finder_cost(1.0, -1)
