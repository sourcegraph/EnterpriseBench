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

--json-echo then asks the follow-on question: for the checks the md vector never
exercised, what does an HONEST vector score? It marshals text into the answer
shape (the prompt's ```json fence when it publishes one, otherwise the shape its
checks actually read) and scores every check, once per arm:

    treatment  this task's own prompt     what a prompt echo can score
    nonsense   "zzz"                      probe integrity — credit here came from
                                          the scaffold THIS TOOL built
    filler     lorem ipsum                structural — any well-formed answer of
                                          this shape earns it
    foreign    a real prompt from another domain prose — English, same register,
               task of the SAME TYPE      same check family, not this task

Each control strips one explanation away; only credit surviving ALL THREE is
attributable to this prompt's own vocabulary.

Read the arms as a ladder, and read the CONTROLS, not just the treatment. What
this tool can honestly say is "N tasks credit content that isn't an answer";
what it cannot say is "N tasks violate the invariant". The invariant's stated
property is scoped to ITS vector (`cp instruction.md`); the treatment here is a
synthesized JSON scaffold, a vector the invariant never made a claim about, so an
uncontrolled treatment count is not a violation count.

Three lessons are wired in as mechanism, each learned the hard way when this tool
reproduced the very defect it exists to expose:

  - the nonsense arm exists because the key scrape once lifted ground-truth match
    strings out of list literals in the checks and injected them as answer keys;
    "zzz" then scored 0.6 on a task with no prompt text present. Keys are NOT
    inert — checks match against json.dumps(answer), which contains every key.
  - deltas are compared SHAPE BY SHAPE then maxed, never maxed per arm and
    subtracted, and the task figure is the WORST CHECK, never a mean: averaging
    signed deltas lets a check the controls win cancel a real leak on another,
    and it sorted genuine leakers to the bottom of the table.
  - where no control produced a verdict the delta is None, not 0. Unknown is not
    "no leak", and an `or 0` there is exactly the None-is-zero conflation
    indicted above.

Every mean here is over the checks that RAN, not over the checks the task grades;
rows carry n_scored/n_checks so the denominator travels with the number.

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
import tomllib
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
# anchoring to one name silently shrinks the probe on the rest.
#
# The subscript alternative REQUIRES a preceding identifier/paren/bracket. Without
# that lookbehind it also matches a bare single-element list literal — and the
# checks are full of them, holding GROUND-TRUTH match strings
# (`'reraise_mechanism': ['_reraise_as_remote_protocol_error']`). Scraping those
# as answer keys injects the check's own answers into the body it then greps, so
# the probe scores itself. Keys are NOT inert: checks match against
# `json.dumps(answer)`, which contains every key name.
KEY_ACCESS_RE = re.compile(
    r"""\.get\(\s*['"]([^'"]+)['"]|(?<=[\w\)\]])\[\s*['"]([^'"]+)['"]\s*\]"""
)

# os.environ['REPORT'] and friends are shell/env reads, not answer fields. They
# match KEY_ACCESS_RE, and letting them through is what keeps the
# DEFAULT_LEAF_FIELDS fallback from ever firing. Answer fields are snake_case;
# env vars are SCREAMING_CASE.
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# A key is an identifier. Anything with a space, a slash or a file extension is
# a ground-truth match string that slipped through, not a field the answer
# declares — a second net under the lookbehind above.
FIELD_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$", re.IGNORECASE)

# Always offered, never merely fallen back to: a check reading `f['path']` via an
# idiom the scrape misses must still find the field present.
DEFAULT_LEAF_FIELDS = ("path", "file", "name", "description", "reason", "detail",
                       "symbol", "evidence")

# Control arm — prose-shaped but meaningless. Credit surviving this is structural:
# any well-formed answer of this shape earns it.
FILLER = "lorem ipsum dolor sit amet consectetur adipiscing elit " * 40

# Probe-integrity arm. Content carrying no words at all, so ANY credit it earns
# came from the scaffold this tool synthesized — the shape or the key names — and
# not from content. A check that scores here is measuring the probe, not the
# answer, and its credit must never be attributed to a prompt echo. This arm
# exists because the scrape once pulled ground-truth strings out of list literals
# in the checks and injected them as keys, and "zzz" scored 0.6 on a task with no
# prompt text present.
NONSENSE = "zzz"


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
            if not ENV_NAME_RE.match(name) and FIELD_NAME_RE.match(name):
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


