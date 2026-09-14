"""localization_evidence scorer — did the agent *localize*, or dump a dictionary?

The precision-aware sibling of :mod:`eb_verify.plugins.file_extraction`, for the
14 enterprise-hard tasks only. Same CLI shape, same env (``GT_FILE``,
``ANSWER_FILE``, ``--keys``), same infra-vs-agent-fault discipline — it reuses
file_extraction's parsers wholesale rather than re-spelling them::

    ANSWER_FILE=... GT_FILE=... python3 -m eb_verify.plugins.localization_evidence \
        --keys source_files,files,error_source.files,code_paths,citations

Why a separate scorer instead of a flag on file_extraction: file_extraction is
recall-only *on purpose* — over-listing is free there so the ~100 corpus tasks
stay comparable to each other (its docstring, and bead vdeyx). That recall-only
rule is exactly what residual B exploited on the enterprise-hard stratum: a
static basename dictionary (``conn.rs``, ``stream.rs``, ``rtls.rs``) or a
``find /workspace -type f`` dump scored full credit with zero investigation,
because recall does not care how much junk rode along, and a bare basename
suffix-matches a repo-qualified oracle. Diverging the shared scorer would move
scoring for every task at once (not this bead's call), so the divergence is
scoped to a new module the 14 tasks route to, and file_extraction is left
byte-for-byte unchanged.

Two rules close the hole:

* **F1, not recall.** Precision is the fraction of the agent's answers that
  actually point at an oracle file, so 3 real paths buried in 200 junk score
  ~0.03, not 1.0. An honest, precise answer still scores 1.0.
* **A bare basename is not a localization.** ``conn.rs`` names a file in every
  repo; the directory is the evidence the agent read the tree. So a match needs
  a directory-qualified answer (>= 2 path components) — *unless the oracle path
  is itself a single component* (a repo-root file like follow-redirects'
  ``index.js``), where the bare name is the only truthful spelling and an exact
  full-path match is credited.

This is a stratum divergence by design: an honest but verbose answer that scored
1.0 under recall-only can score below pass here. That is the point — the
enterprise-hard tasks pay for precise localization, not recall.
"""

from __future__ import annotations

import argparse
from typing import List

from eb_verify.plugins.file_extraction import (
    PASS_THRESHOLD,
    _InputsEmitted,
    _JsonErrorParser,
    _matches_parts,
    components,
    emit,
    load_scoring_inputs,
    score_answer,
)


def _directory_qualified(af_parts: List[str], gt_component_set: set[tuple[str, ...]]) -> bool:
    """Is this answer specific enough to count as a localization?

    A directory-qualified path (>= 2 components) always is. A bare basename is
    not — it names a file in every repo — *unless* the oracle itself is a bare
    basename (a repo-root file), in which case the bare name is the only truthful
    spelling and an exact match is credited. This keeps a basename dictionary
    from scoring while a legitimate root-level oracle (follow-redirects'
    ``index.js``) still can.
    """
    if len(af_parts) >= 2:
        return True
    return tuple(af_parts) in gt_component_set


def score_localization(
    gt_paths: List[str], found: List[str]
) -> tuple[float, float, float, set[str]]:
    """F1 over the oracle paths, rejecting bare basenames. Returns (f1, precision, recall, matched).

    Recall reuses :func:`score_answer`'s refine/abbreviate/ambiguous
    disambiguation, on the answers that clear the directory-qualified bar.
    Precision divides productive answers by *all* answers, so padding the list
    with bare basenames or junk paths costs credit rather than being free.
    """
    # Split each path into components once; both the qualified filter and the
    # productive count read these rather than re-parsing per (answer, gt) pair.
    gt_parts = {gt: components(gt) for gt in gt_paths}
    found_parts = {af: components(af) for af in found}
    gt_component_set = {tuple(parts) for parts in gt_parts.values()}

    qualified = [af for af in found if _directory_qualified(found_parts[af], gt_component_set)]

    matched, _ambiguous = score_answer(gt_paths, qualified)
    recall = len(matched) / len(gt_paths)

    productive = [
        af
        for af in qualified
        if any(_matches_parts(gt_parts[gt], found_parts[af]) for gt in gt_paths)
    ]
    # Denominator is every answer, including the bare basenames dropped from
    # `qualified`: a dictionary dump must dilute precision, not be filtered free.
    precision = len(productive) / len(found) if found else 0.0

    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return f1, precision, recall, matched


def build_parser() -> argparse.ArgumentParser:
    parser = _JsonErrorParser(
        prog="python -m eb_verify.plugins.localization_evidence",
        description="Score localization precision+recall (F1) over ground-truth oracle paths.",
        add_help=False,
    )
    parser.add_argument(
        "--keys",
        required=True,
        help="Comma-separated answer keys, unioned. Dotted keys traverse nested "
             "objects (e.g. error_source.files).",
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    try:
        _keys, gt_paths, found = load_scoring_inputs(argv, build_parser())
    except _InputsEmitted as done:
        return done.code

    f1, precision, recall, matched = score_localization(gt_paths, found)
    missed = [gt for gt in gt_paths if gt not in matched]

    detail = (
        f"Localization F1 {round(f1, 4)} "
        f"(precision {round(precision, 4)} over {len(found)} answers, "
        f"recall {len(matched)}/{len(gt_paths)})"
    )
    if missed:
        detail += f" (missed: {', '.join(missed)})"
    if f1 < PASS_THRESHOLD and precision < recall:
        detail += " — over-listing or bare basenames diluted the answer below pass"
    return emit(f1, detail)


if __name__ == "__main__":
    import sys

    sys.exit(main())
