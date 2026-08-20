"""Unit tests for benchmark agent harness planning."""

from __future__ import annotations

import pytest

from agents.harnesses import registry
from agents.harnesses.registry import (
    CODEX_NPM_PACKAGE,
    OPENCODE_NPM_PACKAGE,
    HarnessConfigurationError,
    build_harness_plan,
    harness_variant_label,
)


def test_codex_plan_uses_real_noninteractive_cli() -> None:
    plan = build_harness_plan("codex", model="gpt-5.6-sol", mode="baseline")

    assert plan.command == (
        "codex",
        "exec",
        "--model",
        "gpt-5.6-sol",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--skip-git-repo-check",
        "--dangerously-bypass-approvals-and-sandbox",
        "-",
    )
    assert plan.binary == "codex"
    assert plan.npm_package == CODEX_NPM_PACKAGE


def test_codex_mcp_plan_loads_isolated_config() -> None:
    plan = build_harness_plan("codex", model="gpt-5.6-sol", mode="mcp_only")

    assert plan.command == (
        "codex",
        "exec",
        "--model",
        "gpt-5.6-sol",
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
        "--dangerously-bypass-approvals-and-sandbox",
        "-",
    )
    assert "--ignore-user-config" not in plan.command


def test_codex_cli_plan_ignores_mcp_user_config() -> None:
    plan = build_harness_plan("codex", model="gpt-5.6-sol", mode="cli")

    assert "--ignore-user-config" in plan.command


def test_opencode_plan_uses_explicit_provider_model_and_auto_permissions() -> None:
    plan = build_harness_plan(
        "opencode",
        model="openrouter/deepseek/deepseek-v4-pro",
        mode="baseline",
    )

    assert plan.command == (
        "opencode",
        "run",
        "--model",
        "openrouter/deepseek/deepseek-v4-pro",
        "--format",
        "json",
        "--pure",
        "--auto",
    )
    assert plan.binary == "opencode"
    assert plan.npm_package == OPENCODE_NPM_PACKAGE
    assert plan.required_env == ("OPENROUTER_API_KEY",)


def test_opencode_mcp_plan_preserves_noninteractive_cli_contract() -> None:
    plan = build_harness_plan(
        "opencode",
        model="openrouter/moonshotai/kimi-k3",
        mode="mcp_only",
    )

    assert plan.command == (
        "opencode",
        "run",
        "--model",
        "openrouter/moonshotai/kimi-k3",
        "--format",
        "json",
        "--pure",
        "--auto",
    )


def test_opencode_cli_plan_preserves_noninteractive_cli_contract() -> None:
    plan = build_harness_plan(
        "opencode",
        model="openrouter/moonshotai/kimi-k3",
        mode="cli",
    )

    assert plan.command == (
        "opencode",
        "run",
        "--model",
        "openrouter/moonshotai/kimi-k3",
        "--format",
        "json",
        "--pure",
        "--auto",
    )


@pytest.mark.parametrize("harness", ["codex", "opencode"])
def test_new_harnesses_require_explicit_models(harness: str) -> None:
    with pytest.raises(HarnessConfigurationError, match="requires --model"):
        build_harness_plan(harness, model=None, mode="baseline")


@pytest.mark.parametrize(
    ("harness", "mode"),
    [
        ("codex", "hybrid"),
        ("opencode", "hybrid"),
    ],
)
def test_new_harnesses_fail_closed_on_claude_specific_modes(
    harness: str, mode: str
) -> None:
    with pytest.raises(HarnessConfigurationError, match="does not support mode"):
        build_harness_plan(
            harness,
            model=(
                "gpt-5.6-sol"
                if harness == "codex"
                else "openrouter/openai/gpt-oss-120b"
            ),
            mode=mode,
        )


def test_opencode_initially_requires_openrouter_model_ids() -> None:
    with pytest.raises(HarnessConfigurationError, match="openrouter/"):
        build_harness_plan("opencode", model="ollama/qwen3-coder", mode="baseline")


def test_generated_harness_rejects_legacy_agent_override() -> None:
    with pytest.raises(HarnessConfigurationError, match="cannot be combined"):
        build_harness_plan(
            "codex",
            model="gpt-5.6-sol",
            mode="baseline",
            agent_command="codex exec -",
        )


def test_claude_preserves_legacy_agent_command() -> None:
    plan = build_harness_plan(
        "claude",
        model=None,
        mode="baseline",
        agent_command="claude -p",
    )
    assert plan.command == ("claude", "-p")
    assert plan.npm_package is None


@pytest.mark.parametrize(
    ("harness", "model", "expected"),
    [
        ("codex", "gpt-5.6-sol", "codex-gpt-5-6-sol-2507d95681"),
        (
            "opencode",
            "openrouter/deepseek/deepseek-v4-pro",
            "opencode-openrouter-deepseek-deepseek-v4-pro-600d7d9311",
        ),
    ],
)
def test_generated_harness_has_stable_run_label(
    harness: str, model: str, expected: str
) -> None:
    assert harness_variant_label(harness, model) == expected


def test_long_model_ids_get_collision_resistant_variant_labels() -> None:
    shared_prefix = "openrouter/vendor/" + ("model-segment-" * 12)

    first = harness_variant_label("opencode", shared_prefix + "a")
    second = harness_variant_label("opencode", shared_prefix + "b")

    assert first != second
    assert len(first) <= 96
    assert len(second) <= 96