def scored_above_zero(scores: dict, check: str) -> bool:
    """Did this check actually run AND earn credit?

    Explicit rather than `(scores.get(k) or 0) > 0`: that idiom reads a check that
    THREW as one that scored zero, which is the conflation this whole tool exists
    to expose. Absent and zero are different facts and must not share a branch.
    """
    v = scores.get(check)
    return v is not None and v > 0


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
    controls = [name for name in arms if name != "treatment"]
    for k in checks:
        deltas = []
        for i, t_scores in enumerate(arms["treatment"]):
            t = t_scores.get(k)
            if t is None:
                continue
            baselines = [arms[c][i].get(k) for c in controls
                         if arms[c][i].get(k) is not None]
            if not baselines:
                continue
            deltas.append(t - max(baselines))
        out[k] = round(max(deltas), 3) if deltas else None
    return out


def measure_json_echo(task_dir: Path, foreign: str, foreign_id: str = "") -> dict:
    """Treatment vs three controls, in the answer shape the checks actually read.

    treatment  this task's own prompt     — what an echo can score
    nonsense   "zzz"                      — probe integrity: credit here came from
                                            the scaffold THIS TOOL built, not content
    filler     lorem ipsum                — structural: any well-formed answer earns it
    foreign    another task's real prompt — domain prose: English, same register,
                                            not this task

    Each control strips one explanation away, and only credit surviving ALL THREE
    is attributable to this prompt's own vocabulary. The nonsense arm is the one
    that keeps this tool honest about itself: the scrape once lifted ground-truth
    strings out of list literals in the checks and injected them as answer keys,
    and "zzz" scored 0.6 on a task with no prompt text present. Any check the
    nonsense arm scores is measuring the probe, not the answer.
    """
    rel = str(task_dir.relative_to(gate.BENCH))
    row: dict = {"task": rel, "suite": rel.split("/")[0], "foreign_control": foreign_id}
    prompt = prompt_text(task_dir)
    if prompt is None:
        return row | {"probe": "no_instruction_md"}
    templates = candidate_templates(task_dir, prompt)
    deliverables = gate.deliverable_paths(task_dir)
    arms = {
        name: [probe_shape(task_dir, text, tpl, deliverables) for tpl in templates]
        for name, text in (("treatment", prompt), ("nonsense", NONSENSE),
                           ("filler", FILLER), ("foreign", foreign))
    }
    best = {name: best_per_check(shapes) for name, shapes in arms.items()}
    t_mean = mean(best["treatment"])
    if t_mean is None:
        return row | {"probe": "checks_still_threw",
                      "n_checks": len(best["treatment"])}
    deltas = per_check_echo_delta(arms)
    leaking = sorted(k for k, d in deltas.items() if d is not None and d > 0)
    return row | {
        "probe": "probed",
        "n_checks": len(best["treatment"]),
        "n_scored": sum(1 for v in best["treatment"].values() if v is not None),
        "n_threw": sum(1 for v in best["treatment"].values() if v is None),
        "treatment_mean": t_mean,
        "nonsense_mean": mean(best["nonsense"]),
        "filler_mean": mean(best["filler"]),
        "foreign_mean": mean(best["foreign"]),
        # Worst single check, NOT a mean over checks. Averaging signed deltas lets
        # a check the controls win cancel a real leak on another check — the same
        # erasure per_check_echo_delta() prevents across shapes, reintroduced one
        # level up. It also sorted genuine leakers to the bottom of the table.
        "max_check_delta": round(max(deltas[k] for k in leaking), 3)
        if leaking else None,
        "n_leaking_checks": len(leaking),
        "treatment": best["treatment"],
        "nonsense": best["nonsense"],
        "filler": best["filler"],
        "foreign": best["foreign"],
        "per_check_delta": deltas,
        # Credits content carrying no words at all. Once the key scrape stopped
        # injecting ground-truth strings, the keys left are field names a real
        # answer would plausibly carry — so this is the BENCHMARK's structural
        # floor, not an artifact of this tool. Kept as the strictest control.
        "credits_nonsense_checks": sorted(k for k, v in best["nonsense"].items()
                                          if v is not None and v > 0),
        # Probe-integrity alarm proper: meaningless content should never beat
        # meaningless prose. If it does, the KEY NAMES are carrying meaning and
        # the scrape is injecting answers again. Claimed only when BOTH arms
        # produced a verdict — an absent filler score is not a zero one.
        "probe_artifact_checks": sorted(
            k for k, v in best["nonsense"].items()
            if v is not None and v > 0
            and best["filler"].get(k) is not None and v > best["filler"][k]
        ),
        # survives all three controls on some shape: this prompt's own vocabulary
        "echo_leaking_checks": leaking,
        # scores, but no better than a control: credit any answer of this kind earns
        "structural_freebie_checks": sorted(
            k for k, d in deltas.items()
            if d is not None and d <= 0 and scored_above_zero(best["filler"], k)
        ),
        "domain_prose_checks": sorted(
            k for k, d in deltas.items()
            if d is not None and d <= 0 and not scored_above_zero(best["filler"], k)
            and scored_above_zero(best["foreign"], k)
        ),
        # treatment scored but no control ever ran: attribution unknown, not zero
        "control_inconclusive_checks": sorted(
            k for k, d in deltas.items()
            if d is None and best["treatment"].get(k) is not None
            and best["treatment"][k] > 0
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


def task_type(task_dir: Path) -> str:
    """The task's declared type, which is what determines its checks."""
    toml = task_dir / "task.toml"
    if not toml.exists():
        return ""
    try:
        with open(toml, "rb") as fh:
            return str(tomllib.load(fh).get("task", {}).get("task_type", ""))
    except (tomllib.TOMLDecodeError, OSError):
        return ""


def foreign_prompt(task_dir: Path, pool: list[Path]) -> tuple[str, str] | None:
    """A real prompt from a different task of the SAME TASK TYPE.

    The domain-prose control: English, same register, written against the same
    check family — but not this task. Task type, not suite: suites here are
    workflow clusters spanning several types (customer_escalation holds
    error_provenance AND support_code_mapping AND calibration), while the checks
    are type-specific. Controlling an error_provenance task against a calibration
    prompt measures almost nothing and inflates the delta.

    Picks the NEAREST sibling by name rather than a fixed rotation: sorting the
    pool by path makes same-type tasks contiguous, so a rotation lands on the
    alphabetically-first sibling for every task in the group and collapses the
    control onto one prompt. Returns (text, id) so the pick is auditable — the
    delta is sensitive to it, and a control nobody can see is a number nobody can
    check.
    """
    my_type = task_type(task_dir)
    peers = [d for d in pool if d != task_dir and my_type and task_type(d) == my_type]
    if not peers:
        peers = [d for d in pool if d != task_dir and d.parent == task_dir.parent]
    for cand in sorted(peers, key=lambda d: (abs(len(d.name) - len(task_dir.name)), str(d))):
        text = prompt_text(cand)
        if text:
            return text, str(cand.relative_to(gate.BENCH))
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
        picked = foreign_prompt(task_dir, pool)
        if picked is None:
            return {"task": r["task"], "suite": r["task"].split("/")[0],
                    "probe": "no_foreign_control"}
        text, fid = picked
        return measure_json_echo(task_dir, text, fid) | {
            "quarantined": r["quarantined"], "md_verdict": r["verdict"]}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        out = list(ex.map(run, targets))
    # None sorts last: an unknown delta is not a zero one.
    out.sort(key=lambda r: (r.get("max_check_delta") is None,
                            -(r.get("max_check_delta") or 0.0)))

    if as_json:
        print(json.dumps(out, indent=2))
        return 0

    print(f"{'task':<48} {'treat':>6} {'zzz':>5} {'fill':>5} {'frgn':>5} "
          f"{'delta':>6} {'ran':>5}  status")
    print("-" * 108)
    for r in out:
        if r["probe"] != "probed":
            print(f"{r['task']:<48} {'—':>6} {'—':>5} {'—':>5} {'—':>5} "
                  f"{'—':>6} {'—':>5}  {r['probe']}")
            continue
        status = ("ECHO-LEAK" if r["echo_leaking_checks"] else "no echo credit")
        if r["credits_nonsense_checks"]:
            status += " +credits-garbage"
        if r["probe_artifact_checks"]:
            status += " !PROBE-ARTIFACT"
        if r["control_inconclusive_checks"]:
            status += " (+inconclusive)"
        # "—" not "None": no leaking check is a measured absence, not an unknown.
        delta = "—" if r["max_check_delta"] is None else r["max_check_delta"]
        print(f"{r['task']:<48} {r['treatment_mean']:>6} "
              f"{str(r['nonsense_mean']):>5} {str(r['filler_mean']):>5} "
              f"{str(r['foreign_mean']):>5} {str(delta):>6} "
              f"{r['n_scored']}/{r['n_checks']:<3}  {status}")

    probed = [r for r in out if r["probe"] == "probed"]
    leaking = [r for r in probed if r["echo_leaking_checks"]]
    artifact = [r for r in probed if r["probe_artifact_checks"]]
    garbage = [r for r in probed if r["credits_nonsense_checks"]]
    print("-" * 108)
    print(f"cohort (>=1 check the md vector never exercised) : {len(targets)}")
    print(f"probed                                          : {len(probed)}")
    for reason in ("checks_still_threw", "no_instruction_md", "no_foreign_control"):
        n = len([r for r in out if r["probe"] == reason])
        if n:
            print(f"  unprobeable ({reason}): {n}")
    partial = [r for r in probed if r["n_threw"]]
    if partial:
        print(f"  NB {len(partial)} rows score over a SUBSET of their checks "
              f"(ran column); their means are over what ran, not what is graded")

    # What each arm scores, uncontrolled. NOT a count of invariant violations:
    # the invariant's stated property is scoped to ITS vector (cp instruction.md),
    # and this treatment is a synthesized JSON scaffold it never made a claim
    # about. Reported as a baseline ladder so the controls are visible, not as a
    # verdict.
    print("\nscores >0 on at least one check, by arm (uncontrolled):")
    for arm in ("nonsense", "filler", "foreign", "treatment"):
        n = len([r for r in probed
                 if any(v is not None and v > 0 for v in r[arm].values())])
        print(f"  {arm:<10} {n:>3} / {len(probed)}")
    print("  (nonsense ≈ filler is the healthy reading: both are meaningless, so"
          " credit\n   they share is the benchmark's structural floor. nonsense"
          " ABOVE filler would\n   mean the key names carry meaning — i.e. this"
          " tool injecting answers.)")

    print(f"\nECHO-LEAK — credit surviving ALL THREE controls : {len(leaking)}")
    for suite, n in Counter(r["suite"] for r in leaking).most_common():
        print(f"  {suite:<28} {n}")
    freebie = [r for r in probed if r["structural_freebie_checks"]]
    prose = [r for r in probed if r["domain_prose_checks"]]
    incon = [r for r in probed if r["control_inconclusive_checks"]]
    print(f"CREDITS GARBAGE — literal \"zzz\" scores            : {len(garbage)}")
    print(f"structural freebie  (Latin filler also earns it) : {len(freebie)}")
    print(f"domain-prose credit (a real foreign prompt earns it, filler does not) "
          f": {len(prose)}")
    if artifact:
        print(f"!! PROBE ARTIFACT (nonsense BEAT filler — the key scrape is "
              f"injecting answers; these rows are this tool's fault, discount "
              f"them) : {len(artifact)}")
    if incon:
        print(f"control-inconclusive (unknown, NOT zero)        : {len(incon)}")
    deltas = [r["max_check_delta"] for r in leaking
              if r["max_check_delta"] is not None]
    if deltas:
        print(f"\nworst single check per task, over the {len(deltas)} leakers: "
              f"median {sorted(deltas)[len(deltas)//2]}  max {max(deltas)}")
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
    # Selected on n_threw, NOT on the verdict label or the quarantine flag —
    # classify() lets `leak` win, hiding a task that leaks on one check while five
    # throw, and a quarantine entry records one known leak while saying nothing
    # about whether the task's other checks were ever exercised. Both filters read
    # as correct today only because leak==0 and the quarantine file is empty.
    uncovered = [r for r in rows if r["n_threw"] > 0]
    print(f"\nCERTIFIED-BUT-UNEXERCISED (unquarantined, vacuous or partial): "
          f"{len(uncovered)}")
    for suite, n in Counter(r["suite"] for r in uncovered).most_common():
        print(f"  {suite:<28} {n}")
    by_kind = Counter(r["deliverable_kind"] for r in uncovered)
    print("  by deliverable kind:", dict(sorted(by_kind.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
