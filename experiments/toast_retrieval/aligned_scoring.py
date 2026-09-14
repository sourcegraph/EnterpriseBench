"""Scoring that survives the corpus's mixed repo-qualification.

Ground truth is repo-qualified for part of the corpus and repo-relative for
the rest -- ``otel-go/sdk/trace/batch_span_processor.go`` in one task,
``sdk/trace/batch_span_processor.go`` in another (EnterpriseBench-7x8v). The
retrieval side is uniformly repo-relative, because that is what the tools
emit.

``ir_metrics.normalize_path`` handles that by stripping one leading segment
unless it is on a 22-name allowlist (``src``, ``lib``, ``pkg``, ...). For a
repo whose top directory is not on the list -- ``sdk``, ``httpx``, ``axum``,
``packages``, ``pilot``, ``source`` -- it strips a *real* directory from the
retrieved path while stripping only the repo name from ground truth, leaving
``sdk/trace/x.go`` to be compared against ``trace/x.go``. A correct retrieval
scores as a miss.

EB's own ``compute_run_ir_scores`` runs ``_align_retrieved_to_required``
first, which repairs the opposite asymmetry (retrieved *longer* than
required, as in ``/workspace/zulip/zerver/x.py``). It cannot repair this one:
its guard is ``len(retrieved_components) >= len(required_components)``.

So this module matches on a component-wise suffix relation in **either**
direction, which is the only form that is agnostic to which side carries the
repo name. Two constraints keep that from over-crediting, both of them
answers to false credits found in real reports:

* **Bounded prefix drop** (``MAX_PREFIX_DROP``). The only legitimate reason
  for a length difference is a qualifier prefix on one side: a repo name, and
  at most one conventional source root above it (``src/flask/json/__init__.py``
  cited as ``json/__init__.py``). Beyond two components the match is a
  coincidence, and measurably so: unbounded, ``listener/ConcurrentMessage
  ListenerContainer.java`` credited against a seven-deep Spring path, and
  gatekeeper's *vendored copy* ``constraint/vendor/github.com/
  open-policy-agent/opa/v1/ast/parser.go`` credited against OPA's own
  ``ast/parser.go`` in a different repo.
* **One-to-one assignment** (``match_pairs``). Reports carry no per-path repo
  tag, so one cited path can suffix-match two required files in a multi-repo
  task -- ``pkg_resources/__init__.py`` matches both setuptools' copy and
  pip's vendored one in ``config-drift-dual-setuptools-pip-001``. A policy
  that cited one path retrieved one file, so each cited path claims at most
  one required file and each required file is claimed at most once, resolved
  by smallest prefix drop first.

Measured effect on this experiment's sweeps: +0.121 file_recall for toast1
and +0.156 for code_finder on the hard slice. The correction is not uniform
across arms -- it depends on each repo's top-level directory name and on the
citation form each policy emits -- so leaving it uncorrected is a confound in
the comparison, not a constant offset.
"""

from __future__ import annotations

from eb_metrics_shim import import_eb_metrics

_extraction = import_eb_metrics("eb_metrics.retrieval_extraction")
_ir = import_eb_metrics("eb_metrics.ir_metrics")

path_components = _extraction._path_components
compute_ir_scores = _ir.compute_ir_scores

# Repo name, plus at most one conventional source root above it.
MAX_PREFIX_DROP = 2

# Stamped into every report this module scores. `compare_policies` refuses
# reports without it, so a pre-fix sweep cannot be silently averaged in with
# corrected ones.
SCORING_CONTRACT = "aligned_suffix_v1"


def prefix_drop(required: str, retrieved: str) -> int | None:
    """Components dropped to make the two paths agree, or None if they don't.

    Zero when the paths are already identical component-wise.
    """
    req_parts = path_components(required)
    got_parts = path_components(retrieved)
    if not req_parts or not got_parts:
        return None
    shorter, longer = (
        (req_parts, got_parts)
        if len(req_parts) <= len(got_parts)
        else (got_parts, req_parts)
    )
    # A bare filename may only match another bare filename: crediting
    # "index.js" against "packages/babel-core/src/index.js" would count a
    # guess as a retrieval.
    if len(shorter) < 2 and len(longer) > 1:
        return None
    if longer[-len(shorter):] != shorter:
        return None
    dropped = len(longer) - len(shorter)
    return dropped if dropped <= MAX_PREFIX_DROP else None


def suffix_match(required: str, retrieved: str) -> bool:
    """True when the two paths name the same file under a qualifier prefix."""
    return prefix_drop(required, retrieved) is not None


def _representatives(paths: list[str]) -> dict[str, str]:
    """Map each cited path to the first path naming the same file.

    One-to-one assignment must run over distinct *files*, not distinct
    strings: a policy that cites ``axum/src/routing/mod.rs`` and
    ``axum/axum/src/routing/mod.rs`` named one file twice, and both should
    resolve to the same required entry rather than one of them being pushed
    out of the assignment as if it were a second, irrelevant result.
    """
    representative: dict[str, str] = {}
    kept: list[str] = []
    for path in paths:
        match = next((k for k in kept if suffix_match(k, path)), None)
        if match is None:
            kept.append(path)
            match = path
        representative[path] = match
    return representative


def match_pairs(retrieved: list[str], required: list[str]) -> dict[str, str]:
    """Assign cited paths to required files, one to one.

    Returns ``{retrieved path: required path}`` covering every cited path that
    names an assigned file, duplicates included. Candidates are resolved by
    smallest prefix drop first, so an exact match always wins over a match
    that has to discard a qualifier, and no required file is claimed twice.
    """
    representative = _representatives(retrieved)
    distinct = list(dict.fromkeys(representative.values()))

    candidates = []
    for got in distinct:
        for req in required:
            drop = prefix_drop(req, got)
            if drop is not None:
                candidates.append((drop, got, req))
    candidates.sort(key=lambda c: c[0])

    assigned: dict[str, str] = {}
    claimed: set[str] = set()
    for _, got, req in candidates:
        if got in assigned or req in claimed:
            continue
        assigned[got] = req
        claimed.add(req)
    return {
        path: assigned[rep]
        for path, rep in representative.items()
        if rep in assigned
    }


def found(retrieved: list[str], required: list[str]) -> set[str]:
    """Which required files a policy actually retrieved."""
    return set(match_pairs(retrieved, required).values())


def align(retrieved: list[str], required: list[str]) -> list[str]:
    """Rewrite each retrieved path to the required path it was assigned.

    Mirrors ``_align_retrieved_to_required``'s contract -- order preserved,
    de-duplicated on components, unmatched paths keep their own form so they
    stay correctly non-relevant.
    """
    assigned = match_pairs(retrieved, required)
    aligned: list[str] = []
    seen: set[tuple[str, ...]] = set()
    for path in retrieved:
        canon = assigned.get(path, path)
        key = path_components(canon)
        if key and key not in seen:
            seen.add(key)
            aligned.append(canon)
    return aligned


def score(retrieved: list[str], required: list[str], task_id: str, config: str):
    """``compute_ir_scores`` over one-to-one aligned retrieved paths."""
    return compute_ir_scores(align(retrieved, required), required, task_id, config)