def test_separator_normalization_cannot_collapse_distinct_model_ids() -> None:
    first = harness_variant_label("opencode", "openrouter/foo/bar-baz")
    second = harness_variant_label("opencode", "openrouter/foo-bar/baz")

    assert first != second
    assert first.startswith("opencode-openrouter-foo-bar-baz-")
    assert second.startswith("opencode-openrouter-foo-bar-baz-")


def test_case_distinct_model_ids_cannot_overwrite_each_other() -> None:
    first = harness_variant_label("opencode", "openrouter/Foo/Model")
    second = harness_variant_label("opencode", "openrouter/foo/model")

    assert first != second


class TestClaudeModelReachesTheCli:
    """`--model` must change the command, not just the recorded metadata.

    The claude branch used to store `model` on the plan while building the
    command purely from `--agent`, so a sweep dispatched with
    `--model claude-sonnet-5` recorded Sonnet in results.json and ran whatever
    the CLI defaulted to. Nothing downstream could detect the mismatch, which
    makes it a provenance claim the artifacts cannot support -- the same class
    of defect the scoring trust boundary exists to prevent.
    """

    _AGENT_COMMAND = "claude --dangerously-skip-permissions -p"

    def test_model_is_passed_to_the_binary(self) -> None:
        plan = build_harness_plan(
            "claude",
            model="claude-sonnet-5",
            mode="baseline",
            agent_command=self._AGENT_COMMAND,
        )

        assert "--model" in plan.command
        assert plan.command[plan.command.index("--model") + 1] == "claude-sonnet-5"

    def test_model_precedes_the_prompt_flag(self) -> None:
        """`-p` consumes the prompt, so nothing may follow it."""
        plan = build_harness_plan(
            "claude",
            model="claude-sonnet-5",
            mode="baseline",
            agent_command=self._AGENT_COMMAND,
        )

        assert plan.command.index("--model") < plan.command.index("-p")
        assert plan.command[0] == "claude"

    def test_two_models_produce_two_commands(self) -> None:
        def command_for(model: str) -> tuple[str, ...]:
            return build_harness_plan(
                "claude",
                model=model,
                mode="baseline",
                agent_command=self._AGENT_COMMAND,
            ).command

        assert command_for("claude-opus-5") != command_for("claude-sonnet-5")

    def test_no_model_leaves_the_command_untouched(self) -> None:
        plan = build_harness_plan(
            "claude",
            model=None,
            mode="baseline",
            agent_command=self._AGENT_COMMAND,
        )

        assert plan.command == tuple(self._AGENT_COMMAND.split())
        assert "--model" not in plan.command


class TestTheDefaultClaudeCommandCarriesTheModel:
    """--model must reach the CLI on the path that actually runs in sweeps.

    The earlier fix only covered an explicit --agent. The default command was
    still assembled inside run_task, so every unattended run took the CLI
    default: results.json recorded model=claude-opus-5 for 40 runs whose traces
    all say claude-sonnet-5.
    """

    def test_default_command_is_used_when_no_agent_is_given(self) -> None:
        plan = build_harness_plan("claude", model=None, mode="baseline")

        assert plan.command == registry.DEFAULT_CLAUDE_COMMAND

    def test_model_reaches_the_default_command(self) -> None:
        plan = build_harness_plan("claude", model="claude-sonnet-5", mode="baseline")

        assert plan.command[:3] == ("claude", "--model", "claude-sonnet-5")
        assert plan.command[-1] == "-p", "the prompt flag must stay last"

    def test_the_default_command_still_ends_in_the_prompt_flag(self) -> None:
        assert registry.DEFAULT_CLAUDE_COMMAND[-1] == "-p"


def test_turn_cap_stays_clear_of_the_arms_it_would_censor():
    """The turn budget is a runaway guard, not a limit any arm should reach.

    At 50 it truncated the baseline and direct-MCP arms, whose over-exploration
    is the thing those arms are measured for. Keep it far above their observed
    range so a high turn count is recorded rather than discarded.
    """
    assert registry.MAX_AGENT_TURNS >= 150

    command = registry.DEFAULT_CLAUDE_COMMAND
    flag = command.index("--max-turns")
    assert command[flag + 1] == str(registry.MAX_AGENT_TURNS)


def test_codex_reasoning_effort_reaches_the_cli():
    """Effort must be pinned in the command, not left to the CLI default."""
    plan = registry.build_harness_plan(
        "codex", model="gpt-5.6-sol", mode="baseline", reasoning_effort="medium"
    )

    assert "-c" in plan.command
    assert 'model_reasoning_effort="medium"' in plan.command


def test_codex_rejects_an_unknown_reasoning_effort():
    with pytest.raises(registry.HarnessConfigurationError, match="reasoning effort"):
        registry.build_harness_plan(
            "codex", model="gpt-5.6-sol", mode="baseline", reasoning_effort="ultra"
        )


def test_reasoning_effort_is_refused_for_harnesses_that_ignore_it():
    """Silently dropping it would record an effort the run never used."""
    with pytest.raises(registry.HarnessConfigurationError, match="codex option"):
        registry.build_harness_plan(
            "claude", model="claude-sonnet-5", mode="baseline", reasoning_effort="medium"
        )


def test_codex_command_is_unchanged_when_no_effort_is_given():
    plan = registry.build_harness_plan("codex", model="gpt-5.6-sol", mode="baseline")

    assert "-c" not in plan.command
