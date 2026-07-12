#!/usr/bin/env python3
"""Deterministic oracle-token leakage classifier — task-authoring Gate B.

Classifies a task's instruction against its ground-truth oracle into a leak
bucket. A task is grep-trivial-by-leak when the instruction hands the agent
the location of the answer (exact path, filename, a distinctive containing
directory, or a distinctive oracle symbol). Only behavioral / grep-hard tasks
are admitted to the enterprise stratum: leaky tasks let local grep localize
in ~1 step and collapse the retrieval-vs-baseline contrast this benchmark
exists to measure.

Structural and deterministic by design (ZFC: no semantic judgment, no LLM
call) so it is an auditable authoring gate that runs in any container.
Stdlib-only plus ``tomllib``/``tomli``.

This is a port of CodeScaleBench's ``scripts/authoring/leak_classifier.py``
(read-only reference; see EnterpriseBench-jn73.3). The core ``classify()``
taxonomy and bucket logic are unchanged from that reference. What changed is
the EB task loader: CSB's shipped "EB adapter" read
``expected_solution.json{"files"}``, a key EB's checkpoint-structured tasks
do not have (`checkpoints.<name>.expected_solution` free text, not a file
list) — it failed to load real EB tasks. This module instead reads
``task.toml [ground_truth].required_files`` + ``.sufficient_files``, mirroring
the adapter pattern in ``lib/eb_verify/qa_adapter.py``
(``_oracle_files`` / ``_oracle_tokens`` / the ``ground_truth.json``
setdefault-merge in ``load_task_inputs``) rather than duplicating a second,
divergent parsing convention.

Policy (Option B, decided 2026-07-10 on EnterpriseBench-jn73.3): Gate B
HONORS the ``[[qa.waivers]]`` ledger. An ``exact_path`` leak ADMITS the task
iff the leaked oracle file's path is a recorded ``qa.waivers`` token for that
task (rationale: the named path is the given investigation entry point, and
the scored discovery target is downstream — see
``docs/qa/eb_f2_triage_2026_06_04.md``); otherwise it REJECTs. ``symbol`` and
``dir`` buckets are never waiver-admissible — only ``exact_path`` is, per the
decided rule. This makes Gate B share exactly one waiver ledger with the F2
runtime score-validity gate (``lib/eb_verify/qa_adapter.py`` on
``fix/eb-f2-leak-triage``, not yet merged to main) instead of building a
second, conflicting leak-admission mechanism.

Usage:
    python3 scripts/validation/leak_classifier.py <task_dir>          # one task
    python3 scripts/validation/leak_classifier.py --corpus benchmarks/  # batch
    python3 scripts/validation/leak_classifier.py --corpus benchmarks/ --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# Path setup ONLY in script mode, where the repo root is not yet importable.
# When imported as `scripts.validation.leak_classifier` (tests, stratum_selector)
# __package__ is set and we touch nothing: mutating sys.path at import time makes
# unrelated tests pass or fail depending on collection order.
if __package__ in (None, ""):  # pragma: no cover - script-mode only
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.lib.tasks import find_task_dirs  # noqa: E402

try:
    import tomllib
except ImportError:  # pragma: no cover - py<3.11
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:  # pragma: no cover
        tomllib = None  # type: ignore[assignment]


class Bucket(str, Enum):
    EXACT_PATH = "exact_path"   # instruction names the file path or basename
    SYMBOL = "symbol"           # instruction names a distinctive oracle symbol
    DIR = "dir"                 # instruction names a distinctive containing dir
    BEHAVIORAL = "behavioral"   # no locational leak -> admit
    GREP_HARD = "grep_hard"     # behavioral + dispersed targets -> admit (preferred)


REJECT = frozenset({Bucket.EXACT_PATH, Bucket.SYMBOL, Bucket.DIR})

# Generic path segments that do not localize anything on their own.
_GENERIC_DIRS = frozenset({
    "src", "lib", "pkg", "internal", "app", "core", "cmd", "test", "tests",
    "main", "api", "server", "client", "common", "util", "utils", "config",
    "include", "source", "build", "dist", "docs", "example", "examples",
})
# Short/common tokens that would false-positive as distinctive "symbols".
_COMMON_WORDS = frozenset({
    "the", "and", "for", "add", "get", "set", "run", "new", "make", "call",
    "data", "file", "code", "type", "name", "value", "error", "result",
    "handler", "service", "manager", "client", "server", "config", "request",
    "response", "process", "update", "create", "delete", "list", "check",
})

# Build-manifest basenames that exist in (almost) every repo of a given
# language. Naming one in a prompt ("look at go.mod") is not a localization
# leak: the basename is non-discriminative, so it tells the agent nothing about
# WHICH file holds the answer. A token *with* directory components (e.g.
# ``envoy/go.mod``) is specific and is still treated as a leak.
#
# Kept in sync with the F2 aux-file leakage checker's list of the same name
# (fix/eb-f2-leak-triage, 76f5469). That branch is unmerged, so the two copies
# cannot yet share one definition -- unify them when it lands.
GENERIC_MANIFEST_BASENAMES = frozenset({
    # Go
    "go.mod", "go.sum",
    # Node / JS-TS
    "package.json", "package-lock.json", "yarn.lock",
    # Python
    "setup.py", "setup.cfg", "requirements.txt", "pyproject.toml",
    "Pipfile", "Pipfile.lock",
    # Rust
    "Cargo.toml", "Cargo.lock",
    # JVM
    "pom.xml", "build.gradle",
    # Bazel
    "BUILD", "BUILD.bazel",
    # Helm -- EB addition beyond the F2 list. Every chart has a values.yaml and
    # a Chart.yaml, so they are non-discriminative by the identical argument.
    "values.yaml", "Chart.yaml",
})


# Public aliases. `stratum_selector` (Gate A/D) needs the same path-normalisation
# and ground-truth-loading logic; a sibling module should not have to reach into
# underscore names to get it. The unified loader (rule of three: qa_adapter,
# leak_classifier, retrieval_extraction) is tracked as EnterpriseBench-jn73.2.8 --
# until then these aliases mark the shared surface explicitly.


@dataclass(frozen=True)
class Leak:
    bucket: Bucket
    token: str        # the leaking token; "" for behavioral / grep_hard
    # EB addition (not in the CSB reference): the full oracle file path (as
    # given in ground_truth) that triggered an EXACT_PATH/DIR bucket. `token`
    # may be only a basename or a partial two-segment fragment of a longer
    # path, which is not enough to match a qa.waivers `token` field (recorded
    # as the *complete* required/sufficient_files path). Gate B's waiver
    # check needs the complete path, so we carry it separately rather than
    # overload `token` and risk breaking bucket-classification parity with
    # the CSB reference's self-test.
    source_path: str = ""

    @property
    def admit(self) -> bool:
        """A leak admits iff its bucket is not a rejecting one. Derived, not
        stored, so the two can never be set out of step."""
        return self.bucket not in REJECT


def _distinctive(token: str) -> bool:
    """A token localizes only if it is code-shaped or long-and-uncommon."""
    t = token.strip()
    if len(t) < 4 or t.lower() in _COMMON_WORDS:
        return False
    has_camel = bool(re.search(r"[a-z][A-Z]", t))   # camelCase / PascalCase
    return has_camel or "_" in t or len(t) >= 8


def strip_repo_slug(path: str) -> str:
    """Drop a leading `repo--hexhash/` wrapper and any `repo::` prefix."""
    p = path.replace("\\", "/")
    p = re.sub(r"^[^/]+--[0-9a-f]{6,}/", "", p)
    return p.split("::")[-1].strip("/")


def _path_parts(path: str) -> tuple[str, list[str], list[str]]:
    """Return (basename, distinctive-dir-segments, all-segments)."""
    segs = [s for s in strip_repo_slug(path).split("/") if s]
    basename = segs[-1] if segs else ""
    dirs = [s for s in segs[:-1] if s.lower() not in _GENERIC_DIRS]
    return basename, dirs, segs


def classify(instruction: str,
             oracle_files: list[str],
             oracle_symbols: list[str] | None = None) -> Leak:
    """Classify one task. Most-specific leak wins; first hit reported."""
    ins = instruction.lower()

    def names(word: str) -> bool:
        # word-boundary match that is not part of a longer path/identifier
        return re.search(r"(?<![\w/.\-])" + re.escape(word.lower()) + r"(?![\w])",
                         ins) is not None

    # The generic-manifest skip is only sound when the manifest is an ENTRY POINT
    # to something else. Its rationale -- "naming go.mod does not tell you WHICH
    # file holds the answer" -- collapses when the manifest IS the answer, as in
    # the dep-traversal tasks whose entire oracle is ["go.mod", "go.sum"]. There,
    # naming the manifest names the oracle exactly, and suppressing it would
    # promote a grep-trivial task into grep_hard (the *preferred* bucket).
    #
    # So suppress generic basenames only if at least one oracle file is NOT a
    # generic manifest -- i.e. only when there is genuinely something downstream
    # left to discover.
    oracle_has_non_generic = any(
        _path_parts(f)[0] not in GENERIC_MANIFEST_BASENAMES for f in oracle_files
    )

    # 1) exact path or basename, or >=2 consecutive path segments verbatim
    for f in oracle_files:
        basename, _dirs, segs = _path_parts(f)
        # A bare generic manifest basename does not localize -- skip it here,
        # but keep checking the multi-segment fragments below, which restore
        # specificity (`envoy/go.mod` still leaks).
        is_generic = (basename in GENERIC_MANIFEST_BASENAMES
                      and oracle_has_non_generic)
        if basename and not is_generic and names(basename):
            return Leak(Bucket.EXACT_PATH, basename, source_path=f)
        for i in range(len(segs) - 1):
            frag = "/".join(segs[i:i + 2]).lower()
            if frag and frag in ins:
                return Leak(Bucket.EXACT_PATH, frag, source_path=f)

    # 2) distinctive oracle symbol named verbatim
    for sym in (oracle_symbols or []):
        if _distinctive(sym) and names(sym):
            return Leak(Bucket.SYMBOL, sym)

    # 3) distinctive containing directory, but only when named in PATH form
    #    (`escalation/`), not as a bare domain noun ("the escalation service").
    #    A bare word collides with the task's own subject; path form does not.
    for f in oracle_files:
        _basename, dirs, _segs = _path_parts(f)
        for d in dirs:
            if _distinctive(d) and (d.lower() + "/") in ins:
                return Leak(Bucket.DIR, d, source_path=f)

    # 4) no locational leak -> admit; grep_hard if targets are dispersed
    repos = {re.split(r"[/:]", f, 1)[0] for f in oracle_files}
    dispersed = len(oracle_files) >= 3 or len(repos) >= 2
    return Leak(Bucket.GREP_HARD if dispersed else Bucket.BEHAVIORAL, "")


# --- EB task loader (task.toml [ground_truth], NOT expected_solution.json) --

def _flatten_gt_paths(ground_truth: dict[str, Any]) -> list[str]:
    """Flatten required_files + sufficient_files paths from EB ground_truth.

    Mirrors ``lib/eb_verify/qa_adapter._oracle_tokens``: full path strings
    (not just basenames), deduplicated, order preserved. Symbol-level tokens
    are intentionally not collected here — EB's ground_truth schema has no
    ``required_symbols`` field, and ``expected_solution.json`` only carries
    free-text judge-prompt prose (concept tokens embedded in sentences), not
    a clean symbol list; regex-extracting "symbols" from that prose would be
    a heuristic meaning-detection step forbidden under ZFC. ``oracle_symbols``
    stays empty for the EB corpus, so the SYMBOL bucket structurally exists
    (parity with the CSB taxonomy) but does not currently fire.
    """
    paths: list[str] = []
    for section in ("required_files", "sufficient_files"):
        for entry in ground_truth.get(section, []) or []:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            if isinstance(path, str) and path:
                paths.append(path)
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def load_ground_truth(task_dir: Path, task_toml: dict[str, Any]) -> dict[str, Any]:
    """Merge task.toml [ground_truth] with ground_truth.json (toml wins).

    Mirrors ``qa_adapter.load_task_inputs``'s merge rule exactly: the
    canonical, schema-validated table in task.toml always wins; a sibling
    ``ground_truth.json`` (task-type-specific keys, and on some older tasks a
    mirror of required_files/sufficient_files) only fills keys the TOML
    table doesn't have via ``setdefault``.
    """
    gt_embedded = task_toml.get("ground_truth")
    ground_truth: dict[str, Any] = dict(gt_embedded) if isinstance(gt_embedded, dict) else {}
    gt_path = task_dir / "ground_truth.json"
    if gt_path.is_file():
        try:
            gt_json = json.loads(gt_path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            gt_json = {}
        if isinstance(gt_json, dict):
            block = gt_json.get("ground_truth", gt_json)
            if isinstance(block, dict):
                for key, value in block.items():
                    ground_truth.setdefault(key, value)
    return ground_truth


def _load_waiver_tokens(task_toml: dict[str, Any]) -> set[str]:
    """Return the set of oracle-path tokens recorded as qa.waivers.

    Any ``decision`` value counts (both "waive" and "accept" mean the finding
    was reviewed and is not a fresh leak) — Gate B only cares whether the
    token was triaged, not which of the two labels was applied.
    """
    qa = task_toml.get("qa")
    if not isinstance(qa, dict):
        return set()
    waivers = qa.get("waivers")
    if not isinstance(waivers, list):
        return set()
    return {
        w["token"] for w in waivers
        if isinstance(w, dict) and isinstance(w.get("token"), str)
    }


@dataclass(frozen=True)
class TaskOracle:
    """Everything a gate needs from one task on disk, read exactly once.

    ``ground_truth`` is the merged toml+json table ``oracle_files`` was
    flattened from. It is carried rather than dropped so a caller that needs the
    structured entries (Gate A wants each entry's ``repo``) does not have to
    re-read the task off disk to get them back.
    """

    instruction: str
    oracle_files: list[str]
    task_toml: dict[str, Any] = field(default_factory=dict)
    ground_truth: dict[str, Any] = field(default_factory=dict)


def load_task(task_dir: Path) -> TaskOracle | None:
    """Load one EB task's instruction + oracle files, or None if unreadable."""
    if tomllib is None:  # pragma: no cover
        raise ImportError("TOML parsing requires tomllib (Python 3.11+) or tomli.")

    toml_path = task_dir / "task.toml"
    if not toml_path.is_file():
        return None
    with open(toml_path, "rb") as fh:
        task_toml = tomllib.load(fh)

    instr_path = task_dir / "instruction.md"
    instruction = instr_path.read_text(errors="replace") if instr_path.is_file() else ""

    ground_truth = load_ground_truth(task_dir, task_toml)
    oracle_files = _flatten_gt_paths(ground_truth)

    if not instruction or not oracle_files:
        return None
    return TaskOracle(instruction=instruction, oracle_files=oracle_files,
                      task_toml=task_toml, ground_truth=ground_truth)


# --- Gate B decision (Option B: waiver-aware admission) ---------------------

@dataclass(frozen=True)
class GateBResult:
    task_id: str
    bucket: Bucket
    token: str
    admit: bool
    waived: bool
    reason: str


def decide(task_id: str, oracle: TaskOracle) -> GateBResult:
    """Apply the Option B decision rule to one already-loaded task.

    leak = classify(instruction, ground_truth)
    if bucket == EXACT_PATH and token in qa.waivers -> ADMIT (waived entry point)
    elif bucket in {EXACT_PATH, SYMBOL, DIR}        -> REJECT
    else                                            -> ADMIT
    """
    leak = classify(oracle.instruction, oracle.oracle_files, None)

    if leak.bucket is Bucket.EXACT_PATH:
        waiver_tokens = _load_waiver_tokens(oracle.task_toml)
        triggering_path = leak.source_path or leak.token
        if triggering_path in waiver_tokens:
            return GateBResult(
                task_id=task_id,
                bucket=leak.bucket,
                token=leak.token,
                admit=True,
                waived=True,
                reason=(
                    f"exact_path leak on {triggering_path!r} is a recorded "
                    "qa.waivers entry point; admitted per Option B"
                ),
            )

    if leak.bucket in REJECT:
        return GateBResult(
            task_id=task_id,
            bucket=leak.bucket,
            token=leak.token,
            admit=False,
            waived=False,
            reason=f"{leak.bucket.value} leak on {leak.token!r}, no matching qa.waivers entry",
        )

    return GateBResult(
        task_id=task_id,
        bucket=leak.bucket,
        token=leak.token,
        admit=True,
        waived=False,
        reason="no locational leak",
    )


def decide_task_dir(task_dir: Path) -> GateBResult | None:
    oracle = load_task(task_dir)
    if oracle is None:
        return None
    task_block = oracle.task_toml.get("task", {})
    task_id = task_block.get("id", task_dir.name) if isinstance(task_block, dict) else task_dir.name
    return decide(task_id, oracle)


# --- CLI --------------------------------------------------------------------

def _run_corpus(root: Path, *, as_json: bool) -> int:
    results: list[GateBResult] = []
    unreadable: list[str] = []
    for task_dir in find_task_dirs(root):
        result = decide_task_dir(task_dir)
        if result is None:
            unreadable.append(str(task_dir.relative_to(root)))
            continue
        results.append(result)

    by_bucket: dict[str, int] = {}
    for r in results:
        by_bucket[r.bucket.value] = by_bucket.get(r.bucket.value, 0) + 1
    rejects = [r for r in results if not r.admit]
    waived = [r for r in results if r.waived]

    if as_json:
        payload = {
            "total": len(results),
            "unreadable": unreadable,
            "bucket_distribution": by_bucket,
            "admitted": len(results) - len(rejects),
            "rejected": len(rejects),
            "waived_admits": len(waived),
            "rejects": [
                {"task_id": r.task_id, "bucket": r.bucket.value, "token": r.token, "reason": r.reason}
                for r in rejects
            ],
            "waived_tasks": [
                {"task_id": r.task_id, "token": r.token} for r in waived
            ],
        }
        print(json.dumps(payload, indent=2))
        return 1 if rejects else 0

    print(f"Gate B leak classification over {len(results)} tasks "
          f"({len(unreadable)} unreadable, skipped):")
    for bucket_name in ("exact_path", "symbol", "dir", "behavioral", "grep_hard"):
        print(f"  {bucket_name:11} {by_bucket.get(bucket_name, 0)}")
    print(f"\n{len(waived)} exact_path leaks ADMITTED via qa.waivers:")
    for r in waived:
        print(f"  WAIVED  {r.task_id:40} token={r.token!r}")
    print(f"\n{len(rejects)}/{len(results)} tasks REJECTED by Gate B (leaky, unwaived):")
    for r in rejects:
        print(f"  REJECT  {r.bucket.value:11} {r.token!r:40} {r.task_id}")
    return 1 if rejects else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_dir", nargs="?", help="Single task directory to classify")
    parser.add_argument("--corpus", metavar="ROOT", help="Batch-classify every task.toml under ROOT")
    parser.add_argument("--json", action="store_true", help="JSON output (--corpus only)")
    args = parser.parse_args(argv)

    if args.corpus:
        return _run_corpus(Path(args.corpus), as_json=args.json)
    if not args.task_dir:
        parser.print_help()
        return 0

    result = decide_task_dir(Path(args.task_dir))
    if result is None:
        print("could not load an oracle+instruction for this task", file=sys.stderr)
        return 2
    verdict = "ADMIT" if result.admit else "REJECT"
    waived_note = " (waived)" if result.waived else ""
    print(f"{verdict}{waived_note}  bucket={result.bucket.value}  token={result.token!r}")
    print(f"  {result.reason}")
    return 0 if result.admit else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
