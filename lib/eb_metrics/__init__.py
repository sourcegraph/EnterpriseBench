"""EnterpriseBench cross-rig metrics adapters.

Adapter modules that project EB result formats onto shared schemas owned
by other rigs (currently codeprobe's ``TraceQualityReporter``). Each
adapter is a structural projection — no semantic judgment, no hidden
heuristics — so quality reporting stays shape-compatible across rigs
without coupling EB's runner to codeprobe's internals.

Exports resolve on first access (PEP 562) rather than at import, because the
adapters do not share a dependency floor: ``trace_quality_adapter`` and
``aggregate_quality`` need codeprobe installed, while the retrieval, IR and
usage modules need nothing outside the stdlib. Binding them all eagerly made
codeprobe a hard requirement of *every* consumer — including
``scripts/cost_tracker.py``, which reaches this package only for
:class:`~eb_metrics.usage_dedup.UsageDedup` and must keep running where
codeprobe is absent (EnterpriseBench-4p5k).

Import the submodule, not this package, in new code. A type-checker cannot see
through :func:`__getattr__`, so a name reached as ``eb_metrics.compute_ttfr``
checks as ``Any``, losing the signature. The usual repair — restating the
imports under ``if TYPE_CHECKING`` — would put codeprobe back on the checker's
path in exactly the environment this laziness exists to serve, so the re-export
surface below is kept for compatibility rather than recommended. Every consumer
in this repo already imports from the defining submodule, which type-checks
normally.
"""

from __future__ import annotations

import importlib
from typing import Any

# Exported name -> the submodule that defines it.
_EXPORTS: dict[str, str] = {
    "metrics_from_eb_record": "trace_quality_adapter",
    "metrics_from_results_file": "trace_quality_adapter",
    "build_quality_metrics": "aggregate_quality",
    "IRScores": "ir_metrics",
    "compute_ir_scores": "ir_metrics",
    "localization_at_1": "ir_metrics",
    "localization_at_k": "ir_metrics",
    "TTFRResult": "retrieval_extraction",
    "compute_run_ir_scores": "retrieval_extraction",
    "compute_ttfr": "retrieval_extraction",
    "required_files_from_ground_truth": "retrieval_extraction",
    "resolve_ground_truth": "retrieval_extraction",
    "retrieved_files_from_trace": "retrieval_extraction",
    "RunRetrieval": "retrieval_rollup",
    "MeanRetrieval": "retrieval_rollup",
    "aggregate_retrieval": "retrieval_rollup",
    "iter_run_retrievals": "retrieval_rollup",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Import the submodule owning ``name`` on first access, then cache it."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(f".{module_name}", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})
