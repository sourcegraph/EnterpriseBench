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

--json-echo then answers the follow-on question: for the cohort the md vector
never exercised, what does an HONEST vector score? It marshals the prompt's own
text into the answer shape (the prompt's ```json fence when it publishes one,
otherwise the shape its checks read) and scores every check — the treatment.
It reruns the identical shape filled with prompt-free filler — the control.
Only treatment-minus-control is credit attributable to prompt VOCABULARY; credit
the filler also earns is a structural freebie (the check credits any well-formed
answer), a real defect but a different one.

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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import curated_gate_analyzer as gate  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
QUARANTINE_FILE = REPO_ROOT / "tests" / "integrity" / "known_prompt_echo_leaks.json"

# ```json ... ``` fence in instruction.md — the answer shape the prompt publishes.
JSON_FENCE_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)

# `answer.get('key'` / `answer.get("key"` inside a check — the keys it reads.
ANSWER_GET_RE = re.compile(r"answer\.get\(\s*['\"]([^'\"]+)['\"]")

# Leaf field names checks pull out of list-of-dict answers (f.get('path'), ...).
DICT_LEAF_FIELDS = ("path", "file", "name", "description", "reason", "detail",
                    "symbol")

# Control filler: same JSON shape, same bulk, zero prompt vocabulary. Credit that
# survives this is structural (any well-formed answer earns it), not echo.
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


def answer_template(task_dir: Path) -> object | None:
    """The answer shape the prompt itself publishes, as a parsed template.

    Returns None when instruction.md ships no parseable ```json fence — such a
    task cannot be probed with a schema-faithful echo by this tool.
    """
    instr = task_dir / "instruction.md"
    if not instr.exists():
        return None
    for block in JSON_FENCE_RE.findall(instr.read_text(errors="replace")):
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


def keys_from_checks(task_dir: Path) -> list[str]:
    """Answer keys the checks read (`answer.get('x', answer.get('y', ...))`).

    Most report prompts publish no ```json fence, yet their checks still
    json.load the deliverable. The shape is then discoverable only from the
    checks — so derive it there rather than leave the task unmeasured.
    """
    keys: set[str] = set()
    for sh in sorted((task_dir / "checks").glob("*.sh")):
        keys.update(ANSWER_GET_RE.findall(sh.read_text(errors="replace")))
    return sorted(keys)


def candidate_templates(task_dir: Path) -> list[object]:
    """Every answer shape worth trying, richest evidence first.

    The prompt's own fence when it has one, plus check-derived shape variants
    (scalar / list-of-string / list-of-dict) because a check's expected type is
    not knowable statically. Content is ALWAYS only the filled text; the shape
    is inert scaffolding, so trying several keeps this a measure of what prompt
    VOCABULARY can buy rather than of shape-guessing luck.
    """
    out: list[object] = []
    fence = answer_template(task_dir)
    if fence is not None:
        out.append(fence)
    keys = keys_from_checks(task_dir)
    if keys:
        out.append({k: "" for k in keys})
        out.append({k: [""] for k in keys})
        out.append({k: [{f: "" for f in DICT_LEAF_FIELDS}] for k in keys})
    # Bare top-level list (`claimed = json.load(f)` then e["file"]/e["symbol"]) —
    # the heredoc idiom the answer.get() scan cannot see. Always worth trying:
    # a shape that fits nothing simply throws and contributes no score.
    out.append([{f: "" for f in DICT_LEAF_FIELDS}])
    return out


def probe(task_dir: Path, text: str) -> dict:
    """Best score each check reaches under any candidate shape filled with `text`.

    Per-check max across shapes: a shape that makes one check throw must not
    mask another check that the same text would have credited.
    """
    deliverables = gate.deliverable_paths(task_dir)
    templates = candidate_templates(task_dir)
    if not deliverables or not templates:
        return {}
    best: dict[str, float | None] = {}
    for tpl in templates:
        body = json.dumps(fill(tpl, text))
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            gate.materialize(ws, deliverables, body)
            scores = gate.run_checks(task_dir, ws)
        for k, v in scores.items():
            if v is None:
                best.setdefault(k, None)
            elif best.get(k) is None or v > best[k]:
                best[k] = v
    return best


