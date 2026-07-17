"""Event-replay runner for EnterpriseBench.

Feeds events to an agent and collects its actions, then scores them.

Agent interaction model (file-based):
  1. Runner writes events.jsonl into the sandbox at a known path.
  2. Agent reads events.jsonl at its own pace.
  3. Agent writes actions.jsonl with its responses.
  4. Runner reads actions.jsonl and scores against oracle.

This is simpler than streaming but still allows timeliness scoring because
the agent timestamps its own actions.  A future streaming mode could feed
events incrementally, but file-based is the pragmatic v1.

Usage:
    python event_replay.py <task.toml> [--agent-actions <path>]

    The positional is a task.toml FILE (with session_type = "event_replay"),
    the same contract run_task and chain_runner declare. Its directory must
    also contain:
      - events.jsonl
      - oracle_actions.jsonl

    If --agent-actions is provided, score that file directly (useful for
    testing without running an actual agent).  Otherwise, the runner sets
    up the sandbox and waits for the agent to produce actions.jsonl.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Support both package import and standalone execution
try:
    from .event_schema import (
        load_events, load_actions, validate_event_file, validate_action_file,
    )
    from .action_scorer import score, ScoringConfig, ScoreResult
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from event_schema import (
        load_events, load_actions, validate_event_file, validate_action_file,
    )
    from action_scorer import score, ScoringConfig, ScoreResult

try:
    from .runner_cli import add_passthrough_args
    from .results_io import PHASE_COMPLETE, RUN_STATUS_INVALID, write_results_json
except ImportError:
    from runner_cli import add_passthrough_args
    from results_io import PHASE_COMPLETE, RUN_STATUS_INVALID, write_results_json

# A verifier could not reach a verdict, so there is no score to record. Same name
# run_task reports the same cause under (run_task.py:1549) — a reader classifies
# every runner's infra failure by one vocabulary.
VERIFIER_INFRA_ERROR = "verifier_infra_error"


def load_task_config(task_dir: Path) -> dict:
    """Load and validate the task.toml file."""
    task_file = task_dir / "task.toml"
    if not task_file.exists():
        raise FileNotFoundError(f"No task.toml in {task_dir}")

    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore
        except ImportError:
            raise ImportError("Need Python 3.11+ (tomllib) or tomli package")

    with open(task_file, "rb") as f:
        config = tomllib.load(f)

    task = config.get("task", {})
    if task.get("session_type") != "event_replay":
        raise ValueError(
            f"Task {task.get('id', '???')} has session_type={task.get('session_type')}, "
            f"expected 'event_replay'"
        )

    return config


def resolve_paths(task_dir: Path, config: dict) -> tuple[Path, Path]:
    """Resolve event and oracle action file paths from task config."""
    events_cfg = config.get("events", {})

    event_file = task_dir / events_cfg.get("event_file", "events.jsonl")
    oracle_file = task_dir / events_cfg.get("oracle_actions", "oracle_actions.jsonl")

    if not event_file.exists():
        raise FileNotFoundError(f"Events file not found: {event_file}")
    if not oracle_file.exists():
        raise FileNotFoundError(f"Oracle actions file not found: {oracle_file}")

    return event_file, oracle_file


def run_scoring(
    events_path: Path,
    oracle_path: Path,
    agent_path: Path,
    config: ScoringConfig | None = None,
) -> ScoreResult:
    """Load files and run the scorer.

    The agent's action file is validated as a WARNING and scored regardless: a
    malformed action is the agent's own failure, and routing it anywhere but the
    scorer would let a broken agent escape the 0 it earned.

    The task's own event fixture is the caller's precondition and is not checked
    here — the asymmetry is the point, and it is why the two validators no longer
    sit side by side.
    """
    action_errors = validate_action_file(agent_path)
    if action_errors:
        print(f"WARNING: Agent action file validation issues:", file=sys.stderr)
        for e in action_errors:
            print(f"  - {e}", file=sys.stderr)

    events = load_events(events_path)
    oracle_actions = load_actions(oracle_path)
    agent_actions = load_actions(agent_path)

    return score(events, oracle_actions, agent_actions, config)


def print_report(result: ScoreResult) -> None:
    """Print a human-readable scoring report."""
    print("=" * 60)
    print("  EVENT-REPLAY SCORING REPORT")
    print("=" * 60)
    print()
    print(f"  Correctness:  {result.correctness:.1%}  (weight: {result.config['weight_correctness']:.0%})")
    print(f"  Completeness: {result.completeness:.1%}  (weight: {result.config['weight_completeness']:.0%})")
    print(f"  Timeliness:   {result.timeliness:.1%}  (weight: {result.config['weight_timeliness']:.0%})")
    print(f"  Ordering:     {result.ordering:.1%}  (weight: {result.config['weight_ordering']:.0%})")
    print(f"  ──────────────────────────")
    print(f"  TOTAL SCORE:  {result.total:.1%}")
    print()

    if result.extra_actions > 0:
        print(f"  Extra (unmatched) agent actions: {result.extra_actions}")
        print()

    print("  Per-oracle-action breakdown:")
    for i, m in enumerate(result.matches, 1):
        oracle = m["oracle"]
        status = "MATCHED" if m["agent"] else "MISSED"
        print(f"    {i}. [{status}] {oracle['action_type']:12s} -> {oracle['target']}")
        if m["agent"]:
            print(f"       Timeliness: {m['timeliness']:.1%}")
    print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Event-replay runner and scorer")
    parser.add_argument(
        "task_toml",
        type=Path,
        help="Path to the task.toml file",
    )
    parser.add_argument(
        "--agent-actions", type=Path, default=None,
        help="Path to agent's actions.jsonl (for offline scoring)"
    )
    parser.add_argument("--json", action="store_true", help="Output raw JSON instead of report")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    # run_benchmark forwards a fixed flag set to whichever runner handles a task.
    # None are used here, but all must parse or the run dies at argparse before a
    # result is written — event_replay silently rejected --mode and --output-dir
    # this way (EnterpriseBench-nw70h). Contract lives in runner_cli.
    add_passthrough_args(parser)
    return parser


def main() -> int:
    args = build_parser().parse_args()

    # Positional is a task.toml FILE (as in run_task and chain_runner), so the
    # task directory is its parent. Consuming it as a directory instead resolved
    # .../task.toml/task.toml and killed every run_benchmark dispatch before it
    # could score (EnterpriseBench-78zt5).
    task_dir = args.task_toml.parent.resolve()

    # Load task config
    config = load_task_config(task_dir)
    events_path, oracle_path = resolve_paths(task_dir, config)

    if args.agent_actions is None:
        # No sandbox integration exists yet (EnterpriseBench-ia157): nothing
        # clones the declared repos or runs an agent, so there is no
        # actions.jsonl to score. Exiting nonzero is the point — run_benchmark
        # forwards no --agent-actions, so every dispatched task lands here, and
        # returning 0 had it record status="completed" with score None: a stub
        # reported as a real run that scored nothing.
        print("Event-replay runner (sandbox integration not yet implemented)")
        print(f"  Task: {config['task']['id']}")
        print(f"  Events: {events_path} ({len(load_events(events_path))} events)")
        print(f"  Oracle: {oracle_path} ({len(load_actions(oracle_path))} actions)")
        print()
        print("To score an agent's output, re-run with --agent-actions <path>")
        return 1

    # Score mode
    agent_path = args.agent_actions.resolve()
    if not agent_path.exists():
        print(f"Agent actions file not found: {agent_path}", file=sys.stderr)
        return 1

    # Durable result, in the shape every runner writes (results_io). The score
    # was printed to stdout and nowhere else before EnterpriseBench-78zt5, so a
    # caller reading results.json saw None no matter how the agent did.
    task_meta = config.get("task", {})
    run_facts = dict(
        task_id=task_meta.get("id", ""),
        # Offline scoring of an actions file the agent already wrote: this
        # process spends no tokens, so it reports none.
        tool_usage={},
        config={"mode": args.mode, "agent_actions": str(agent_path)},
        task_metadata={
            "suite": task_meta.get("suite", ""),
            "task_type": task_meta.get("task_type", ""),
            "difficulty": task_meta.get("difficulty", ""),
            "languages": config.get("metadata", {}).get("languages", []),
        },
    )

    # The events file is the task's OWN fixture, not agent output: if it is
    # malformed the verifier's inputs are broken, so no verdict it reaches about
    # the agent is trustworthy and the run belongs in the invalid/re-run channel
    # rather than the results table with whatever the scorer computed from the
    # wreckage. This is why it is checked here and not beside the agent-action
    # validator inside run_scoring, which warns and scores on purpose.
    fixture_errors = validate_event_file(events_path)
    if fixture_errors:
        print(
            "INVALID RUN — this task's own event fixture is malformed, so no "
            "verdict reached about the agent would be trustworthy:",
            file=sys.stderr,
        )
        for e in fixture_errors:
            print(f"  - {e}", file=sys.stderr)
        write_results_json(
            task_dir,
            success=False,
            phase="",
            status=RUN_STATUS_INVALID,
            error="; ".join(fixture_errors),
            failure_class=VERIFIER_INFRA_ERROR,
            **run_facts,
        )
        # Nonzero routes the run to run_benchmark's error channel, which it
        # reads BEFORE scores.task_score (run_benchmark.py:333 vs :353) — the
        # exit code, not the payload, is what keeps this out of the results table.
        return 1

    result = run_scoring(events_path, oracle_path, agent_path)

    write_results_json(
        task_dir,
        success=True,
        # The headline aggregators filter on phase == complete AND success.
        phase=PHASE_COMPLETE,
        scores={
            "task_score": result.total,
            "correctness": result.correctness,
            "completeness": result.completeness,
            "timeliness": result.timeliness,
            "ordering": result.ordering,
            "extra_actions": result.extra_actions,
        },
        **run_facts,
    )

    if args.json:
        indent = 2 if args.pretty else None
        print(json.dumps(result.to_dict(), indent=indent))
    else:
        print_report(result)

    return 0


if __name__ == "__main__":
    # main()'s return value was discarded, so even an explicit failure exited 0.
    sys.exit(main())
