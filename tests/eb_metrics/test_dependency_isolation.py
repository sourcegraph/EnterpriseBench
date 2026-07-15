"""The eb_metrics package must not impose one adapter's deps on every consumer.

``trace_quality_adapter`` and ``aggregate_quality`` import codeprobe, a sibling
rig rather than a declared dependency — CI installs only ``pytest jsonschema
tomli`` plus ``pip install -e lib/``. Anything ``__init__`` binds is executed by
every consumer, which is what made the stdlib-only modules (``usage_dedup``,
``retrieval_extraction``, ``ir_metrics``, ``retrieval_rollup``) unimportable
wherever codeprobe was absent.

The subprocess tests block codeprobe at the meta-path rather than relying on it
being uninstalled, so they fail the same way in a dev env that has it.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

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


def test_importing_the_package_alone_does_not_need_codeprobe() -> None:
    # The isolation is that __init__ binds nothing, so reaching one adapter never
    # executes another's imports.
    result = _run_without_codeprobe(
        """
        import eb_metrics
        print(sorted(n for n in vars(eb_metrics) if not n.startswith("__")))
        """
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_codeprobe_backed_adapter_still_fails_loudly_when_codeprobe_is_absent() -> None:
    # Deferring the dependency must not disguise a missing one as merely absent.
    result = _run_without_codeprobe(
        """
        from eb_metrics.aggregate_quality import build_quality_metrics
        """
    )
    assert result.returncode != 0
    assert "ModuleNotFoundError: No module named 'codeprobe'" in result.stderr
