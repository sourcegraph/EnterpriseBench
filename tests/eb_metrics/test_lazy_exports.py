"""The eb_metrics package must not impose one adapter's deps on every consumer.

``trace_quality_adapter`` and ``aggregate_quality`` import codeprobe, which is a
sibling rig rather than a declared dependency — CI installs only ``pytest
jsonschema tomli`` plus ``pip install -e lib/``. While ``__init__`` bound every
export eagerly, importing *any* eb_metrics module executed those two, so the
stdlib-only modules (``usage_dedup``, ``retrieval_extraction``, ``ir_metrics``,
``retrieval_rollup``) were unimportable wherever codeprobe was absent, and
``scripts/cost_tracker.py`` — dependency-clean until it started reaching here for
:class:`UsageDedup` — died at import before reading a trace (EnterpriseBench-4p5k).

The subprocess tests block codeprobe at the meta-path instead of relying on it
being uninstalled, so they fail the same way in a dev env that has it.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Reject codeprobe no matter where it sits on the path, reproducing CI's env.
_BLOCK_CODEPROBE = """
import sys

class _BlockCodeprobe:
    def find_spec(self, name, path=None, target=None):
        if name == "codeprobe" or name.startswith("codeprobe."):
            raise ModuleNotFoundError("No module named 'codeprobe'")
        return None

sys.meta_path.insert(0, _BlockCodeprobe())
"""

_IMPORT_PATH = "".join(
    f"sys.path.insert(0, {str(REPO_ROOT / part)!r})\n"
    for part in ("", "scripts", "lib")
)


def _run_without_codeprobe(body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _BLOCK_CODEPROBE + _IMPORT_PATH + textwrap.dedent(body)],
        capture_output=True,
        text=True,
    )


def test_usage_dedup_imports_without_codeprobe() -> None:
    result = _run_without_codeprobe(
        """
        from eb_metrics.usage_dedup import UsageDedup
        print(UsageDedup().total_tokens)
        """
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0"


def test_cost_tracker_imports_without_codeprobe() -> None:
    # The cost-report entrypoint. It reaches eb_metrics only for UsageDedup.
    result = _run_without_codeprobe(
        """
        import cost_tracker
        print(cost_tracker.parse_trace.__name__)
        """
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "parse_trace"


def test_retrieval_axis_imports_without_codeprobe() -> None:
    result = _run_without_codeprobe(
        """
        from eb_metrics.retrieval_extraction import compute_ttfr
        from eb_metrics.ir_metrics import compute_ir_scores
        from eb_metrics.retrieval_rollup import aggregate_retrieval
        print("ok")
        """
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_codeprobe_backed_export_still_fails_loudly_when_codeprobe_is_absent() -> None:
    # Laziness defers the dependency; it must not disguise a missing one as a
    # merely-absent attribute.
    result = _run_without_codeprobe(
        """
        import eb_metrics
        try:
            eb_metrics.build_quality_metrics
        except ModuleNotFoundError as exc:
            print(f"ModuleNotFoundError: {exc}")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "No module named 'codeprobe'" in result.stdout


def test_lazy_attribute_access_returns_the_real_object() -> None:
    import eb_metrics
    from eb_metrics.usage_dedup import UsageDedup
    from eb_metrics.retrieval_extraction import compute_ttfr

    assert eb_metrics.compute_ttfr is compute_ttfr
    # UsageDedup is deliberately not re-exported; the submodule is the entrypoint.
    assert "UsageDedup" not in eb_metrics.__all__
    assert UsageDedup is not None


def test_every_advertised_export_resolves() -> None:
    pytest.importorskip("codeprobe", reason="two exports are codeprobe-backed")
    import eb_metrics

    for name in eb_metrics.__all__:
        assert getattr(eb_metrics, name) is not None


def test_unknown_attribute_raises_attribute_error() -> None:
    import eb_metrics

    with pytest.raises(AttributeError, match="no attribute 'nope'"):
        eb_metrics.nope
