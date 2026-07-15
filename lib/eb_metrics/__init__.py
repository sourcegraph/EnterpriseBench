"""EnterpriseBench cross-rig metrics adapters.

Adapter modules that project EB result formats onto shared schemas owned
by other rigs (currently codeprobe's ``TraceQualityReporter``). Each
adapter is a structural projection — no semantic judgment, no hidden
heuristics — so quality reporting stays shape-compatible across rigs
without coupling EB's runner to codeprobe's internals.

This package deliberately imports nothing. The adapters do not share a
dependency floor: ``trace_quality_adapter`` and ``aggregate_quality`` need
codeprobe installed, while the retrieval, IR and usage modules need nothing
outside the stdlib. Anything bound here is executed by *every* consumer,
including ``scripts/cost_tracker.py``, which reaches this package only for
:class:`~eb_metrics.usage_dedup.UsageDedup` and must keep running where
codeprobe is absent. Import the defining submodule instead.
"""
