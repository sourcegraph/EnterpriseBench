"""Schema-agnostic QA library for benchmark task definitions.

Consumed by EnterpriseBench (EB) and CodeScaleBench (CSB) adapters.
Each rig parses its own task-meta schema and feeds already-extracted inputs into
these pure-functional checks. Findings come back as a flat ``list[Finding]``
that the rig surfaces however it likes (CLI report, dolt write, etc.).

This copy is the **F2 subset** of the full library: only the aux-file-leakage
check is vendored (see ``VENDOR.md``). The oracle-coherence and
scoring-honesty checks live on the fix/eb-f2-leak-triage branch and can be
ported later without changing this package's namespace.

Public API:

* :class:`Finding` — uniform per-issue record
* :func:`check_aux_file_leakage` — flags oracle tokens that appear in
  context/auxiliary files visible to the agent

Finding-code namespace (subset present here):

* ``F*`` — aux-file-leakage (``check_aux_file_leakage``)

Reserved by the full library (not vendored): ``A*``/``B*``/``C*``/``D*``
(oracle coherence), ``E*`` (scoring-method tier).
"""

from .leakage import check_aux_file_leakage
from .types import Finding

__all__ = [
    "Finding",
    "check_aux_file_leakage",
]
