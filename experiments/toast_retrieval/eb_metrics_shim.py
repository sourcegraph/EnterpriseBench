"""Import an eb_metrics submodule without executing eb_metrics/__init__.py.

The package's ``__init__`` eagerly imports ``trace_quality_adapter``, which
pulls ``codeprobe`` and then ``docker_image`` -- a dependency ``lib/pyproject.toml``
does not declare and this experiment's venv does not have. Standing a stub
package with a real ``__path__`` in front of it, so submodules resolve their
package-internal imports, loads the metric code without that gap and without
vendoring it into this experiment.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def import_eb_metrics(module_name: str):
    if "eb_metrics" not in sys.modules:
        package = types.ModuleType("eb_metrics")
        package.__path__ = [str(REPO_ROOT / "lib" / "eb_metrics")]
        package.__package__ = "eb_metrics"
        sys.modules["eb_metrics"] = package
    return importlib.import_module(module_name)
