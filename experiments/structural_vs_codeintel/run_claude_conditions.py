"""Run the five conditions through the Claude Code CLI instead of OpenRouter.

A second generation seam, not a replacement. It exists because the OpenRouter
key is dead and five Claude Code OAuth accounts are live, and it writes reports
in the schema ``compare_conditions.py`` already consumes, so the analysis does
not care which seam produced a run. What the analysis MUST care about is that
the two seams are not interchangeable, so every report records
``generation_seam`` and the caveats below, and mixing seams inside one contrast
is refused rather than silently averaged.

Three controls the OpenRouter seam holds, this one cannot:

* **Sampling is not settable.** The CLI exposes no temperature or top_p, so the
  0.7/0.95 that ``toast_generate.py`` pins -- and that makes these arms
  comparable to the existing toast_retrieval numbers -- is whatever the CLI
  chooses. Repeats here measure the CLI's stochasticity, not a pinned sampler.
* **The loop is Claude Code's.** Its turn accounting, its compaction, its
  retry policy. ``--max-turns`` bounds it, but a CLI turn is not the harness's
  round, and this file does not pretend otherwise: ``rounds_executed`` is
  recorded from ``num_turns`` and labelled ``cli_turns``.
* **Submission cannot be forced.** The OpenRouter loop compels ``submit_files``
  on its last round, so an agent there cannot fail by simply never answering.
  Here it can. A run with no submission is therefore a harness asymmetry, not a
  measured zero, and is routed out of scoring rather than recorded as 0.0 --
  the same rule ``scorer_guard`` states for the containerized arms.

Measured, not assumed: the CLI injects roughly 28k tokens of its own scaffolding
per run even with the system prompt replaced, dynamic sections excluded, strict
MCP config and every built-in denied. That constant lands on the efficiency
claim, so token fields carry ``scaffold_overhead_tokens`` alongside them and
must not be compared across seams.

Usage:
    python3 run_claude_conditions.py --dry-run
    python3 run_claude_conditions.py --outdir reports_cc --accounts 1,2,3,4,5
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "toast_retrieval"))

from aligned_scoring import SCORING_CONTRACT, score as score_retrieval  # noqa: E402
from conditions import (  # noqa: E402
    ABLATION_CONTROLS,
    ABLATION_TOOLS,
    CONDITIONS,
    PILOT_CONDITIONS,
    DECLARED_ASYMMETRIES,
    check_ablation_declarations,
    check_description_parity,
)
from eb_metrics_shim import import_eb_metrics  # noqa: E402
from run_conditions import (  # noqa: E402
    COVERAGE,
    _code_intel_ineligible,
    _needs_code_intel,
    _units,
    report_path,
)

required_files_from_ground_truth = import_eb_metrics(
    "eb_metrics.retrieval_extraction"
).required_files_from_ground_truth

REPO_ROOT = HERE.parent.parent
SEAM = "claude_code_cli"
GENERATION_SEAM_NOTE = (
    "Claude Code CLI over OAuth. Sampling is not settable, the agent loop is "
    "the CLI's, and roughly 28k tokens of CLI scaffolding are charged to every "
    "run. Do not compare token or cost fields against the openrouter seam."
)
_HOMES = Path.home() / ".claude-homes"
_DEFAULT_MODEL = "claude-sonnet-5"
#: Bounds a runaway loop. Not the OpenRouter seam's 4 rounds in disguise: a CLI
#: turn is a different unit, so this is a stop, not a matched control.
#:
#: Set high deliberately. A cap that binds does not bind evenly: definition and
#: references need a starting location before they can navigate (a declared
#: asymmetry), so the code-intel arm spends more turns than the others and a
#: tight cap invalidates it disproportionately. Differential invalidation is a
#: biased filter on the sample, which is worse than an expensive run. The first
#: smoke run hit a cap of 12 on code-intel while the lexical arm finished in 10.
_MAX_TURNS = 40


def system_prompt(store_identifiers) -> str:
    """The OpenRouter seam's prompt, plus the one fact that seam did not need.

    Under OpenRouter the tool surface IS the request, so the model cannot reach
    for anything else. Here the built-ins exist and are denied at the hook, and
    an agent that does not know that spends turns rediscovering it -- which
    would show up as a behavioural difference between seams that is really a
    difference in what the agent was told.
    """
    repos = ", ".join(store_identifiers)
    return (
        "You are a code retrieval policy. Given a task, find the source files "
        "required to carry it out. Search only these repositories: "
        f"{repos}. When you are confident, call submit_files with every "
        "required file, most important first, using repo-relative paths "
        "exactly as the tools returned them. The mcp__retrieval__* tools are "
        "the only tools available to you; every other tool is denied."
    )


def _write_run_config(rundir: Path, condition_name: str, stores) -> tuple[Path, Path]:
    python = sys.executable
    mcp_config = rundir / "mcp.json"
    mcp_config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "retrieval": {
                        "command": python,
                        "args": [str(HERE / "claude_seam" / "mcp_server.py")],
                        "env": {
                            "EB_CONDITION": condition_name,
                            "EB_STORES": ",".join(stores),
                            "EB_RUNDIR": str(rundir),
                        },
                    }
                }
            }
        )
    )
    settings = rundir / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "*",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"{python} {HERE / 'claude_seam' / 'allowlist_hook.py'}",
                                }
                            ],
                        }
                    ]
                }
            }
        )
    )
    return mcp_config, settings


def cli_argv(rundir: Path, model: str, prompt: str, mcp_config: Path, settings: Path):
    return [
        "claude",
        "--print",
        "--model",
        model,
        "--mcp-config",
        str(mcp_config),
        # Without this the CLI may add servers from user or project config, and
        # the tool surface would stop being the condition.
        "--strict-mcp-config",
        "--settings",
        str(settings),
        "--system-prompt",
        prompt,
        "--exclude-dynamic-system-prompt-sections",
        "--max-turns",
        str(_MAX_TURNS),
        "--output-format",
        "json",
    ]


def _usage(result: dict) -> dict:
    usage = result.get("usage") or {}
    fresh = usage.get("input_tokens", 0) or 0
    cached_write = usage.get("cache_creation_input_tokens", 0) or 0
    cached_read = usage.get("cache_read_input_tokens", 0) or 0
    return {
        # Everything the model was charged for reading, however it was cached.
        # Reporting only `input_tokens` would show ~6 tokens for a run that
        # actually consumed a hundred thousand.
        "input_tokens": fresh + cached_write + cached_read,
        "output_tokens": usage.get("output_tokens", 0) or 0,
        "input_tokens_uncached": fresh,
        "cache_creation_input_tokens": cached_write,
        "cache_read_input_tokens": cached_read,
    }


def run_one(candidate: dict, condition_name: str, model: str, account: int) -> dict:
    """One (task, condition) run. Raises RuntimeError if the CLI never answered."""
    task_id = candidate["task_id"]
    task_dir = REPO_ROOT / candidate["path"]
    required_files = required_files_from_ground_truth(task_dir / "ground_truth.json")
    if not required_files:
        raise RuntimeError(f"{task_id}: ground_truth.json yielded no required_files")
    instruction = (task_dir / "instruction.md").read_text()
    stores = candidate["store_identifiers"]

    rundir = Path(tempfile.mkdtemp(prefix=f"eb-{condition_name}-"))
    try:
        mcp_config, settings = _write_run_config(rundir, condition_name, stores)
        env = dict(os.environ)
        env.update(
            CLAUDE_CONFIG_DIR=str(_HOMES / f"account{account}" / ".claude"),
            EB_CONDITION=condition_name,
            EB_RUNDIR=str(rundir),
            # Deferred tool schemas would make the tool surface something the
            # model has to discover, and tool selection is the dependent
            # variable. run_task.py sets this for the same reason.
            ENABLE_TOOL_SEARCH="false",
        )
        started = time.time()
        completed = subprocess.run(
            cli_argv(rundir, model, system_prompt(stores), mcp_config, settings),
            input=instruction,
            capture_output=True,
            text=True,
            env=env,
            timeout=900,
        )
        wall_seconds = round(time.time() - started, 1)
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"{task_id}/{condition_name}: CLI produced no JSON result "
                f"({exc}); stderr={completed.stderr[:300]}"
            ) from exc

        submission_path = rundir / "submission.json"
        retrieved = (
            json.loads(submission_path.read_text())["retrieved_paths"]
            if submission_path.is_file()
            else []
        )
        log_path = rundir / "tool_log.json"
        logged = json.loads(log_path.read_text()) if log_path.is_file() else {}
        denied_path = rundir / "denied_tools.jsonl"
        denied = (
            [json.loads(line) for line in denied_path.read_text().splitlines() if line]
            if denied_path.is_file()
            else []
        )
    finally:
        shutil.rmtree(rundir, ignore_errors=True)

    condition = CONDITIONS[condition_name]
    report = {
        "task_id": task_id,
        "policy": f"condition:{condition_name}",
        "model": model,
        "suite": candidate.get("suite"),
        "repo_count": candidate.get("repo_count", len(stores)),
        "store_identifiers": stores,
        "retrieved_paths": retrieved,
        "required_files": required_files,
        "scoring": SCORING_CONTRACT,
        "condition": condition_name,
        "condition_tools": [t["function"]["name"] for t in condition.tools],
        "condition_knows": condition.knows,
        # None for every arm but the ablation ones. Recorded on all of them so
        # a reader of one report does not have to know which arms deviate.
        "description_ablation": condition.description_ablation,
        "generation_seam": SEAM,
        "generation_seam_note": GENERATION_SEAM_NOTE,
        "sampling": "cli_default_not_settable",
        "rounds_executed": result.get("num_turns"),
        "rounds_unit": "cli_turns",
        "max_turns": _MAX_TURNS,
        "token_counting": "cli_reported",
        "scaffold_overhead_tokens": "~28600 fixed CLI scaffolding per run",
        "cost_usd": result.get("total_cost_usd"),
        "elapsed_seconds": wall_seconds,
        "wall_seconds": wall_seconds,
        "tool_log": logged.get("tool_log", []),
        "tool_seconds": logged.get("tool_seconds", 0.0),
        "retrieval_result_chars": logged.get("retrieval_result_chars", 0),
        "denied_tools": denied,
        "cli_result_text": result.get("result", ""),
        "cli_is_error": bool(result.get("is_error")),
        "cli_subtype": result.get("subtype"),
        "cli_stop_reason": result.get("stop_reason"),
        **_usage(result),
    }

    if report["cli_is_error"] and "max_turns" in str(report["cli_subtype"]):
        # Hitting the turn cap is not a retrieval failure, and it is the one
        # failure mode expected to fall unevenly across conditions. It gets its
        # own status so that unevenness is visible in the excluded-run counts
        # rather than hidden inside a generic error bucket.
        report["status"] = "invalid_turn_limit"
        report["ir_scores"] = None
        return report

    if report["cli_is_error"]:
        # The CLI itself failed (expired token, disabled account, turn limit
        # reached mid-search). No agent behaviour was measured, so this routes
        # to the re-run channel exactly as scorer_guard requires -- folding it
        # into a zero would report a broken account as a bad retrieval arm.
        report["status"] = "invalid_cli_error"
        report["ir_scores"] = None
        return report

    if not retrieved:
        # The OpenRouter seam forces a final submit_files, so no run there can
        # end without an answer. This seam cannot force it, which makes an empty
        # submission a property of the harness rather than of the agent. Scoring
        # it 0.0 would charge the arm for the seam's limitation.
        report["status"] = "invalid_no_submission"
        report["ir_scores"] = None
        return report

    report["status"] = "scored"
    report["ir_scores"] = score_retrieval(
        retrieved, required_files, task_id, f"condition:{condition_name}"
    ).to_dict()
    return report


def probe_accounts(accounts, model: str) -> dict[int, str]:
    """Accounts that cannot run the CLI, mapped to why.

    Runs are spread round-robin, so a single disabled account silently poisons
    a fixed fraction of every condition. Those runs route to the re-run channel
    rather than scoring, but a sweep that quietly loses a fifth of its cells is
    still a sweep worth stopping before it starts.
    """
    def probe(account: int) -> tuple[int, str | None]:
        env = dict(os.environ)
        env["CLAUDE_CONFIG_DIR"] = str(_HOMES / f"account{account}" / ".claude")
        try:
            completed = subprocess.run(
                ["claude", "--print", "--model", model, "--output-format", "json",
                 "--system-prompt", "Reply with one word.",
                 "--exclude-dynamic-system-prompt-sections", "--strict-mcp-config"],
                input="Say READY.",
                capture_output=True,
                text=True,
                env=env,
                timeout=180,
            )
            payload = json.loads(completed.stdout)
        except Exception as exc:
            return account, f"{type(exc).__name__}: {exc}"
        if payload.get("is_error"):
            return account, str(payload.get("result", ""))[:160]
        return account, None

    with ThreadPoolExecutor(max_workers=len(accounts)) as pool:
        results = list(pool.map(probe, accounts))
    return {account: why for account, why in results if why}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=HERE / "pilot_tasks.json")
    parser.add_argument("--outdir", type=Path, default=HERE / "reports_claude_code")
    parser.add_argument("--model", default=_DEFAULT_MODEL)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260823)
    parser.add_argument(
        "--accounts",
        default="1,2,3,4,5",
        help="OAuth accounts to spread runs across, round-robin",
    )
    parser.add_argument("--conditions", default=",".join(PILOT_CONDITIONS))
    parser.add_argument("--task", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    problems = check_description_parity() + check_ablation_declarations()
    if problems:
        print("tool description gate FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    if shutil.which("claude") is None:
        print("the `claude` CLI is not on PATH", file=sys.stderr)
        return 2

    condition_names = [n.strip() for n in args.conditions.split(",") if n.strip()]
    unknown = [n for n in condition_names if n not in CONDITIONS]
    if unknown:
        print(f"unknown conditions: {unknown}", file=sys.stderr)
        return 2

    # An ablation arm swaps a schema; it does not get to skip the gate. The
    # exampled description must still say what the tool matches and nothing
    # about when to prefer it, or the ablation would measure coaching.
    ablating = [n for n in condition_names if CONDITIONS[n].description_ablation]
    if ablating:
        problems = check_description_parity(ABLATION_TOOLS)
        if problems:
            print("ablation description parity FAILED:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return 2
        for name in ablating:
            print(
                f"  ablation arm {name} vs control "
                f"{ABLATION_CONTROLS[name]}: {CONDITIONS[name].description_ablation}",
                file=sys.stderr,
            )

    accounts = [int(a) for a in args.accounts.split(",") if a.strip()]
    missing = [
        a for a in accounts if not (_HOMES / f"account{a}" / ".claude" / ".credentials.json").is_file()
    ]
    if missing:
        print(f"no OAuth credentials for account(s) {missing}", file=sys.stderr)
        return 2

    dead = probe_accounts(accounts, args.model)
    if dead:
        print(
            "account(s) cannot run the CLI: "
            + "; ".join(f"{a}: {why}" for a, why in dead.items()),
            file=sys.stderr,
        )
        return 2

    candidates = json.loads(args.tasks.read_text())
    if args.task:
        candidates = [c for c in candidates if c["task_id"] == args.task]
        if not candidates:
            print(f"{args.task!r} not in {args.tasks}", file=sys.stderr)
            return 2

    ineligible = _code_intel_ineligible(candidates)
    if ineligible is None:
        print(
            f"{COVERAGE.name} missing or incomplete; run preflight_index.py "
            f"--tasks {args.tasks} first",
            file=sys.stderr,
        )
        return 2

    units = [
        unit
        for unit in _units(candidates, condition_names, args.repeats, args.seed)
        if not (_needs_code_intel(unit[0]) and unit[2]["task_id"] in ineligible)
    ]
    pending = [
        unit
        for unit in units
        if not report_path(args.outdir, unit[0], unit[1], unit[2]["task_id"]).is_file()
    ]
    print(
        f"{len(candidates)} tasks x {len(condition_names)} conditions x "
        f"{args.repeats} repeats = {len(units)} runs "
        f"({len(units) - len(pending)} cached, {len(pending)} to run) "
        f"seam={SEAM} model={args.model} accounts={accounts}",
        file=sys.stderr,
    )
    print(f"  {GENERATION_SEAM_NOTE}", file=sys.stderr)
    for asymmetry in DECLARED_ASYMMETRIES:
        print(f"  declared asymmetry: {asymmetry}", file=sys.stderr)
    if args.dry_run:
        return 0

    args.outdir.mkdir(parents=True, exist_ok=True)
    # One worker per account: the accounts are the rate-limit boundary, so
    # running more concurrently just moves the throttle inside an account.
    order = random.Random(args.seed).sample(pending, len(pending))

    def work(indexed) -> None:
        position, unit = indexed
        condition_name, repeat, candidate = unit
        account = accounts[position % len(accounts)]
        label = f"{condition_name}/r{repeat}/{candidate['task_id']}"
        try:
            report = run_one(candidate, condition_name, args.model, account)
        except Exception as exc:  # one run must not void the sweep
            print(f"{label}: FAILED -- {type(exc).__name__}: {exc}", file=sys.stderr)
            return
        report.update(repeat=repeat, seed=args.seed, account=account)
        destination = report_path(args.outdir, condition_name, repeat, candidate["task_id"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2) + "\n")
        if report["status"] != "scored":
            print(f"{label}: {report['status']}", file=sys.stderr)
            return
        scores = report["ir_scores"]
        print(
            f"{label}: recall={scores['file_recall']:.3f} "
            f"turns={report['rounds_executed']} cost=${report['cost_usd']:.3f} "
            f"denied={len(report['denied_tools'])}",
            file=sys.stderr,
        )

    with ThreadPoolExecutor(max_workers=len(accounts)) as pool:
        list(pool.map(work, enumerate(order)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
