"""codeprobe must be an installable dependency of the dev/test environment.

`lib/eb_metrics/__init__.py` imports `trace_quality_adapter` (and
`aggregate_quality`) unconditionally at package-init time, and both import
`codeprobe.analysis.trace_quality`. `lib/pyproject.toml` never declared
`codeprobe`, so CI's `pip install -e "lib/[dev]"` never installed it: every
test under `tests/eb_metrics/` — and any other module that merely imports
`eb_metrics` — died at collection with `ModuleNotFoundError: No module named
'codeprobe'`. That was the actual cause of CI being red on main for 8+
consecutive pushes (the collection step exits 2 and no later job step runs).

Mirrors TestOptionalDependencyDeclaration in
tests/integrity/test_plugin_registration_deps.py, which pins the numpy/
scikit-learn [scoring] extra the same way.
"""

from __future__ import annotations

import importlib
import re
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "lib"


def _names(requirements: list[str]) -> set[str]:
    """Distribution names from PEP 508 strings, minus extras/version/markers."""
    return {
        re.split(r"[<>=!~;\[\s]", req, maxsplit=1)[0].strip().lower()
        for req in requirements
    }


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return tomllib.loads((LIB / "pyproject.toml").read_text(encoding="utf-8"))


class TestCodeprobeDeclared:
    def test_metrics_extra_declares_codeprobe(self, pyproject: dict) -> None:
        extras = pyproject["project"]["optional-dependencies"]
        assert "metrics" in extras, (
            "no [metrics] extra — codeprobe (required by "
            "lib/eb_metrics/trace_quality_adapter.py) is undeclared, so "
            "`pip install -e \"lib/[dev]\"` in CI will never install it"
        )
        assert "codeprobe" in _names(extras["metrics"]), (
            "the [metrics] extra must name codeprobe, the dependency "
            "eb_metrics.trace_quality_adapter imports unconditionally"
        )

    def test_dev_extra_pulls_in_metrics(self, pyproject: dict) -> None:
        """CI installs `lib/[dev]` specifically, not `lib/[metrics]` — the
        [metrics] extra existing is not enough if [dev] never references it.
        """
        dev_entries = pyproject["project"]["optional-dependencies"]["dev"]
        assert any(
            re.match(r"eb-verify\[.*\bmetrics\b.*\]", entry) for entry in dev_entries
        ), (
            f"[dev] does not pull in [metrics]: {dev_entries!r} — "
            "`pip install -e \"lib/[dev]\"` (the exact command CI runs) "
            "would still land without codeprobe"
        )


class TestEbMetricsActuallyImports:
    """The declaration above is only real if it results in an importable
    package in the environment CI's `pip install -e "lib/[dev]"` produces.
    This environment already has codeprobe installed (dev machine or CI
    post-fix), so a plain import pins the fix against a future regression
    that re-breaks the codeprobe API surface eb_metrics depends on.
    """

    def test_eb_metrics_package_imports(self) -> None:
        sys.modules.pop("eb_metrics", None)
        sys.modules.pop("eb_metrics.trace_quality_adapter", None)
        module = importlib.import_module("eb_metrics")
        assert hasattr(module, "metrics_from_eb_record")
        assert hasattr(module, "build_quality_metrics")

    def test_trace_quality_adapter_symbols_present(self) -> None:
        from codeprobe.analysis.trace_quality import (  # noqa: F401
            LOW_RECALL_THRESHOLD,
            TraceQualityMetrics,
        )
