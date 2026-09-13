#!/usr/bin/env python3
"""Blinded PR-review-like judging pilot (EnterpriseBench-rryas.10).

Runs the existing Tier-2 LLM judge (``lib/eb_verify/judge/``) as a SECONDARY,
blinded reviewer over already-scored run artifacts, and measures how often it
agrees with the deterministic reward. It never blends into the deterministic
score; it emits a parallel ledger and an agreement report.

It has not yet been pointed at a headline run. The committed demonstration under
results/analysis/rryas10_pilot ran against results/sample_runs, which is a set of
hand-authored calibration fixtures rather than agent output, so its figures
measure the wiring and nothing else. See the doc's "Feasibility" section.

Design (see docs/internal/rryas10_blinded_judge_pilot.md for the full protocol):

  * Blinding. The judge sees only the arm-neutral final answer (answer.json)
    and the curated expected_solution per checkpoint. Arm identity (baseline /
    mcp_only / hybrid / cli) is withheld from the judge entirely -- it is
    carried alongside each unit for post-hoc analysis only, never into the
    prompt. That withholding, backstopped by _assert_arm_neutral, IS the
    blinding mechanism; the seeded shuffle below is not. Each checkpoint is a
    fresh stateless call at temperature 0, so processing order carries no
    signal between calls today -- the shuffle is cheap insurance against a
    future batched or conversational judge, and is seeded so a run replays
    exactly. It is not load-bearing for blinding, and the ledger records the
    arm per row regardless.
  * Reuse, not reinvention. Rubric = the repo's CHECKPOINT_EVAL_PROMPT; engine
    = LLMJudge. The only net-new pieces are (a) reading arm-neutral artifacts,
    (b) randomization, (c) persisting the reasoning/evidence/confidence that the
    production runner discards (eb_verify/runner.py returns only result.score).
  * No primary-reward contamination. This script reads run_log.json's
    deterministic total_score read-only and writes a separate ledger. It does
    not touch any scoring path.

Usage:
  PYTHONPATH=lib python3 scripts/analysis/rryas10_blinded_judge_pilot.py \
      --runs-root results/sample_runs --model cc:haiku --seed 20260719 \
      --out results/analysis/rryas10_pilot

The deterministic reward is authoritative for the benchmark. This pilot only
asks a separate question: does an independent blinded reviewer corroborate it?
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import random
import re
import statistics
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

# eb_verify is importable via PYTHONPATH=lib (see conftest / CLAUDE.md).
from eb_verify.judge import CheckpointJudgeInput, LLMJudge

# Default repo root = two levels up from scripts/analysis/. Overridable via
# --repo-root so the pilot can read artifacts/fixtures from a sibling checkout
# (e.g. run the worktree's script against the base tree's not-yet-committed
# expected_solution.json fixtures) without copying anything.
REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Unit:
    """One (task, arm) artifact to be judged, plus its held-out arm label."""

    task_id: str
    arm: str  # held out from the judge; used only for post-hoc analysis
    answer_path: str
    task_dir: str  # benchmark dir holding expected_solution.json
    deterministic_total: Optional[float]
    deterministic_checkpoints: dict = field(default_factory=dict)  # name -> {score,weight}


@dataclass
class CheckpointVerdict:
    blind_id: str
    checkpoint: str
    judge_score: float
    judge_passed: bool
    deterministic_score: Optional[float]
    weight: float
    reasoning: str
    evidence: str
    confidence: str
    # The backend's full decoded payload. reasoning/evidence/confidence are a
    # lossy projection of it -- CheckpointJudgeResult maps five known fields and
    # drops anything else the model returned. Keeping the payload is what lets a
    # rescore distinguish "the model answered differently" from "we changed how
    # we read the answer".
    #
    # NOT the byte-level response: both backends parse before returning
    # (`backends.py:60` returns `_parse_json(raw)`), so the original text is
    # already gone by the time the engine sees it. Capturing that would mean
    # changing eb_verify, which this pilot does not touch.
    raw_response: dict


def _read_json(path: str) -> Optional[dict]:
    """Read JSON, or None if absent/unreadable.

    A *corrupt* file is louder than a missing one: silently treating malformed
    ground truth as "no ground truth" would drop a unit from the pilot without
    anyone noticing, which is precisely the measurement-integrity failure this
    pilot exists to avoid.
    """
    try:
        with open(path) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[warn] unreadable JSON at {path}: {exc}", file=sys.stderr)
        return None


def _find_task_dir(task_id: str) -> Optional[str]:
    """Resolve a task_id to its benchmark dir (holds expected_solution.json)."""
    hits = glob.glob(
        str(REPO_ROOT / "benchmarks" / "**" / task_id / "expected_solution.json"),
        recursive=True,
    )
    hits = [h for h in hits if "_archived" not in h]
    # sorted(): glob order is filesystem-dependent, so an unsorted hits[0]
    # would resolve a task_id colliding across suites non-deterministically.
    return str(Path(sorted(hits)[0]).parent) if hits else None


def discover_units(runs_root: Path) -> list[Unit]:
    """Find every answer.json under runs_root and pair it with its arm + scores.

    Layout handled: <runs_root>/<group>/<arm>/<task_id>/workspace/agent_output/
    answer.json, with a sibling run_log.json carrying the deterministic score.
    """
    units: list[Unit] = []
    for answer_path in sorted(runs_root.glob("**/agent_output/answer.json")):
        # .../<arm>/<task_id>/workspace/agent_output/answer.json
        task_run_dir = answer_path.parents[2]
        task_id = task_run_dir.name
        arm = task_run_dir.parent.name
        run_log = _read_json(str(task_run_dir / "run_log.json")) or {}
        # Prefer the arm recorded in the artifact; fall back to the path.
        arm = run_log.get("mode", arm)
        det_cps = {
            cp["name"]: {"score": cp.get("score"), "weight": cp.get("weight", 1.0)}
            for cp in run_log.get("checkpoints", [])
            if "name" in cp
        }
        task_dir = _find_task_dir(task_id)
        if task_dir is None:
            print(f"[skip] no expected_solution.json for {task_id}", file=sys.stderr)
            continue
        units.append(
            Unit(
                task_id=task_id,
                arm=arm,
                answer_path=str(answer_path),
                task_dir=task_dir,
                deterministic_total=run_log.get("total_score"),
                deterministic_checkpoints=det_cps,
            )
        )
    return units


# Characters that differ between spellings of the same field name. Folding them
# means `toolAccess`, `tool-access`, `tool access` and `tool_access` are one key
# to this guard -- a closed list of exact spellings was how `toolUsage` walked
# straight past a scrub written for `tool_usage`.
_KEY_NOISE_RE = re.compile("[\\s_\\-\u200b\u200c\u200d\u00a0]+")


def _norm_key(key: str) -> str:
    """Fold a field name to its comparison form (case, width, separators)."""
    return _KEY_NOISE_RE.sub("", unicodedata.normalize("NFKC", key).casefold())


# Field names are declared ONCE, in their canonical spelling. The structural
# passes compare normalized forms; the text pass needs the literal spelling for
# its regex. Deriving one from the other means the two cannot disagree about
# what the vocabulary is.

# Keys that mean nothing BUT "which experimental condition". Any value under one
# of these is a tell whatever it says, so this tier needs no vocabulary of arm
# names and cannot rot as arms are added.
ARM_IDENTITY_SPELLINGS = frozenset({"arm", "experiment_arm", "tool_access"})

# Keys the harness also uses for the condition but which are ordinary answer
# vocabulary (an Ansible file mode, a Grafana/Terraform `condition`). They bind
# a token in the text pass; they are NOT refused on an arbitrary value.
ARM_CONTEXT_SPELLINGS = frozenset({"mode", "condition"})

# Keys holding harness tool telemetry echoed into the artifact. These carry no
# arm NAME, so no name-matching guard sees them, yet their shape identifies the
# arm outright (grep_calls vs sourcegraph_searches). They are harness state
# rather than answer content, so they are scrubbed, not refused.
TELEMETRY_SPELLINGS = frozenset({"tool_usage"})

# Tool-provenance field names. Unlike the keys above these are never ordinary
# answer vocabulary, so their presence ANYWHERE -- as a key, or as a value that
# survived under a wrapper the scrub did not recognize -- is a leak.
PROVENANCE_SPELLINGS = frozenset(
    {
        "grep_calls",
        "sourcegraph_searches",
        "symbol_navigations",
        "mcp_calls",
        "sgx_calls",
    }
)

# Known arm tokens. "baseline"/"mcp_only"/"hybrid"/"cli" are
# scripts/lib/shared.py's VALID_MODES (pinned by a drift test); "mcp" is the
# short form actually written to run_log.json under results/sample_runs.
ARM_TOKENS = frozenset({"baseline", "mcp_only", "mcp", "hybrid", "cli", "sgx"})

ARM_IDENTITY_KEYS = frozenset(_norm_key(k) for k in ARM_IDENTITY_SPELLINGS)
TELEMETRY_KEYS = frozenset(_norm_key(k) for k in TELEMETRY_SPELLINGS)
PROVENANCE_TOKENS = frozenset(_norm_key(k) for k in PROVENANCE_SPELLINGS)


def _alternation(words: frozenset[str]) -> str:
    """Regex alternation, longest first so a prefix cannot shadow a longer word.

    The secondary sort on the word itself keeps the compiled pattern identical
    across processes: set iteration order is hash-seeded, and this pattern is
    fingerprinted into every report as evidence of which guard ran.
    """
    return "|".join(sorted(words, key=lambda w: (-len(w), w)))


# A token bound to an arm-ish key. Used ONLY for payloads that will not parse as
# JSON -- for anything parseable the structural passes are authoritative, and
# running this over valid JSON refused ordinary prose that happened to end on
# "...retry mode: cli", aborting the whole run over a legitimate answer.
_ARM_TEXT_RE = re.compile(
    r"\b(?:"
    + _alternation(ARM_IDENTITY_SPELLINGS | ARM_CONTEXT_SPELLINGS)
    + r")\b[\"']?\s*[:=]\s*[\"']?\s*("
    + _alternation(ARM_TOKENS)
    + r")[\"']?\s*(?:[,}\]\n]|$)",
    re.IGNORECASE,
)

_TELEMETRY_TEXT_RE = re.compile(
    r"\b(?:" + _alternation(TELEMETRY_SPELLINGS | PROVENANCE_SPELLINGS) + r")\b",
    re.IGNORECASE,
)


def _walk_json(node: object) -> list[str]:
    """Collect every arm tell in a parsed payload.

    Two rules, neither of which depends on guessing the field name a leak will
    arrive under -- that guess is what let `{"provenance": "mcp_only"}` and
    `["mode", "mcp_only"]` through an earlier version:

    1. Any value under a condition-identity key. Keyed off the field, so a new
       arm name cannot slip past a stale vocabulary.
    2. Any string ANYWHERE that IS an arm or provenance token, whatever key it
       sits under and at any depth. Matching the whole string rather than a
       substring is what keeps "run the cli to reproduce" judgeable while
       refusing a bare "cli".
    """
    found: list[str] = []

    def walk(node: object, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                here = f"{path}.{key}" if isinstance(key, str) else path
                if isinstance(key, str):
                    norm = _norm_key(key)
                    if norm in ARM_IDENTITY_KEYS:
                        found.append(f"{here}={_clip(value)}")
                    elif norm in PROVENANCE_TOKENS:
                        found.append(f"{here} (tool provenance)")
                walk(key, here)
                walk(value, here)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")
        elif isinstance(node, str):
            probe = node.strip().casefold()
            if probe in ARM_TOKENS or _norm_key(node) in PROVENANCE_TOKENS:
                found.append(f"{path}={_clip(node)}")

    walk(node, "$")
    return found


def _clip(value: object, limit: int = 200) -> str:
    """repr() a leaked value for the error message, bounded.

    answer.json is agent-generated, so an unbounded repr puts an arbitrary
    amount of untrusted text into an exception message and any log carrying it.
    """
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "...(truncated)"


def _scrub_telemetry(node: object) -> tuple[object, list[str]]:
    """Drop harness telemetry blocks, returning (clean node, removed paths)."""
    removed: list[str] = []

    def scrub(node: object, path: str) -> object:
        if isinstance(node, dict):
            out: dict = {}
            for key, value in node.items():
                here = f"{path}.{key}" if isinstance(key, str) else path
                if isinstance(key, str) and _norm_key(key) in TELEMETRY_KEYS:
                    removed.append(here)
                    continue
                out[key] = scrub(value, here)
            return out
        if isinstance(node, list):
            return [scrub(v, f"{path}[{i}]") for i, v in enumerate(node)]
        return node

    return scrub(node, "$"), removed


def blind_payload(payload: str) -> tuple[str, list[str]]:
    """Return (payload safe to judge, telemetry paths that had to be removed).

    Scrub first, then assert: the telemetry block is removable, so removing it
    is strictly better than refusing a unit over it. What survives the scrub
    and still names an arm is not removable without editing the answer, so it
    fails closed.
    """
    try:
        clean, removed = _scrub_telemetry(json.loads(payload))
    except (json.JSONDecodeError, RecursionError):
        # Unparseable -- either not JSON, or nested past the interpreter's
        # recursion limit. Both mean nothing is structurally removable, and
        # neither may crash the batch: _assert_arm_neutral falls through to the
        # text pass and any refusal is reported alongside the other units.
        removed = []
    else:
        if removed:
            payload = json.dumps(clean)

    _assert_arm_neutral(payload)
    return payload, removed


def _assert_arm_neutral(payload: str) -> None:
    """Fail closed if the blinded payload names the arm the judge must not see.

    The final answer.json is arm-neutral by construction, but a task author
    could in principle echo the mode into it. Refuse rather than silently leak.

    Parseable JSON is judged structurally by ``_walk_json``; only a payload
    that will not parse falls through to the text pass. That split is the whole
    design. An earlier version ran the text regex over valid JSON too, and
    because the regex anchors to the end of a value it refused ordinary prose
    that happened to close on "...retry mode: cli" -- aborting the entire run
    over a legitimate answer, which is precisely the failure the structural
    passes were rewritten to stop.

    What the text pass buys on unparseable input is narrow but real: a
    python-repr payload (``{'arm': 'hybrid'}``) still names its arm, and
    telemetry that could have been scrubbed out of parseable JSON has to be
    refused instead, because prose offers nothing to remove.

    Known residuals, all deliberate and pinned by tests:

    * A bare arm token inside a longer sentence -- "solved via hybrid
      retrieval", "ran with mode=hybrid" -- is NOT refused in parseable JSON.
      "cli", "baseline" and "hybrid" are ordinary engineering vocabulary, so
      blocking them inside prose would reject legitimate answers wholesale, and
      deciding whether such a mention is self-identification is a semantic
      judgment rather than a substring test.
    * A future arm named after ordinary vocabulary is caught only where it sits
      under an identity key. The value scan matches known tokens.
    * On unparseable payloads only the text pass runs, and it is token-bound,
      so an unknown arm name in a python-repr payload is not caught.
    """
    try:
        leaked = _walk_json(json.loads(payload))
    except (json.JSONDecodeError, RecursionError):
        leaked = [
            m.group(0).strip()
            for pattern in (_TELEMETRY_TEXT_RE, _ARM_TEXT_RE)
            for m in pattern.finditer(payload)
        ]

    if leaked:
        raise ValueError(
            f"arm tell {sorted(set(leaked))!r} present in blinded payload "
            "-- refusing to judge"
        )


def _judge_inputs(
    unit: Unit, blinded_answer: str
) -> list[tuple[str, CheckpointJudgeInput]]:
    """Per checkpoint: (checkpoint name, judge input) over an already-blinded answer."""
    expected = _read_json(str(Path(unit.task_dir) / "expected_solution.json")) or {}
    return [
        (
            name,
            CheckpointJudgeInput(
                task_id=unit.task_id,
                checkpoint_name=name,
                agent_output=blinded_answer,
                expected_solution=cp.get("expected_solution", ""),
                evaluation_criteria=cp.get("evaluation_criteria", []),
                checkpoint_weight=unit.deterministic_checkpoints.get(name, {}).get(
                    "weight", 1.0
                ),
            ),
        )
        for name, cp in expected.get("checkpoints", {}).items()
    ]


def build_blinded_inputs(unit: Unit) -> list[tuple[str, CheckpointJudgeInput]]:
    """Per checkpoint: (checkpoint name, blinded judge input).

    The CheckpointJudgeInput carries only answer text + expected solution +
    criteria. No arm field exists on it (verified against models.py), so
    blinding here is: (1) scrub harness telemetry from the answer, (2) refuse
    what still names an arm, (3) never attach the mode.
    """
    answer, _ = blind_payload(Path(unit.answer_path).read_text())
    return _judge_inputs(unit, answer)


def _blind_all(
    blind_map: dict[str, Unit],
) -> tuple[dict[str, list[tuple[str, CheckpointJudgeInput]]], int]:
    """Blind every unit up front, or refuse the run naming every offender.

    A pre-pass, not a per-unit check inside the judging loop: blinding used to
    be asserted after the ledger was opened for writing, so a tell on the
    seventh of forty units destroyed report.json having already paid for six
    units of judge calls -- the same harm the loop's backend except-clause
    exists to prevent. Checking before the first call makes a refusal free, and
    collecting every failure means one invocation surfaces the whole problem
    instead of one name per run.
    """
    prepared: dict[str, list[tuple[str, CheckpointJudgeInput]]] = {}
    scrubbed_units = 0
    refusals: list[str] = []

    for blind_id, unit in blind_map.items():
        try:
            answer, removed = blind_payload(Path(unit.answer_path).read_text())
        except ValueError as exc:
            refusals.append(f"{unit.task_id} ({unit.answer_path}): {exc}")
            continue
        prepared[blind_id] = _judge_inputs(unit, answer)
        scrubbed_units += int(bool(removed))

    if refusals:
        raise ValueError(
            f"{len(refusals)} unit(s) name an arm -- refusing to judge:\n  "
            + "\n  ".join(refusals)
        )
    return prepared, scrubbed_units


def _assign_blind_ids(units: list[Unit]) -> dict[str, Unit]:
    """Opaque id per unit, so nothing downstream has to name a task or an arm."""
    return {f"U{i:04d}": unit for i, unit in enumerate(units)}


def run_pilot(units: list[Unit], model: str, seed: int, out_dir: Path) -> dict:
    """Judge every unit in shuffled order, blind to arm; write ledger + report."""
    blind_map = _assign_blind_ids(units)
    order = list(blind_map)
    random.Random(seed).shuffle(order)

    # Blind before anything is opened, constructed, or paid for.
    prepared, scrubbed_units = _blind_all(blind_map)

    judge = LLMJudge(model=model, temperature=0.0)
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = out_dir / "verdicts.jsonl"
    verdicts: list[CheckpointVerdict] = []
    infra_errors: list[dict] = []

    with open(ledger_path, "w") as ledger:
        for blind_id in order:
            unit = blind_map[blind_id]
            for name, judge_input in prepared[blind_id]:
                det = unit.deterministic_checkpoints.get(name, {})
                try:
                    res = judge.evaluate_checkpoint(
                        judge_input, task_description="", checkpoint_description=name
                    )
                except Exception as exc:
                    # No verdict reached. Do NOT coerce to a score (scorer_guard
                    # invariant): record as infra error and move on.
                    #
                    # Deliberately broad. evaluate_checkpoint documents only
                    # JudgeBackendError/JudgeScoreError, but the SDK can raise
                    # connection/timeout errors the backend does not wrap, and
                    # letting one flaky call abort the loop would discard the
                    # whole run's report.json -- the artifact of record. The
                    # exception type is recorded so a genuine code bug shows up
                    # in infra_errors_detail instead of hiding as "flakiness".
                    infra_errors.append(
                        {
                            "blind_id": blind_id,
                            "checkpoint": name,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                    )
                    continue
                v = CheckpointVerdict(
                    blind_id=blind_id,
                    checkpoint=name,
                    judge_score=res.score,
                    judge_passed=res.passed,
                    deterministic_score=det.get("score"),
                    weight=det.get("weight", 1.0),
                    reasoning=res.reasoning,
                    evidence=res.evidence,
                    confidence=res.confidence,
                    raw_response=res.raw_response,
                )
                verdicts.append(v)
                # Ledger row reveals arm ONLY here (post-hoc), never to the judge.
                row = asdict(v) | {"task_id": unit.task_id, "arm": unit.arm}
                ledger.write(json.dumps(row) + "\n")

    report = _agreement_report(
        blind_map,
        verdicts,
        model,
        seed,
        infra_errors,
        judge.pass_threshold,
        scrubbed_units,
    )
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    return report


def _weighted_total(pairs: list[tuple[float, float]]) -> Optional[float]:
    tot_w = sum(w for _, w in pairs)
    if tot_w == 0:
        return None
    return sum(s * w for s, w in pairs) / tot_w


def _agreement_report(
    blind_map: dict[str, Unit],
    verdicts: list[CheckpointVerdict],
    model: str,
    seed: int,
    infra_errors: list[dict],
    pass_threshold: float,
    scrubbed_units: int,
) -> dict:
    """Report judge-vs-deterministic agreement. No blending: parallel view."""
    paired = [v for v in verdicts if v.deterministic_score is not None]
    cp_abs_diffs = [abs(v.judge_score - v.deterministic_score) for v in paired]
    # Binary pass-agreement at the judge's own pass line (read off the engine
    # rather than restated here, so the two cannot drift apart).
    pass_agree = sum(
        1
        for v in paired
        if (v.judge_score >= pass_threshold) == (v.deterministic_score >= pass_threshold)
    )
    # Per (task, arm) weighted totals: judge vs deterministic run_log total.
    by_unit: dict[str, list[CheckpointVerdict]] = {}
    for v in verdicts:
        by_unit.setdefault(v.blind_id, []).append(v)
    unit_rows = []
    for blind_id, vs in by_unit.items():
        unit = blind_map[blind_id]
        unit_paired = [v for v in vs if v.deterministic_score is not None]
        judge_total = _weighted_total([(v.judge_score, v.weight) for v in unit_paired])
        # judge_total renormalizes over the checkpoints this pilot could pair;
        # deterministic_total came from run_log.json over ALL of that run's
        # checkpoints. When curated expected_solution.json covers only some of
        # them the two totals have different denominators, and the delta then
        # reflects coverage, not disagreement. Surface the ratio per unit so a
        # reader can tell those apart instead of having to trust they match.
        covered = len(unit_paired)
        total_cps = len(unit.deterministic_checkpoints) or covered
        unit_rows.append(
            {
                "task_id": unit.task_id,
                "arm": unit.arm,
                "judge_total": judge_total,
                "deterministic_total": unit.deterministic_total,
                "checkpoints_covered": covered,
                "checkpoints_in_run_log": len(unit.deterministic_checkpoints),
                "full_coverage": covered == total_cps,
                "delta": (
                    None
                    if judge_total is None or unit.deterministic_total is None
                    else round(judge_total - unit.deterministic_total, 3)
                ),
            }
        )
    # Deltas are only comparable under full coverage; partial-coverage units
    # are reported in `units` but excluded from the aggregate.
    unit_deltas = [
        r["delta"] for r in unit_rows if r["delta"] is not None and r["full_coverage"]
    ]

    def _stats(xs: list[float]) -> dict:
        if not xs:
            return {"n": 0}
        return {
            "n": len(xs),
            "mean": round(statistics.fmean(xs), 3),
            "median": round(statistics.median(xs), 3),
            "max": round(max(xs), 3),
        }

    return {
        "config": {
            "model": model,
            "seed": seed,
            "rubric": "eb_verify.judge.prompts.CHECKPOINT_EVAL_PROMPT",
            "rubric_sha256": _rubric_hash(),
            "pass_threshold": pass_threshold,
            "blending": "none (secondary outcome, parallel to deterministic reward)",
            # Evidence that blinding ran, on the same reasoning as
            # rubric_sha256: a reader should not have to take it on faith.
            "blinding": {
                "units_checked": len(blind_map),
                "units_telemetry_scrubbed": scrubbed_units,
                "refusal_policy": "abort before judging",
                "vocabulary_sha256": _blinding_vocabulary_hash(),
            },
        },
        "sample": {
            "units": len(blind_map),
            "arms": sorted({u.arm for u in blind_map.values()}),
            "checkpoints_judged": len(verdicts),
            "checkpoints_paired_with_deterministic": len(paired),
            "infra_errors": len(infra_errors),
        },
        "checkpoint_agreement": {
            "abs_diff": _stats(cp_abs_diffs),
            "pass_agreement_rate": (
                round(pass_agree / len(paired), 3) if paired else None
            ),
        },
        "unit_total_agreement": {
            "abs_delta": _stats([abs(d) for d in unit_deltas]),
            "units_excluded_partial_coverage": len(
                [r for r in unit_rows if not r["full_coverage"]]
            ),
        },
        "units": sorted(unit_rows, key=lambda r: (r["task_id"], r["arm"])),
        "infra_errors_detail": infra_errors,
    }


def _blinding_vocabulary_hash() -> str:
    """Fingerprint the key/token sets, so a report says which guard produced it.

    Blinding strength is a property of this vocabulary. Two reports carrying
    different hashes were not blinded to the same standard, and without the
    fingerprint that difference is invisible after the fact. The free-text
    regexes are compiled from these same sets, so hashing the sets covers every
    pass rather than only the structural ones.
    """
    vocab = json.dumps(
        {
            "identity_keys": sorted(ARM_IDENTITY_SPELLINGS),
            "provenance_tokens": sorted(PROVENANCE_SPELLINGS),
            "context_keys": sorted(ARM_CONTEXT_SPELLINGS),
            "telemetry_keys": sorted(TELEMETRY_SPELLINGS),
            "arm_tokens": sorted(ARM_TOKENS),
        },
        sort_keys=True,
    )
    return hashlib.sha256(vocab.encode()).hexdigest()


def _rubric_hash() -> str:
    from eb_verify.judge.prompts import CHECKPOINT_EVAL_PROMPT, CHECKPOINT_EVAL_SYSTEM

    h = hashlib.sha256()
    h.update(CHECKPOINT_EVAL_SYSTEM.encode())
    h.update(CHECKPOINT_EVAL_PROMPT.encode())
    return h.hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--repo-root",
        default=None,
        help="resolve benchmarks/ + runs-root here (default: this checkout)",
    )
    ap.add_argument("--runs-root", default="results/sample_runs")
    ap.add_argument("--model", default="cc:haiku")
    ap.add_argument("--seed", type=int, default=20260719)
    ap.add_argument("--out", default="results/analysis/rryas10_pilot")
    ap.add_argument(
        "--limit", type=int, default=0, help="cap number of units (0 = all)"
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="discover + blind-check units, but do not call the judge",
    )
    args = ap.parse_args()

    if args.repo_root:
        global REPO_ROOT
        REPO_ROOT = Path(args.repo_root).resolve()

    runs_root = (REPO_ROOT / args.runs_root).resolve()
    units = discover_units(runs_root)
    if args.limit:
        # Keep arms balanced under the cap: interleave by arm.
        by_arm: dict[str, list[Unit]] = {}
        for u in units:
            by_arm.setdefault(u.arm, []).append(u)
        interleaved: list[Unit] = []
        while len(interleaved) < args.limit and any(by_arm.values()):
            for arm in sorted(by_arm):
                if by_arm[arm] and len(interleaved) < args.limit:
                    interleaved.append(by_arm[arm].pop(0))
        units = interleaved

    print(
        f"[pilot] {len(units)} units across arms "
        f"{sorted({u.arm for u in units})} from {runs_root}",
        file=sys.stderr,
    )
    if args.dry_run:
        # Same pre-pass the real run uses, so a dry-run names every offender
        # rather than only the first.
        _blind_all(_assign_blind_ids(units))
        print("[pilot] dry-run OK: units discovered and blind-checked", file=sys.stderr)
        return 0

    report = run_pilot(units, args.model, args.seed, (REPO_ROOT / args.out).resolve())
    print(json.dumps(report["checkpoint_agreement"], indent=2))
    print(json.dumps(report["unit_total_agreement"], indent=2))
    print(f"[pilot] ledger + report written to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
