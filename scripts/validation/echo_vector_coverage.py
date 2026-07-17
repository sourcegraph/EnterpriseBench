#!/usr/bin/env python3
"""Measure whether the prompt-echo invariant's md-copy vector EXERCISES the
checks it certifies clean.

The invariant (tests/integrity/test_report_prompt_echo.py) asserts that a
verbatim prompt copy earns zero credit on every check. Its only vector is
`cp instruction.md -> <deliverable>`. `curated_gate_analyzer.echo_leak()`
keeps only checks scoring > 0:

    return {k: v for k, v in echo_scores(...).items() if v is not None and v > 0}

A check that never produced a verdict scores None, and None is filtered out.
So a check that THREW on the echo input is indistinguishable, at the invariant's
level, from a check that ran and correctly refused credit. For a JSON
deliverable the md copy is not valid JSON, so every json.load-based check
raises, the `set -euo pipefail` wrapper exits non-zero with empty stdout, and
the task is certified clean without the vector ever testing echo-solvability.

This tool reports, per active report task, whether the vector actually
exercised the checks:

    exercised  every check returned a real verdict, all zero -> clean is EARNED
    partial    some checks returned a verdict, some threw    -> partly vacuous
    vacuous    NO check returned a verdict                   -> clean is VACUOUS
    leak       some check credited the echo (> 0)            -> real leak

--json-echo then answers the follow-on question: for the checks the md vector
never exercised, what does an HONEST vector score? It marshals text into the
answer shape (the prompt's ```json fence when it publishes one, otherwise the
shape its checks actually read) and scores every check, three times:

    treatment  this task's own prompt     what a prompt echo can score
    filler     lorem ipsum, same shape    structural credit — any well-formed
                                          answer of this shape earns it
    foreign    ANOTHER task's real prompt domain-prose credit — English, same
                                          register, none of this task's ground truth

Only credit beyond BOTH controls is attributable to this prompt's vocabulary.
The foreign arm is load-bearing: these checks match English ground-truth
substrings, so Latin filler cannot score them for reasons having nothing to do
with echo, and controlling against filler alone credits "the answer is English
prose" as a leak — overstating the delta and mis-ranking which tasks are worst.

Deltas are compared SHAPE BY SHAPE and then maxed, never maxed per arm and
subtracted: an unrelated shape where a control happened to score must not erase
a real leak visible on another. Where no control produced a verdict the delta is
None, not 0 — unknown is not "no leak", and an `or 0` there would reproduce
inside this tool the exact None-is-zero conflation it exists to expose.

Measurement only. This tool changes no verdict and enforces nothing; it
quantifies what the existing invariant does and does not cover.

Usage:
  python3 scripts/validation/echo_vector_coverage.py             # table + summary
  python3 scripts/validation/echo_vector_coverage.py --json      # machine-readable
  python3 scripts/validation/echo_vector_coverage.py --json-echo # honest-vector probe
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import curated_gate_analyzer as gate  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
QUARANTINE_FILE = REPO_ROOT / "tests" / "integrity" / "known_prompt_echo_leaks.json"

# ```json ... ``` fence in instruction.md — the answer shape the prompt publishes.
JSON_FENCE_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)

# Any string key a check pulls out of a dict: `x.get('path')` or `e["symbol"]`.
# Deliberately NOT anchored to the identifier `answer`: checks bind the parsed
# deliverable to whatever name they like (`report`, `claimed`, `data`), and
# anchoring to one name silently shrinks the probe on the rest — the very failure
# this tool exists to expose.
KEY_ACCESS_RE = re.compile(
    r"""\.get\(\s*['"]([^'"]+)['"]|\[\s*['"]([^'"]+)['"]\s*\]"""
)

# os.environ['REPORT'] and friends are shell/env reads, not answer fields. They
# match KEY_ACCESS_RE, and letting them through is what keeps the
# DEFAULT_LEAF_FIELDS fallback from ever firing. Answer fields are snake_case;
# env vars are SCREAMING_CASE.
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Always offered, never merely fallen back to: a check reading `f['path']` via an
# idiom the scrape misses must still find the field present.
DEFAULT_LEAF_FIELDS = ("path", "file", "name", "description", "reason", "detail",
                       "symbol", "evidence")

# Control arm 1 — shape-only. Same JSON shape, same bulk, no natural language at
# all. Credit surviving this is structural: any well-formed answer earns it.
FILLER = "lorem ipsum dolor sit amet consectetur adipiscing elit " * 40


def deliverable_kind(deliverables: list[str]) -> str:
    """json_only / mixed / non_json — which echo vector could exercise this."""
    if not deliverables:
        return "none"
    if all(d.endswith(".json") for d in deliverables):
        return "json_only"
    if any(d.endswith(".json") for d in deliverables):
        return "mixed"
    return "non_json"


def classify(scores: dict) -> str:
    """Verdict for one task under the md-copy echo vector."""
    if any(v is not None and v > 0 for v in scores.values()):
        return "leak"
    scored = [v for v in scores.values() if v is not None]
    if not scored:
        return "vacuous"
    if len(scored) < len(scores):
        return "partial"
    return "exercised"


def measure(task_dir: Path) -> dict:
    deliverables = gate.deliverable_paths(task_dir)
    scores = gate.echo_scores(task_dir)
    rel = str(task_dir.relative_to(gate.BENCH))
    return {
        "task": rel,
        "suite": rel.split("/")[0],
        "deliverables": deliverables,
        "deliverable_kind": deliverable_kind(deliverables),
        "n_checks": len(scores),
        "n_scored": sum(1 for v in scores.values() if v is not None),
        "n_threw": sum(1 for v in scores.values() if v is None),
        "scores": scores,
        "verdict": classify(scores),
    }


def answer_template(prompt: str) -> object | None:
    """The answer shape the prompt itself publishes, as a parsed template.

    Returns None when the prompt ships no parseable ```json fence — most report
    prompts don't, which is why candidate_templates() also derives shapes from
    the checks.
    """
    for block in JSON_FENCE_RE.findall(prompt):
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue
    return None


def fill(node: object, text: str) -> object:
    """Replace every string leaf of the template with `text`, shape preserved."""
    if isinstance(node, str):
        return text
    if isinstance(node, list):
        return [fill(v, text) for v in node] if node else [text]
    if isinstance(node, dict):
        return {k: fill(v, text) for k, v in node.items()}
    return node


def accessed_keys(task_dir: Path) -> tuple[str, ...]:
    """Every dict key the checks read, whatever variable they read it from.

    Most report prompts publish no ```json fence, yet their checks still
    json.load the deliverable, so the shape is discoverable only here. Scraped
    without anchoring to a variable name (`answer`/`report`/`claimed` are all in
    use) and without anchoring to a nesting level: a superset is inert (an unread
    key is filler) whereas a missing one shrinks the probe silently and reports
    the task as echo-resistant when it was merely never exercised.

    SCREAMING_CASE names are dropped — they are os.environ reads, not answer
    fields, and letting them through masks a genuinely empty scrape.
    """
    found: set[str] = set()
    for sh in sorted((task_dir / "checks").glob("*.sh")):
        for a, b in KEY_ACCESS_RE.findall(sh.read_text(errors="replace")):
            name = a or b
            if not ENV_NAME_RE.match(name):
                found.add(name)
    return tuple(sorted(found))


def leaf_fields(task_dir: Path) -> tuple[str, ...]:
    """Field names to offer inside list-of-dict answers.

    Union, not fallback: the scrape and the defaults each cover idioms the other
    misses, and an extra field costs nothing while a missing one costs a false
    clean.
    """
    return tuple(sorted(set(accessed_keys(task_dir)) | set(DEFAULT_LEAF_FIELDS)))


def candidate_templates(task_dir: Path, prompt: str) -> list[object]:
    """Every answer shape worth trying, richest evidence first.

    The prompt's own fence when it has one, plus check-derived shapes because a
    check's expected type is not knowable statically. Content is ALWAYS only the
    filled text; the shape is inert scaffolding, so trying several keeps this a
    measure of what prompt VOCABULARY can buy rather than of shape-guessing luck.
    """
    out: list[object] = []
    fence = answer_template(prompt)
    if fence is not None:
        out.append(fence)
    keys = accessed_keys(task_dir)
    leaves = leaf_fields(task_dir)
    if keys:
        # A list of strings covers the scalar reader too: checks stringify the
        # value before matching (`json.dumps(v)` / `' '.join(str(c) ...)`), so a
        # separate {k: ""} shape scored identically on every task measured and
        # only doubled the subprocess count.
        out.append({k: [""] for k in keys})
        out.append({k: [{f: "" for f in leaves}] for k in keys})
    # Bare top-level list (`claimed = json.load(f)` then e["file"]/e["symbol"]) —
    # the heredoc idiom a key scrape cannot attribute to a variable. Always worth
    # trying: a shape that fits nothing throws and contributes no score.
    out.append([{f: "" for f in leaves}])
    return out


def probe_shape(task_dir: Path, text: str, tpl: object,
                deliverables: list[str]) -> dict:
    """Score every check against ONE shape filled with `text`."""
    body = json.dumps(fill(tpl, text))
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        gate.materialize(ws, deliverables, body)
        return gate.run_checks(task_dir, ws)


def best_per_check(per_shape: list[dict]) -> dict:
    """Highest score each check reached under any shape; None if it never ran.

    A shape that makes one check throw must not mask another check the same text
    would have credited.
    """
    best: dict[str, float | None] = {}
    for scores in per_shape:
        for k, v in scores.items():
            if v is None:
                best.setdefault(k, None)
            elif best.get(k) is None or v > best[k]:
                best[k] = v
    return best


def mean(scores: dict) -> float | None:
    """Mean over the checks that produced a verdict. None if none did.

    Callers MUST report n_scored/n_checks alongside this: it is an average over
    what ran, not over what the task grades, and conflating those is the defect
    this tool exists to expose.
    """
    vals = [v for v in scores.values() if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def per_check_echo_delta(arms: dict) -> dict:
    """Credit attributable to THIS prompt's vocabulary, per check.

    Compared SHAPE BY SHAPE, then maxed — not maxed per arm and subtracted. An
    unrelated shape where a control happened to score well must not erase a real
    vocabulary leak visible on another shape.

    None (not 0) when no shape produced a verdict for both the treatment and at
    least one control: unknown is not "no leak". `or 0` here would reproduce the
    None-is-zero conflation this whole tool indicts.
    """
    out: dict[str, float | None] = {}
    checks = {k for scores in arms["treatment"] for k in scores}
    for k in checks:
        deltas = []
        for i, t_scores in enumerate(arms["treatment"]):
            t = t_scores.get(k)
            if t is None:
                continue
            baselines = [arm[i].get(k) for arm in
                         (arms["filler"], arms["foreign"]) if arm[i].get(k) is not None]
            if not baselines:
                continue
            deltas.append(t - max(baselines))
        out[k] = round(max(deltas), 3) if deltas else None
    return out


def measure_json_echo(task_dir: Path, foreign: str) -> dict:
    """Treatment vs TWO controls, in the answer shape the checks actually read.

    treatment  this task's own prompt      — what an echo can score
    filler     lorem ipsum, same shape     — structural credit: any well-formed answer
    foreign    ANOTHER task's real prompt  — domain-prose credit: English, right
                                             register, none of this task's ground truth

    The foreign arm is what makes the attribution honest. These checks match
    English ground-truth substrings, so Latin filler cannot score them for reasons
    that have nothing to do with prompt vocabulary — controlling only against
    filler credits "the answer is English prose" as an echo leak. Only credit
    beyond BOTH controls is attributable to this prompt.
    """
    rel = str(task_dir.relative_to(gate.BENCH))
    row: dict = {"task": rel, "suite": rel.split("/")[0]}
    prompt = prompt_text(task_dir)
    if prompt is None:
        return row | {"probe": "no_instruction_md"}
    templates = candidate_templates(task_dir, prompt)
    deliverables = gate.deliverable_paths(task_dir)
    arms = {
        name: [probe_shape(task_dir, text, tpl, deliverables) for tpl in templates]
        for name, text in (("treatment", prompt), ("filler", FILLER),
                           ("foreign", foreign))
    }
    best = {name: best_per_check(shapes) for name, shapes in arms.items()}
    t_mean = mean(best["treatment"])
    if t_mean is None:
        return row | {"probe": "checks_still_threw",
                      "n_checks": len(best["treatment"])}
    deltas = per_check_echo_delta(arms)
    scored = [v for v in deltas.values() if v is not None]
    return row | {
        "probe": "probed",
        "n_checks": len(best["treatment"]),
        "n_scored": sum(1 for v in best["treatment"].values() if v is not None),
        "n_threw": sum(1 for v in best["treatment"].values() if v is None),
        "treatment_mean": t_mean,
        "filler_mean": mean(best["filler"]),
        "foreign_mean": mean(best["foreign"]),
        "echo_delta": round(sum(scored) / len(scored), 3) if scored else None,
        "treatment": best["treatment"],
        "filler": best["filler"],
        "foreign": best["foreign"],
        "per_check_delta": deltas,
        # THE INVARIANT'S OWN PROPERTY: "a prompt copy must earn ZERO credit on
        # EVERY check". That is an absolute threshold — any check the echo scores
        # above 0 violates it, whatever a control does. No control needed, and no
        # dependence on which foreign prompt got picked.
        "checks_crediting_echo": sorted(k for k, v in best["treatment"].items()
                                        if v is not None and v > 0),
        # scores beyond BOTH controls on some shape: this prompt's vocabulary
        "echo_leaking_checks": sorted(k for k, d in deltas.items()
                                      if d is not None and d > 0),
        # scores, but no better than a control: credit any answer of this shape earns
        "structural_freebie_checks": sorted(
            k for k, d in deltas.items()
            if d is not None and d <= 0 and (best["treatment"].get(k) or 0) > 0
        ),
        # treatment scored but no control ever ran: attribution unknown, not zero
        "control_inconclusive_checks": sorted(
            k for k, d in deltas.items()
            if d is None and (best["treatment"].get(k) or 0) > 0
        ),
    }


def prompt_text(task_dir: Path) -> str | None:
    """This task's prompt, or None when it ships none.

    Guarded: gate.echo_scores() guards the same read, and an unguarded one here
    would let a single task without an instruction.md abort the whole sweep
    through ThreadPoolExecutor.map.
    """
    instr = task_dir / "instruction.md"
    if not instr.exists():
        return None
    return instr.read_text(errors="replace")


def foreign_prompt(task_dir: Path, pool: list[Path]) -> str | None:
    """A real prompt from a DIFFERENT task, preferring the SAME suite.

    The domain-prose control: English, written to the same template, about the
    same kind of problem — but not this task. Same-suite is deliberate and is the
    CONSERVATIVE choice: a sibling shares this task's domain vocabulary, so any
    credit it earns is credit generic prose earns, and only what the real prompt
    scores BEYOND it is task-specific. Picking a distant suite would make the
    control nearly as inert as Latin filler and inflate the delta.

    It errs toward understating: if a sibling happens to share ground-truth
    terms, the control scores high and the leak is reported smaller than it is.
    For a number backing an escalation, understating is the safe direction.

    Deterministic — offset halfway around the sorted pool, so pairings are stable
    across runs and no task controls against itself.
    """
    ordered = sorted((d for d in pool if d != task_dir), key=str)
    if not ordered:
        return None
    start = len(ordered) // 2
    rotated = ordered[start:] + ordered[:start]
    for same_suite in (True, False):
        for cand in rotated:
            if (cand.parent == task_dir.parent) is same_suite:
                text = prompt_text(cand)
                if text:
                    return text
    return None


def run_json_echo(rows: list[dict], workers: int, as_json: bool) -> int:
    """Probe every task the md vector left unexercised, in whole or in part.

    Cohort is `n_threw > 0`, NOT the vacuous/partial labels: classify() reports
    one label per task and lets `leak` win, so a task leaking on one check while
    five others throw would hide its five unexercised checks behind the label.
    Quarantined tasks are probed too — quarantine records one known leaking
    check, which says nothing about whether the rest were ever exercised.
    """
    targets = [r for r in rows if r["n_threw"] > 0]
    pool = [gate.BENCH / r["task"] for r in rows]

    def run(r: dict) -> dict:
        task_dir = gate.BENCH / r["task"]
        foreign = foreign_prompt(task_dir, pool)
        if foreign is None:
            return {"task": r["task"], "suite": r["task"].split("/")[0],
                    "probe": "no_foreign_control"}
        return measure_json_echo(task_dir, foreign) | {
            "quarantined": r["quarantined"], "md_verdict": r["verdict"]}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        out = list(ex.map(run, targets))
    out.sort(key=lambda r: -(r.get("echo_delta") or 0))

    if as_json:
        print(json.dumps(out, indent=2))
        return 0

    print(f"{'task':<52} {'treat':>6} {'fill':>6} {'frgn':>6} {'delta':>6} "
          f"{'ran':>5}  status")
    print("-" * 104)
    for r in out:
        if r["probe"] != "probed":
            print(f"{r['task']:<52} {'—':>6} {'—':>6} {'—':>6} {'—':>6} "
                  f"{'—':>5}  {r['probe']}")
            continue
        status = "ECHO-LEAK" if r["echo_leaking_checks"] else "no echo credit"
        if r["control_inconclusive_checks"]:
            status += " (+inconclusive)"
        print(f"{r['task']:<52} {r['treatment_mean']:>6} "
              f"{str(r['filler_mean']):>6} {str(r['foreign_mean']):>6} "
              f"{str(r['echo_delta']):>6} {r['n_scored']}/{r['n_checks']:<3}  {status}")

    probed = [r for r in out if r["probe"] == "probed"]
    crediting = [r for r in probed if r["checks_crediting_echo"]]
    leaking = [r for r in probed if r["echo_leaking_checks"]]
    print("-" * 104)
    print(f"cohort (>=1 check the md vector never exercised) : {len(targets)}")
    print(f"probed                                          : {len(probed)}")
    for reason in ("checks_still_threw", "no_instruction_md", "no_foreign_control"):
        n = len([r for r in out if r["probe"] == reason])
        if n:
            print(f"  unprobeable ({reason}): {n}")
    partial = [r for r in probed if r["n_threw"]]
    if partial:
        print(f"  NB {len(partial)} probed rows average over a SUBSET of their "
              f"checks (see ran column)")

    # Headline: the invariant's OWN property, which needs no control.
    print(f"\nVIOLATE THE INVARIANT'S STATED PROPERTY  : {len(crediting)}")
    print('  ("a prompt copy must earn ZERO credit on every check" — these score >0)')
    for suite, n in Counter(r["suite"] for r in crediting).most_common():
        print(f"  {suite:<28} {n}")

    # Attribution: of that credit, how much is the PROMPT and not the shape.
    print(f"\nof those, credit beyond BOTH controls (task-specific vocabulary) : "
          f"{len(leaking)}")
    freebie = [r for r in probed if r["structural_freebie_checks"]]
    incon = [r for r in probed if r["control_inconclusive_checks"]]
    print(f"structural freebies (credit a control also earns)                : "
          f"{len(freebie)}")
    if incon:
        print(f"control-inconclusive (attribution unknown, NOT zero)             : "
              f"{len(incon)}")
    deltas = [r["echo_delta"] for r in probed if r["echo_delta"] is not None]
    if deltas:
        print(f"\nmean echo delta over probed cohort: "
              f"{round(sum(deltas)/len(deltas), 3)}")
        print(f"max  echo delta: {max(deltas)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--json-echo", action="store_true",
                    help="probe the md-unexercised cohort with a JSON-shaped "
                         "prompt echo (treatment) vs filler (control)")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    tasks = gate.discover_report_tasks()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        rows = list(ex.map(measure, tasks))
    rows.sort(key=lambda r: (r["verdict"], r["task"]))

    quarantine = set(json.loads(QUARANTINE_FILE.read_text()))
    for r in rows:
        r["quarantined"] = r["task"] in quarantine

    if args.json_echo:
        return run_json_echo(rows, args.workers, args.json)

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    print(f"{'task':<62} {'kind':<9} {'checks':>6} {'scored':>6} {'threw':>5}  verdict")
    print("-" * 108)
    for r in rows:
        print(f"{r['task']:<62} {r['deliverable_kind']:<9} {r['n_checks']:>6} "
              f"{r['n_scored']:>6} {r['n_threw']:>5}  {r['verdict']}")

    print("-" * 108)
    print(f"active report tasks : {len(rows)}")
    print(f"quarantined         : {len(quarantine)}")
    for v in ("exercised", "partial", "vacuous", "leak"):
        sel = [r for r in rows if r["verdict"] == v]
        print(f"{v:<20}: {len(sel)}")

    # The headline: tasks the invariant certifies clean without testing them.
    uncovered = [r for r in rows if r["verdict"] in ("vacuous", "partial")
                 and not r["quarantined"]]
    print(f"\nCERTIFIED-BUT-UNEXERCISED (unquarantined, vacuous or partial): "
          f"{len(uncovered)}")
    for suite, n in Counter(r["suite"] for r in uncovered).most_common():
        print(f"  {suite:<28} {n}")
    by_kind = Counter(r["deliverable_kind"] for r in uncovered)
    print("  by deliverable kind:", dict(sorted(by_kind.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
