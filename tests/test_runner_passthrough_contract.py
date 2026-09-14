"""Contract test for run_benchmark → sub-runner flag forwarding.

run_benchmark forwards a fixed set of flags (``collect_passthrough_args``) to
whichever runner handles a task's session_type. If a runner does not declare a
forwarded flag it dies at argparse (exit 2) before writing a result, silently
disabling that whole session_type (EnterpriseBench-nw70h: ``--output-dir`` on
chain/event_replay and ``--mode`` on event_replay).

These tests pin the contract:
  1. the producer emits exactly ``runner_cli.PASSTHROUGH_FLAGS``; and
  2. every runner accepts every flag the producer can emit.
The producer's own output is used as the argv under test, so the check tracks
the real forwarded set rather than a hand-maintained duplicate.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_benchmark import collect_passthrough_args
from orchestration.runner_cli import (
    PASSTHROUGH_FLAGS,
    add_passthrough_args,
    assert_accepts_passthrough,
)
from orchestration.run_task import build_parser as build_run_task_parser
from orchestration.chain_runner import build_parser as build_chain_parser
from orchestration.event_replay import build_parser as build_event_parser


def _maximal_namespace() -> argparse.Namespace:
    """A namespace that drives collect_passthrough_args to emit every flag."""
    return argparse.Namespace(
        source="mirror",
        agent="claude",
        harness="claude",
        model="claude-sonnet-4-6",
        timeout=60,
        account="3",
        judge_model="cc:haiku",
        judge_account=1,
        mode="hybrid",
        dry_run=True,
        variant_label="p3",
    )


def _forwarded_argv() -> list[str]:
    """The exact flag argv run_benchmark forwards to a sub-runner."""
    return collect_passthrough_args(_maximal_namespace(), output_dir=Path("/tmp/out"))


def test_producer_emits_exactly_the_contract() -> None:
    """collect_passthrough_args must emit exactly PASSTHROUGH_FLAGS — no drift."""
    emitted = {tok for tok in _forwarded_argv() if tok.startswith("--")}
    assert emitted == set(PASSTHROUGH_FLAGS)
    assert "--judge-model" in emitted
    assert "--judge-account" in emitted


# (build_parser, positional args) for every runner run_benchmark dispatches to.
_RUNNERS = {
    "run_task": (build_run_task_parser, ["dummy/task.toml"]),
    "chain_runner": (build_chain_parser, ["dummy/task.toml"]),
    "event_replay": (build_event_parser, ["dummy/task_dir"]),
}


@pytest.mark.parametrize("runner", sorted(_RUNNERS))
def test_every_forwarded_flag_parses_on_runner(runner: str) -> None:
    """Every runner must accept every flag the producer can forward (exit != 2)."""
    build_parser, positional = _RUNNERS[runner]
    argv = positional + _forwarded_argv()
    # parse_args raises SystemExit on an unrecognized flag; a clean parse is the
    # whole guarantee — the runner reached its own logic instead of exit 2.
    build_parser().parse_args(argv)


def test_add_passthrough_args_rejects_unknown_consumed() -> None:
    """A typo in a runner's consumed=() list must fail loudly, not silently skip."""
    with pytest.raises(ValueError):
        add_passthrough_args(argparse.ArgumentParser(), consumed=("--not-a-flag",))


def test_assert_accepts_passthrough_catches_a_dropped_flag() -> None:
    """run_task's local contract guard must fail if a forwarded flag is missing."""
    parser = argparse.ArgumentParser()
    add_passthrough_args(parser, consumed=("--output-dir",))  # declare all but one
    with pytest.raises(RuntimeError):
        assert_accepts_passthrough(parser)
