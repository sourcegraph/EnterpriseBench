"""Every source path an answer key cites must exist at the task's pinned rev.

The inverse of the prompt-echo defect (EnterpriseBench-jn73.2.7.3): echo makes a
task gradable without reading the code, while a 404 in the answer key makes it
ungradable no matter how well the agent reads. camel-routing-arch-001 cited

    core/camel-api/src/main/java/org/apache/camel/model/RouteDefinition.java

which does not exist at camel-4.4.0 — RouteDefinition lives in camel-core-model.
An agent that actually opened the file could only ever cite the real path, so the
key graded honest work against a string no honest agent can produce
(EnterpriseBench-c7iik).

Scoped to the tasks whose keys have been path-verified against their pinned rev,
not the whole tree: this asserts a verified fact, and a fleet-wide sweep belongs
in `validate_expected_solutions.py --check-paths`, which flags authoring-time.
Add a task here once its key is verified.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
BENCHMARKS = REPO_ROOT / "benchmarks"

sys.path.insert(0, str(REPO_ROOT / "scripts" / "validation"))
from validate_expected_solutions import _extract_paths, _parse_toml  # noqa: E402

# Suite-relative task dirs whose answer keys are verified against their pinned rev.
VERIFIED_TASKS = [
    "feature_delivery/camel-routing-arch-001",
    # Lane ground-truth authoring: each key was written from a shallow clone of
    # the pinned revs and every extracted path stat()'d locally before landing.
    "customer_escalation/support-mapping-dual-flask-001",
    "customer_escalation/support-mapping-dual-grafana-001",
]


def _network_available() -> bool:
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", "https://github.com/apache/camel.git"],
            capture_output=True,
            timeout=15,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _raw_path_exists(repo_url: str, rev: str, path: str) -> bool | None:
    """True/False for path existence at rev; None on a transport error.

    raw.githubusercontent.com rather than the Contents API: no token, and this
    asks exactly the question the fix turned on — does the file resolve at the
    pinned rev.
    """
    slug = repo_url.removeprefix("https://github.com/").removesuffix(".git").strip("/")
    url = f"https://raw.githubusercontent.com/{slug}/{urllib.parse.quote(rev)}/{urllib.parse.quote(path)}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        return None
    except (urllib.error.URLError, TimeoutError):
        return None


def _cited_paths(task_dir: Path) -> list[str]:
    """Every source path the task's answer key cites, from all three key files."""
    texts: list[str] = []

    key = json.loads((task_dir / "expected_solution.json").read_text())
    for body in (key.get("checkpoints") or {}).values():
        if not isinstance(body, dict):
            continue
        texts.append(body.get("expected_solution"))
        texts.extend(body.get("evaluation_criteria") or [])

    gt = json.loads((task_dir / "ground_truth.json").read_text())
    for group in ("required_files", "sufficient_files"):
        texts.extend(entry.get("path") for entry in gt.get(group) or [])
    for tokens in (gt.get("scoring_evidence") or {}).values():
        texts.extend(tokens)

    toml = _parse_toml(task_dir / "task.toml")
    texts.extend(
        entry.get("path")
        for entry in (toml.get("ground_truth") or {}).get("required_files") or []
    )

    return _extract_paths(texts)


@pytest.mark.network
@pytest.mark.parametrize("task", VERIFIED_TASKS, ids=VERIFIED_TASKS)
def test_cited_paths_exist_at_pinned_rev(task: str) -> None:
    if not _network_available():
        pytest.skip("Network unavailable — skipping answer-key path verification")

    task_dir = BENCHMARKS / task
    repos = _parse_toml(task_dir / "task.toml")["repos"]
    assert repos, f"{task}: no repos declared"

    paths = _cited_paths(task_dir)
    assert paths, f"{task}: answer key cites no source paths — extraction is broken"

    missing: list[str] = []
    for path in paths:
        results = [_raw_path_exists(r["url"], r["rev"], path) for r in repos]
        if any(r is True for r in results):
            continue
        if all(r is None for r in results):
            pytest.skip(f"transport error resolving {path} — not a fidelity verdict")
        missing.append(path)

    pinned = ", ".join(f"{r['url']}@{r['rev']}" for r in repos)
    assert not missing, (
        f"{task}: answer key cites paths that do not exist at {pinned} — "
        f"agents are graded against files they cannot find: {missing}"
    )


def test_route_definition_is_attributed_to_camel_core_model() -> None:
    """The c7iik regression, pinned offline so it fails without a network run.

    RouteDefinition is camel-core-model's at camel-4.4.0; camel-api has no
    model/ package at that tag at all.
    """
    key = (
        BENCHMARKS / "feature_delivery/camel-routing-arch-001/expected_solution.json"
    ).read_text()

    assert "core/camel-api/src/main/java/org/apache/camel/model/" not in key
    assert (
        "core/camel-core-model/src/main/java/org/apache/camel/model/RouteDefinition.java"
        in key
    )
