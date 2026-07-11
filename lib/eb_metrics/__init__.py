"""EnterpriseBench cross-rig metrics adapters.

Adapter modules that project EB result formats onto shared schemas owned
by other rigs (currently codeprobe's ``TraceQualityReporter``). Each
adapter is a structural projection — no semantic judgment, no hidden
heuristics — so quality reporting stays shape-compatible across rigs
without coupling EB's runner to codeprobe's internals.
"""

from eb_metrics.trace_quality_adapter import (
    metrics_from_eb_record,
    metrics_from_results_file,
)
from eb_metrics.aggregate_quality import build_quality_metrics
from eb_metrics.ir_metrics import (
    IRScores,
    compute_ir_scores,
    localization_at_1,
    localization_at_k,
)
from eb_metrics.retrieval_extraction import (
    TTFRResult,
    compute_run_ir_scores,
    compute_ttfr,
    required_files_from_ground_truth,
    resolve_ground_truth,
    retrieved_files_from_trace,
)
from eb_metrics.retrieval_rollup import (
    RunRetrieval,
    MeanRetrieval,
    aggregate_retrieval,
    iter_run_retrievals,
)

__all__ = [
    "build_quality_metrics",
    "metrics_from_eb_record",
    "metrics_from_results_file",
    "IRScores",
    "compute_ir_scores",
    "localization_at_1",
    "localization_at_k",
    "compute_run_ir_scores",
    "TTFRResult",
    "compute_ttfr",
    "required_files_from_ground_truth",
    "resolve_ground_truth",
    "retrieved_files_from_trace",
    "RunRetrieval",
    "MeanRetrieval",
    "aggregate_retrieval",
    "iter_run_retrievals",
]