def mean(scores: dict) -> float | None:
    vals = [v for v in scores.values() if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def measure_json_echo(task_dir: Path) -> dict:
    """Treatment (prompt text) vs control (filler) in the prompt's own JSON shape.

    The control is what makes this honest: a check that credits BOTH is a
    structural freebie (it would credit any well-formed answer), which is a
    different defect from an echo leak. Only the treatment-minus-control delta
    is credit attributable to prompt vocabulary.
    """
    rel = str(task_dir.relative_to(gate.BENCH))
    row: dict = {"task": rel, "suite": rel.split("/")[0]}
    if not candidate_templates(task_dir):
        return row | {"probe": "no_shape_derivable"}
    treat, ctrl = probe(task_dir, prompt_text(task_dir)), probe(task_dir, FILLER)
    t_mean, c_mean = mean(treat), mean(ctrl)
    if t_mean is None:
        return row | {"probe": "checks_still_threw"}
    delta = round(t_mean - (c_mean or 0.0), 3)
    return row | {
        "probe": "probed",
        "treatment_mean": t_mean,
        "control_mean": c_mean,
        "echo_delta": delta,
        "treatment": treat,
        "control": ctrl,
        # a check the prompt alone drives to >0 that filler cannot: a real leak
        "echo_leaking_checks": sorted(
            k for k, v in treat.items()
            if v is not None and v > 0 and not (ctrl.get(k) or 0) >= v
        ),
        "structural_freebie_checks": sorted(
            k for k, v in treat.items()
            if v is not None and v > 0 and (ctrl.get(k) or 0) >= v
        ),
    }


def prompt_text(task_dir: Path) -> str:
    return (task_dir / "instruction.md").read_text(errors="replace")


def run_json_echo(rows: list[dict], workers: int, as_json: bool) -> int:
    """Probe the cohort the md vector never exercised."""
    targets = [r for r in rows if r["verdict"] in ("vacuous", "partial")
               and not r["quarantined"]]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        out = list(ex.map(lambda r: measure_json_echo(gate.BENCH / r["task"]), targets))
    out.sort(key=lambda r: -(r.get("echo_delta") or 0))

    if as_json:
        print(json.dumps(out, indent=2))
        return 0

    print(f"{'task':<58} {'treat':>6} {'ctrl':>6} {'delta':>6}  status")
    print("-" * 96)
    for r in out:
        if r["probe"] != "probed":
            print(f"{r['task']:<58} {'—':>6} {'—':>6} {'—':>6}  {r['probe']}")
            continue
        print(f"{r['task']:<58} {r['treatment_mean']:>6} "
              f"{str(r['control_mean']):>6} {r['echo_delta']:>6}  "
              f"{'ECHO-LEAK' if r['echo_leaking_checks'] else 'no echo credit'}")

    probed = [r for r in out if r["probe"] == "probed"]
    leaking = [r for r in probed if r["echo_leaking_checks"]]
    print("-" * 96)
    print(f"cohort (unexercised by md vector) : {len(targets)}")
    print(f"probed (prompt text, JSON-shaped) : {len(probed)}")
    for reason in ("no_shape_derivable", "checks_still_threw"):
        n = len([r for r in out if r["probe"] == reason])
        if n:
            print(f"  unprobeable ({reason}): {n}")
    print(f"\nECHO-LEAKING under a JSON-shaped echo : {len(leaking)}")
    by_suite: dict[str, int] = {}
    for r in leaking:
        by_suite[r["suite"]] = by_suite.get(r["suite"], 0) + 1
    for suite, n in sorted(by_suite.items(), key=lambda kv: -kv[1]):
        print(f"  {suite:<28} {n}")
    if probed:
        deltas = [r["echo_delta"] for r in probed]
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
    by_suite: dict[str, int] = {}
    for r in uncovered:
        by_suite[r["suite"]] = by_suite.get(r["suite"], 0) + 1
    for suite, n in sorted(by_suite.items(), key=lambda kv: -kv[1]):
        print(f"  {suite:<28} {n}")
    by_kind: dict[str, int] = {}
    for r in uncovered:
        by_kind[r["deliverable_kind"]] = by_kind.get(r["deliverable_kind"], 0) + 1
    print("  by deliverable kind:", dict(sorted(by_kind.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
