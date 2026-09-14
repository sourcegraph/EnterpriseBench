"""Single source of truth for the flags ``run_benchmark`` forwards to sub-runners.

``run_benchmark.collect_passthrough_args`` emits a fixed set of flags to whichever
runner handles a task's ``session_type`` (``run_task``, ``chain_runner``,
``event_replay``). Every one of those flags must PARSE on the target runner, or
the runner dies at argparse (exit 2) before it can write a result — which
silently disables a whole session_type. Two argparse gaps have each done exactly
that (EnterpriseBench-nw70h: ``--output-dir`` on chain/event_replay and ``--mode``
on event_replay; EnterpriseBench-bl4bc: ``--agent``).

The contract lived as hand-copied ``add_argument`` blocks in each runner, so it
drifted. Declaring it once here — and importing it into every sub-runner —
makes it impossible for the producer to forward a flag a runner rejects.
``PASSTHROUGH_FLAGS`` is the *shape* authority (which flags exist);
``run_benchmark.collect_passthrough_args`` is the *value* authority (how each is
emitted). The contract test (``tests/test_runner_passthrough_contract.py``)
asserts the two stay in sync and that every runner accepts the emitted set.

A runner binds to the contract one of two ways:
  * ``add_passthrough_args(parser)`` — for runners that only forward the flags
    (``chain_runner``, ``event_replay``): every flag is declared accept-and-ignore.
  * ``assert_accepts_passthrough(parser)`` — for a runner that declares the flags
    itself with richer semantics (``run_task``): fail at construction if one is
    missing, rather than as a silent exit-2 at dispatch.
"""

from __future__ import annotations

import argparse
from typing import Iterable

# Every flag run_benchmark may forward to a sub-runner. Keep in sync with
# run_benchmark.collect_passthrough_args — the contract test fails if they drift.
PASSTHROUGH_FLAGS: tuple[str, ...] = (
    "--source",
    "--agent",
    "--harness",
    "--model",
    "--timeout",
    "--account",
    "--judge-model",
    "--judge-account",
    "--mode",
    "--dry-run",
    "--output-dir",
    "--variant-label",
)

# argparse kwargs for each forwarding flag, used when a runner only needs to
# ACCEPT-and-ignore the flag. A runner that genuinely consumes a flag declares
# its own (richer) version and names it in ``consumed`` so it is skipped here.
_FLAG_KWARGS: dict[str, dict[str, object]] = {
    "--source": {"type": str, "default": None},
    "--agent": {"type": str, "default": None},
    "--harness": {"type": str, "default": None},
    "--model": {"type": str, "default": None},
    "--timeout": {"type": int, "default": None},
    "--account": {"type": int, "default": None},
    "--judge-model": {"type": str, "default": None},
    "--judge-account": {"type": int, "default": None},
    "--mode": {"type": str, "default": None},
    "--dry-run": {"action": "store_true"},
    "--output-dir": {"type": str, "default": None},
    "--variant-label": {"type": str, "default": None},
}


def add_passthrough_args(
    parser: argparse.ArgumentParser,
    *,
    consumed: Iterable[str] = (),
) -> None:
    """Declare the run_benchmark passthrough flags on *parser* (in place).

    Flags named in *consumed* are declared by the runner itself (with real
    semantics) and are skipped here to avoid an argparse option conflict; they
    remain part of the contract. Every other flag is accepted and ignored so the
    runner never rejects a forwarded flag.
    """
    skip = set(consumed)
    unknown = skip - set(PASSTHROUGH_FLAGS)
    if unknown:
        raise ValueError(f"consumed names non-passthrough flags: {sorted(unknown)}")
    for flag in PASSTHROUGH_FLAGS:
        if flag in skip:
            continue
        parser.add_argument(flag, help=argparse.SUPPRESS, **_FLAG_KWARGS[flag])


def assert_accepts_passthrough(parser: argparse.ArgumentParser) -> None:
    """Fail loudly if *parser* does not declare every forwarded flag.

    For a runner that declares the passthrough flags itself (run_task) instead of
    calling ``add_passthrough_args`` — this binds it to the contract locally, so a
    dropped or renamed flag fails at parser construction rather than becoming a
    silent argparse exit-2 at dispatch (EnterpriseBench-nw70h).
    """
    declared = set(parser._option_string_actions)
    missing = [flag for flag in PASSTHROUGH_FLAGS if flag not in declared]
    if missing:
        raise RuntimeError(
            f"parser is missing run_benchmark passthrough flags: {missing}"
        )
