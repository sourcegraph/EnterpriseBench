#!/usr/bin/env python3
"""Executable gate analyzer for the rryas curated-dataset selection (rryas.8).

Runs the three *executable* selection gates from
docs/internal/rryas_curated_dataset_handoff.md over the CRNT candidate pool
(curated_candidate_pool.py) and reports a gated shortlist. Structural gate 1
(retrieval necessity) is the pool itself; gate 5 (diversity) is a soft, human
step scored by task_mix_validator.py. This tool covers the reproducible middle:

  Gate 2  mcp_only-answerable   ground_truth.required/sufficient files are all
                                plausibly-indexed source (no build outputs,
                                generated files, vendored trees). Structural
                                heuristic: SURFACES suspects, does not judge.
  Gate 3  prompt-echo resistant the exact vjrbw attack, run for real:
                                `cp instruction.md <deliverable>` must score 0
                                on EVERY check. Any check > 0 = FAIL.
                                JSON deliverables additionally get the
                                STRUCTURED vector (jn73.2.7.3.1.4): a
                                schema-shape-valid JSON whose every string
                                value is the prompt must also score 0 — the
                                raw-copy vector alone is blind to them
                                (json.load throws, every check returns None,
                                the task passes vacuously).
  Gate 4  deterministic-consistent
                                the shipped expected_solution.json, dropped in
                                as the deliverable, passes every check
                                (internal consistency, per the dep-traversal
                                template test).

Gate 3/4 materialize a temp WORKSPACE, drop the candidate text into each
deliverable path the checks reference, and run every check exactly as the
harness does (WORKSPACE + TASK_DIR env). No Docker, no accounts, no network.

Usage:
  python3 scripts/validation/curated_gate_analyzer.py            # table
  python3 scripts/validation/curated_gate_analyzer.py --json     # machine
  python3 scripts/validation/curated_gate_analyzer.py --shortlist  # passing only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BENCH = REPO_ROOT / "benchmarks"
POOL_TOOL = REPO_ROOT / "scripts" / "validation" / "curated_candidate_pool.py"

CHECK_TIMEOUT = 45

# $WORKSPACE/foo  or  ${WORKSPACE:-/workspace}/foo  ->  captures "foo"
DELIV_RE = re.compile(r"\$\{?WORKSPACE(?::-[^}]*)?\}?/([^\"'\s\)`]+)")

# Known partial/full-echo tasks still open under jn73.2.7.3 (handoff gate 3).
KNOWN_ECHO_EXCLUDE = {
    "rbac-audit-001",
    "camel-routing-arch-001",
    "ceph-rgw-auth-secure-001",
}

# Gate 2: paths under these segments are not source a Sourcegraph mirror serves.
# NB: a `build`/`vendor` segment inside a `src/` tree (e.g. Next.js
# src/build/webpack-config.ts, or a java .../jpa/vendor/ package) is real
# source; only flag these segments when the path is NOT under a src/ tree.
# Committed lockfiles (yarn.lock, go.sum, Cargo.lock) ARE indexed, so no
# .lock suffix flag.
NON_SOURCE_SEGMENTS = {
    "node_modules", "dist", "build", "target", "out",
    "__pycache__", ".git", "generated", "bin", "obj",
}
NON_SOURCE_SUFFIXES = {
    ".log", ".bin", ".so", ".o", ".a", ".class", ".jar",
    ".whl", ".tar", ".gz", ".zip", ".pyc",
}


@dataclass
class GateResult:
    task_id: str
    suite: str
    stratum: str
    task_type: str
    repos: int
    deliverables: list[str] = field(default_factory=list)
    n_checks: int = 0
    # gate 2
    gate2_suspects: list[str] = field(default_factory=list)
    # gate 3 (echo attack) — per-check score on cp instruction.md
    gate3_echo_scores: dict = field(default_factory=dict)
    # gate 3, structured vector — per-variant per-check score on the
    # schema-shape-valid prompt-filled JSON (JSON deliverables only)
    gate3_structured_scores: dict = field(default_factory=dict)
    gate3_pass: bool | None = None
    gate3_note: str = ""
    # gate 4 (expected_solution consistency)
    gate4_exp_scores: dict = field(default_factory=dict)
    gate4_pass: bool | None = None
    gate4_note: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def all_pass(self) -> bool:
        return (
            not self.gate2_suspects
            and self.gate3_pass is True
            and self.gate4_pass is True
        )


def discover_report_tasks(root: Path = BENCH) -> list[Path]:
    """All active tasks that grade a deliverable via checks/*.sh.

    Walks benchmarks/ (excluding _archived and mined), returning task dirs that
    have at least one check and at least one extractable deliverable path — the
    universe the prompt-echo invariant must hold over.
    """
    out: list[Path] = []
    for toml in sorted(root.rglob("task.toml")):
        d = toml.parent
        parts = set(d.relative_to(root).parts)
        if parts & {"_archived", "mined"}:
            continue
        if not (d / "checks").is_dir():
            continue
        if not list((d / "checks").glob("*.sh")):
            continue
        if deliverable_paths(d):
            out.append(d)
    return out


def echo_scores(task_dir: Path) -> dict:
    """Run every check against `cp instruction.md -> deliverable(s)`.

    Returns {check_name: score|None}. None means the check produced no scored
    verdict for this deliverable (e.g. a JSON check fed non-JSON echo text) —
    the md-grep echo vector does not exercise it.
    """
    deliverables = deliverable_paths(task_dir)
    instr = task_dir / "instruction.md"
    if not deliverables or not instr.exists():
        return {}
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        ws = Path(td)
        materialize(ws, deliverables, instr.read_text(errors="replace"))
        return run_checks(task_dir, ws)


def echo_leak(task_dir: Path) -> dict:
    """The leaking checks (score > 0) under the md-grep prompt echo. Empty = clean."""
    return {k: v for k, v in echo_scores(task_dir).items() if v is not None and v > 0}


# --- Structured echo vector (jn73.2.7.3.1.4) ---------------------------------
#
# The md-grep vector above is blind to JSON deliverables: a raw instruction.md
# copy is not valid JSON, json.load throws inside the check, and every check
# returns None — the task is certified clean without its grading logic ever
# running on prompt content. The structured vector closes that: a deliverable
# that parses, exposes the keys the checks traverse, and whose every string
# value is the prompt. A check that credits it grades prompt vocabulary.

# Keys a check reads out of the parsed deliverable: answer.get('x') /
# report.get("x") / answer['x']. GT-mirroring alone misses answer-side aliases
# ('files' where ground truth says 'required_files'), so these are grafted in.
ANSWER_KEY_RE = re.compile(
    r"""(?:answer|report)(?:\.get\(|\[)\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]"""
)


def _echofy(node, prompt: str):
    """Mirror a JSON shape; every leaf is replaced so ONLY prompt text remains.

    Strings -> the prompt. Numbers -> 0, booleans -> False (ground-truth values
    must not ride along: credit for them would be answer-key credit, not the
    prompt echo this vector measures). Keys and nesting survive so the checks'
    field traversal actually runs.
    """
    if isinstance(node, dict):
        return {k: _echofy(v, prompt) for k, v in node.items()}
    if isinstance(node, list):
        return [_echofy(v, prompt) for v in node]
    if isinstance(node, str):
        return prompt
    if isinstance(node, bool):
        return False
    if isinstance(node, (int, float)):
        return 0
    return None


def _check_answer_keys(task_dir: Path) -> list[str]:
    keys: set[str] = set()
    for sh in sorted((task_dir / "checks").glob("*.sh")):
        keys.update(ANSWER_KEY_RE.findall(sh.read_text(errors="replace")))
    return sorted(keys)


def synthesize_structured_echo(task_dir: Path, prompt: str) -> str:
    """A schema-shape-valid JSON deliverable whose only content is the prompt.

    Shape source is ground_truth.json (what the checks grade against), plus a
    [prompt] list grafted under every answer key the task's own checks read.
    """
    shape: dict = {}
    gt_path = task_dir / "ground_truth.json"
    if gt_path.exists():
        try:
            parsed = json.loads(gt_path.read_text(errors="replace"))
            if isinstance(parsed, dict):
                shape = parsed
        except (json.JSONDecodeError, UnicodeDecodeError):
            shape = {}
    echo = _echofy(shape, prompt)
    for key in _check_answer_keys(task_dir):
        if key not in echo:
            echo[key] = [prompt]
    return json.dumps(echo, indent=2)


def synthesize_structured_list_echo(task_dir: Path, prompt: str) -> str:
    """The top-level-ARRAY twin of synthesize_structured_echo.

    Some deliverable schemas are a bare list of records (dead_code_report.json:
    [{"file":..,"symbol":..}, ...]); an object payload crashes those checks and
    they stay dark. Entry shape is mirrored from ground_truth.json's
    list-of-dict values, values prompt-only as in _echofy.
    """
    shape: dict = {}
    gt_path = task_dir / "ground_truth.json"
    if gt_path.exists():
        try:
            parsed = json.loads(gt_path.read_text(errors="replace"))
            if isinstance(parsed, dict):
                shape = parsed
        except (json.JSONDecodeError, UnicodeDecodeError):
            shape = {}
    entries = [
        _echofy(item, prompt)
        for value in shape.values()
        if isinstance(value, list)
        for item in value
        if isinstance(item, dict)
    ]
    return json.dumps(entries or [{"text": prompt}], indent=2)


def structured_echo_scores(task_dir: Path) -> dict:
    """Run every check against the structured echo, per prompt variant.

    Returns {instruction_variant: {check_name: score|None}}. Each
    instruction*.md the task ships is a separate echo vector (an mcp variant
    can leak worse than the baseline one — ceph-rgw-auth-secure-001 did).
    Non-JSON deliverables get the raw prompt so every check runs against a
    coherent all-prompt workspace; JSON deliverables get the structured echo.

    Both payload shapes (object mirror, record list) run, merged per check by
    max-of-scored: a real adversary submits whichever shape the schema demands,
    so a check is clean only if NO shape it can parse earns credit, and it is
    evaluated as long as at least one shape reaches its grading logic.
    """
    deliverables = deliverable_paths(task_dir)
    json_delivs = [d for d in deliverables if d.endswith(".json")]
    if not json_delivs:
        return {}
    out: dict[str, dict] = {}
    for instr in sorted(task_dir.glob("instruction*.md")):
        prompt = instr.read_text(errors="replace")
        merged: dict[str, float | None] = {}
        for payload in (
            synthesize_structured_echo(task_dir, prompt),
            synthesize_structured_list_echo(task_dir, prompt),
        ):
            with tempfile.TemporaryDirectory() as td:
                ws = Path(td)
                materialize(ws, deliverables, prompt)
                materialize(ws, json_delivs, payload)
                scores = run_checks(task_dir, ws)
            for check, score in scores.items():
                prev = merged.get(check)
                if prev is None:
                    merged[check] = score
                elif score is not None:
                    merged[check] = max(prev, score)
        out[instr.name] = merged
    return out


def structured_leaks(scores_by_variant: dict) -> dict:
    """Checks crediting the structured echo, as {check: {variant: score}}."""
    leak: dict[str, dict] = {}
    for variant, per_check in scores_by_variant.items():
        for check, score in per_check.items():
            if score is not None and score > 0:
                leak.setdefault(check, {})[variant] = score
    return leak


def structured_echo_leak(task_dir: Path) -> dict:
    """Leaking checks under the structured echo vector. Empty = clean."""
    return structured_leaks(structured_echo_scores(task_dir))


def load_pool() -> list[dict]:
    out = subprocess.run(
        [sys.executable, str(POOL_TOOL), "--json"],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def deliverable_paths(task_dir: Path) -> list[str]:
    paths: set[str] = set()
    for sh in sorted((task_dir / "checks").glob("*.sh")):
        for m in DELIV_RE.findall(sh.read_text(errors="replace")):
            # trim trailing punctuation the shell may append
            p = m.rstrip('"').rstrip("'")
            # skip the ground_truth / task-internal references
            if p.split("/")[-1] in {"ground_truth.json"}:
                continue
            # a deliverable is a file: it must have an extension. Drops bare
            # directory tokens (e.g. "$WORKSPACE/flask") that are repo roots,
            # not the artifact the check grades.
            if not Path(p).suffix:
                continue
            paths.add(p)
    # drop any path that is a parent prefix of another (a repo-dir token that
    # slipped through as e.g. "flask/x" vs "flask/x/y")
    return sorted(paths)


def run_checks(task_dir: Path, workspace: Path) -> dict:
    scores: dict[str, float | None] = {}
    env = os.environ.copy()
    env["WORKSPACE"] = str(workspace)
    env["TASK_DIR"] = str(task_dir)
    for sh in sorted((task_dir / "checks").glob("*.sh")):
        try:
            p = subprocess.run(
                ["bash", str(sh)], capture_output=True, text=True,
                timeout=CHECK_TIMEOUT, env=env,
            )
            out = p.stdout.strip()
            scores[sh.name] = json.loads(out).get("score") if out else None
        except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError):
            scores[sh.name] = None
    return scores


def materialize(workspace: Path, deliverables: list[str], content: str) -> None:
    for rp in deliverables:
        f = workspace / rp
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)


def gate2_suspects(task_dir: Path) -> list[str]:
    toml = task_dir / "task.toml"
    if not toml.exists():
        return []
    import tomllib
    data = tomllib.load(open(toml, "rb"))
    gt = data.get("ground_truth", {}) or {}
    suspects: list[str] = []
    for key in ("required_files", "sufficient_files"):
        for entry in gt.get(key, []) or []:
            path = entry.get("path", "") if isinstance(entry, dict) else str(entry)
            segs = path.split("/")
            suffix = Path(path).suffix.lower()
            # a build/vendor segment under a src/ tree is real source
            under_src = "src" in segs
            seg_hit = bool(set(segs) & NON_SOURCE_SEGMENTS) and not under_src
            if seg_hit or suffix in NON_SOURCE_SUFFIXES:
                suspects.append(path)
    return suspects


def analyze(task: dict) -> GateResult:
    task_dir = BENCH / task["suite"] / task["task_id"]
    r = GateResult(
        task_id=task["task_id"], suite=task["suite"], stratum=task["stratum"],
        task_type=task["task_type"], repos=task["repos"],
    )
    r.deliverables = deliverable_paths(task_dir)
    checks = sorted((task_dir / "checks").glob("*.sh"))
    r.n_checks = len(checks)

    # Gate 2 — structural source-file heuristic
    r.gate2_suspects = gate2_suspects(task_dir)

    # Known-echo exclude short-circuits gate 3
    if task["task_id"] in KNOWN_ECHO_EXCLUDE:
        r.gate3_pass = False
        r.notes.append("KNOWN_ECHO_EXCLUDE (jn73.2.7.3)")

    if not r.deliverables:
        r.notes.append("no deliverable path extracted from checks")
        # cannot run the file-based echo/consistency experiment
        r.gate3_pass = r.gate3_pass if r.gate3_pass is not None else None
        return r

    instr = task_dir / "instruction.md"
    exp = task_dir / "expected_solution.json"

    # Gate 3 — echo attack, two vectors. The md-grep vector
    # (cp instruction.md -> deliverable) exercises text deliverables; for JSON
    # deliverables that copy cannot be parsed, so the structured vector
    # (synthesize_structured_echo: schema-shape-valid, prompt-only values)
    # runs as well and decides. Only a task neither vector can evaluate is
    # INCONCLUSIVE.
    json_deliv = any(d.endswith(".json") for d in r.deliverables)
    if instr.exists() and r.gate3_pass is None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            materialize(ws, r.deliverables, instr.read_text(errors="replace"))
            r.gate3_echo_scores = run_checks(task_dir, ws)
        vals = [v for v in r.gate3_echo_scores.values() if v is not None]
        if not vals:
            r.gate3_pass = None
            r.gate3_note = (
                "structured deliverable — md-grep echo vector N/A"
                if json_deliv else "checks did not evaluate"
            )
        elif all(v == 0.0 for v in vals):
            r.gate3_pass = True
        else:
            r.gate3_pass = False
            leaks = {k: v for k, v in r.gate3_echo_scores.items() if v and v > 0}
            r.gate3_note = f"echo credited: {leaks}"

    # Gate 3, structured vector — JSON deliverables only. A leak here fails
    # the gate even if the md vector passed; an all-zero structured run
    # upgrades an inconclusive md verdict to PASS.
    if instr.exists() and json_deliv:
        r.gate3_structured_scores = structured_echo_scores(task_dir)
        s_leak = structured_leaks(r.gate3_structured_scores)
        s_vals = [
            v
            for per_check in r.gate3_structured_scores.values()
            for v in per_check.values()
            if v is not None
        ]
        if s_leak:
            r.gate3_pass = False
            sep = "; " if r.gate3_note else ""
            r.gate3_note = f"{r.gate3_note}{sep}structured echo credited: {s_leak}"
        elif s_vals and r.gate3_pass is None:
            r.gate3_pass = True
            r.gate3_note = "structured echo vector: all checks 0"

    # Gate 4 — expected_solution consistency
    if not exp.exists():
        r.gate4_pass = None
        r.gate4_note = "no expected_solution.json"
    else:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            materialize(ws, r.deliverables, exp.read_text(errors="replace"))
            r.gate4_exp_scores = run_checks(task_dir, ws)
        vals = [v for v in r.gate4_exp_scores.values() if v is not None]
        json_deliv = any(d.endswith(".json") for d in r.deliverables)
        if vals and all(v is not None and v >= 1.0 for v in vals):
            r.gate4_pass = True
        elif json_deliv and not (vals and all(v >= 1.0 for v in vals)):
            # raw expected_solution.json rarely conforms to a structured
            # JSON deliverable schema; needs structured materialization.
            r.gate4_pass = None
            r.gate4_note = "json deliverable — needs structured materialization"
        else:
            r.gate4_pass = False
            r.gate4_note = "expected_solution did not pass every check"
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--shortlist", action="store_true",
                    help="print only tasks passing gates 2+3+4")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--rescan-quarantine", action="store_true",
                    help="rewrite tests/integrity/known_prompt_echo_leaks.json "
                         "from a fresh echo scan of all active report tasks")
    ap.add_argument("--rescan-structured-quarantine", action="store_true",
                    help="rewrite tests/integrity/known_structured_echo_leaks"
                         ".json from a fresh structured-echo scan of all "
                         "active JSON-deliverable tasks")
    args = ap.parse_args()

    if args.rescan_quarantine:
        leakers = sorted(
            str(d.relative_to(BENCH))
            for d in discover_report_tasks()
            if echo_leak(d)
        )
        out = REPO_ROOT / "tests" / "integrity" / "known_prompt_echo_leaks.json"
        out.write_text(json.dumps(leakers, indent=2) + "\n")
        print(f"wrote {out.relative_to(REPO_ROOT)}: {len(leakers)} leaking tasks")
        return 0

    if args.rescan_structured_quarantine:
        leakers = sorted(
            str(d.relative_to(BENCH))
            for d in discover_report_tasks()
            if structured_echo_leak(d)
        )
        out = (REPO_ROOT / "tests" / "integrity"
               / "known_structured_echo_leaks.json")
        out.write_text(json.dumps(leakers, indent=2) + "\n")
        print(f"wrote {out.relative_to(REPO_ROOT)}: {len(leakers)} leaking tasks")
        return 0

    pool = load_pool()
    if args.limit:
        pool = pool[: args.limit]
    results = [analyze(t) for t in pool]

    if args.json:
        print(json.dumps([asdict(r) | {"all_pass": r.all_pass} for r in results], indent=2))
        return 0

    def sym(v):
        return {True: "PASS", False: "FAIL", None: "—"}[v]

    rows = [r for r in results if (not args.shortlist or r.all_pass)]
    print(f"{'task_id':<48} {'g2':>4} {'g3':>5} {'g4':>5}  {'type':<22} {'stratum'}")
    print("-" * 100)
    for r in sorted(rows, key=lambda x: (not x.all_pass, x.suite, x.task_id)):
        g2 = "PASS" if not r.gate2_suspects else f"?{len(r.gate2_suspects)}"
        print(f"{r.task_id:<48} {g2:>4} {sym(r.gate3_pass):>5} {sym(r.gate4_pass):>5}"
              f"  {r.task_type:<22} {r.stratum}")

    # summary
    n = len(results)
    g3f = [r for r in results if r.gate3_pass is False]
    g4f = [r for r in results if r.gate4_pass is False]
    g4n = [r for r in results if r.gate4_pass is None]
    g2s = [r for r in results if r.gate2_suspects]
    nodel = [r for r in results if not r.deliverables]
    allp = [r for r in results if r.all_pass]
    print("-" * 100)
    print(f"candidates scanned : {n}")
    print(f"gate2 suspects     : {len(g2s)}")
    print(f"gate3 echo FAIL    : {len(g3f)}   {[r.task_id for r in g3f][:12]}")
    print(f"gate4 FAIL         : {len(g4f)}   {[r.task_id for r in g4f][:12]}")
    print(f"gate4 inconclusive : {len(g4n)}  (json deliverable / no expected_solution)")
    print(f"no deliverable path: {len(nodel)}  {[r.task_id for r in nodel][:12]}")
    print(f"PASS all 2+3+4     : {len(allp)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
