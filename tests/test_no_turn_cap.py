"""The benchmark does not cap agent turns.

A fixed `--max-turns 50` truncated agents mid-task and did not truncate them
evenly across arms: the cli arm reaches Sourcegraph through `sgx`, a shell
command, so every retrieval costs a Bash turn, while mcp_only calls
`mcp__sourcegraph__*` directly. Equal turn budgets therefore bought unequal
amounts of retrieval, which lands directly on the cli-vs-mcp_only contrast the
tool-access comparison rests on. dead-code-001 exhausted the budget in baseline
and cli while mcp_only finished inside it.

The detection path for a truncated run is deliberately kept (see
`_scan_agent_max_turns`): if the SDK ever applies a default cap of its own, the
run must still be a scored outcome rather than an INVALID re-run, which would
censor the weakest cells upward.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "orchestration"))
sys.path.insert(0, str(REPO_ROOT / "lib"))

from run_task import (  # noqa: E402
    DEFAULT_AGENT_TIMEOUT_S,
    DEFAULT_OAUTH_AGENT_COMMAND,
    TaskRunConfig,
    _apply_disallowed_tools,
    _apply_model_flag,
    _scan_agent_max_turns,
    build_parser,
)


def test_default_agent_command_sets_no_turn_cap():
    assert "--max-turns" not in DEFAULT_OAUTH_AGENT_COMMAND


def test_default_agent_command_keeps_its_other_flags():
    """Removing the cap must not disturb the trace/stream contract."""
    for flag in ("--verbose", "--output-format stream-json", "-p"):
        assert flag in DEFAULT_OAUTH_AGENT_COMMAND


def test_model_pin_does_not_reintroduce_a_cap():
    cmd = _apply_model_flag(DEFAULT_OAUTH_AGENT_COMMAND, "claude-sonnet-5")
    assert "--max-turns" not in cmd


def test_tool_allowlist_does_not_reintroduce_a_cap():
    cmd = _apply_disallowed_tools(DEFAULT_OAUTH_AGENT_COMMAND)
    assert "--max-turns" not in cmd


def test_truncation_is_still_detected_if_the_sdk_caps_on_its_own(tmp_path):
    """Uncapping is a flag change, not a promise the SDK never truncates.

    The detector has to keep working, because it is what routes a truncated
    run to a scored outcome instead of the infra re-run channel.
    """
    (tmp_path / "agent_stdout.log").write_text(
        '{"type":"result","subtype":"error_max_turns","num_turns":51}\n'
    )
    assert _scan_agent_max_turns(tmp_path) is True


def test_clean_run_is_not_flagged_as_truncated(tmp_path):
    (tmp_path / "agent_stdout.log").write_text(
        '{"type":"result","subtype":"success","num_turns":22}\n'
    )
    assert _scan_agent_max_turns(tmp_path) is False


# ---------------------------------------------------------------------------
# the wall clock, which the turn cap's removal promotes to sole bound
# ---------------------------------------------------------------------------


def test_wall_clock_clears_the_observed_agent_duration_by_a_wide_margin():
    """Exit 124 routes to infra_timeout -> INVALID -> re-run, so a wall clock
    that binds normal work would re-run the slowest cells until they finish and
    censor the corpus upward. The longest of 303 capped pilot cells ran 1335s;
    uncapped runs are longer still, so the bound has to sit well clear of it."""
    assert DEFAULT_AGENT_TIMEOUT_S >= 3600


def test_config_and_cli_agree_on_the_wall_clock():
    """Two defaults that drift apart make the effective bound depend on which
    entry point launched the run."""
    assert TaskRunConfig(task_toml=Path("x.toml")).timeout == DEFAULT_AGENT_TIMEOUT_S
    assert build_parser().get_default("timeout") == DEFAULT_AGENT_TIMEOUT_S


def test_build_timeout_is_unchanged_by_the_agent_wall_clock():
    """Image build is a different resource with its own bound; raising the
    agent's clock must not silently raise the builder's."""
    assert TaskRunConfig(task_toml=Path("x.toml")).build_timeout == 1800
