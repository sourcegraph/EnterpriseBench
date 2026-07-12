"""Tests for scripts/validation/leak_classifier.py -- task-authoring Gate B.

Covers: bucket-classification parity with the CSB reference's own
--selftest cases, the EB task.toml [ground_truth] loader (replacing the
CSB-shipped adapter's broken expected_solution.json{"files"} reader), the
fail-closed admission rule (no waiver forgives a localization leak), and a
real-corpus run producing the leak-bucket distribution.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.validation import leak_classifier as lc

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCHMARKS = REPO_ROOT / "benchmarks"


# ---------------------------------------------------------------------------
# classify() bucket parity (mirrors the CSB reference's --selftest cases)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "instruction, oracle_files, oracle_symbols, want_bucket",
    [
        (
            "Fix the null-deref in cmd/argocd-server/main.go on startup.",
            ["cmd/argocd-server/main.go"],
            [],
            lc.Bucket.EXACT_PATH,
        ),
        (
            "The retry loop double-counts attempts after a transient failure; "
            "callers see inflated attempt numbers. Correct the accounting.",
            ["sg-evals/foo--a1b2c3d4/pkg/backoff/retry.go"],
            [],
            lc.Bucket.BEHAVIORAL,
        ),
        (
            "ParseUpstreamConfig mishandles empty host lists.",
            ["source/common/config/parser.cc"],
            ["ParseUpstreamConfig"],
            lc.Bucket.SYMBOL,
        ),
        (
            "Requests to the escalation service fan out across the gateway and "
            "the billing service and lose their trace id somewhere in the chain.",
            ["gw/trace.go", "billing/ctx.go", "escalation/mw.go"],
            [],
            lc.Bucket.GREP_HARD,
        ),
    ],
)
def test_classify_bucket_parity(instruction, oracle_files, oracle_symbols, want_bucket):
    got = lc.classify(instruction, oracle_files, oracle_symbols)
    assert got.bucket == want_bucket


def test_dir_bucket_requires_path_form_not_bare_noun():
    """A bare domain noun ("the escalation service") must NOT trigger DIR --
    only path form ("escalation/") does. High-precision design: avoids
    false-rejecting every task whose subject matter shares a name with its
    own directory."""
    leak = lc.classify(
        "Requests to the escalation service lose context somewhere in the chain.",
        ["escalation/handler.go"],
        [],
    )
    assert leak.bucket is not lc.Bucket.DIR


# ---------------------------------------------------------------------------
# EB task.toml [ground_truth] loader
# ---------------------------------------------------------------------------


def _toml_entries(section: str, entries: list[dict]) -> list[str]:
    lines: list[str] = []
    for e in entries:
        lines.append(f"[[ground_truth.{section}]]")
        for k, v in e.items():
            lines.append(f'{k} = "{v}"' if isinstance(v, str) else f"{k} = {v}")
        lines.append("")
    return lines


def _write_task(
    tmp_path: Path,
    *,
    instruction: str,
    required_files: list[dict] | None = None,
    sufficient_files: list[dict] | None = None,
    waivers: list[dict] | None = None,
    task_id: str = "synthetic-task-001",
) -> Path:
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    (task_dir / "instruction.md").write_text(instruction)

    lines = ["[task]", f'id = "{task_id}"', ""]
    lines += _toml_entries("required_files", required_files or [])
    lines += _toml_entries("sufficient_files", sufficient_files or [])
    for w in waivers or []:
        lines.append("[[qa.waivers]]")
        for k, v in w.items():
            lines.append(f'{k} = "{v}"')
        lines.append("")
    (task_dir / "task.toml").write_text("\n".join(lines))
    return task_dir


def test_load_task_reads_toml_ground_truth_not_expected_solution_json(tmp_path):
    """The CSB reference's shipped "EB adapter" reads
    expected_solution.json{"files"} -- a key EB's checkpoint-structured tasks
    don't have (real EB expected_solution.json is
    {"checkpoints": {name: {"expected_solution": prose, ...}}}, no "files"
    key at all; that adapter exits 2 on every real EB task). Confirm the
    port's loader reads task.toml [ground_truth] instead, and is unaffected
    by a checkpoint-shaped expected_solution.json sitting right next to it.
    """
    task_dir = _write_task(
        tmp_path,
        instruction="Investigate the checkout flow regression and report root cause.",
        required_files=[{"path": "src/checkout/cart.py", "repo": "shop"}],
    )
    (task_dir / "expected_solution.json").write_text(
        json.dumps({"checkpoints": {"cp1": {"expected_solution": "prose, no files key"}}})
    )
    oracle = lc.load_task(task_dir)
    assert oracle is not None
    assert oracle.oracle_files == ["src/checkout/cart.py"]


def test_load_task_none_when_ground_truth_empty(tmp_path):
    task_dir = _write_task(tmp_path, instruction="Do something.", required_files=[])
    assert lc.load_task(task_dir) is None


def test_load_task_none_when_no_task_toml(tmp_path):
    task_dir = tmp_path / "no-toml"
    task_dir.mkdir()
    (task_dir / "instruction.md").write_text("Do something.")
    assert lc.load_task(task_dir) is None


# ---------------------------------------------------------------------------
# Gate B decision: one rule, and it fails closed
# ---------------------------------------------------------------------------


def test_exact_path_leak_rejects(tmp_path):
    task_dir = _write_task(
        tmp_path,
        instruction="Fix the bug in `metadata/metadata.go`.",
        required_files=[{"path": "metadata/metadata.go", "repo": "grpc-go"}],
    )
    result = lc.decide_task_dir(task_dir)
    assert result is not None
    assert result.bucket is lc.Bucket.EXACT_PATH
    assert result.admit is False


def test_no_waiver_can_forgive_a_leak(tmp_path):
    """The withdrawn Option B rule, pinned shut.

    A `[[qa.waivers]]` entry naming the leaked path used to ADMIT the task, on
    the rationale that the path was a *given entry point* whose scored discovery
    lay downstream. Nothing reads that ledger now, and this test is what keeps it
    that way -- a waiver must be inert, not merely un-consulted-by-default.

    Two things killed the rule. The guard that was supposed to enforce "there IS
    something downstream" compared the leak TOKEN (in practice a two-segment
    fragment like `manifests/ha`) against full oracle paths, so it returned True
    unconditionally and never once rejected a waiver. And the claim itself is
    untestable with what we can measure: Gate C's turns-to-first-relevant counts
    the waived file as a hit, because the waived file is itself a required_file,
    so it scores how fast the agent opened the file the instruction handed it.

    An admission gate fails CLOSED on an untested claim. Restoring the rule is
    EnterpriseBench-jn73.2.12 and needs the measurement, not the argument.
    """
    task_dir = _write_task(
        tmp_path,
        instruction="The producer entry point is `metadata/metadata.go`; find every "
        "consumer of the old API in the etcd codebase.",
        required_files=[
            {"path": "metadata/metadata.go", "repo": "grpc-go"},
            {"path": "clientv3/watch.go", "repo": "etcd"},
        ],
        waivers=[{
            "code": "F2",
            "token": "metadata/metadata.go",
            "decision": "waive",
            "rationale": "entry point, not the scored discovery target",
        }],
    )
    result = lc.decide_task_dir(task_dir)
    assert result is not None
    assert result.bucket is lc.Bucket.EXACT_PATH
    assert result.admit is False, "a waiver must not forgive a localization leak"


def test_dir_bucket_rejects(tmp_path):
    task_dir = _write_task(
        tmp_path,
        instruction="Look under escalation/ for the handler that drops context.",
        required_files=[{"path": "escalation/handler.go", "repo": "svc"}],
    )
    result = lc.decide_task_dir(task_dir)
    assert result is not None
    assert result.bucket is lc.Bucket.DIR
    assert result.admit is False


def test_leak_result_carries_the_source_path_not_just_the_token(tmp_path):
    """`token` can be a fragment of the leaked path ("manifests/ha"), so any
    caller reasoning about the leaked FILE needs the full path. Dropping it is
    what forced the old waiver check to match by suffix and fail open."""
    task_dir = _write_task(
        tmp_path,
        instruction="Fix the bug in `metadata/metadata.go`.",
        required_files=[{"path": "metadata/metadata.go", "repo": "grpc-go"}],
    )
    result = lc.decide_task_dir(task_dir)
    assert result is not None
    assert result.source_path == "metadata/metadata.go"


def test_behavioral_and_grep_hard_always_admit(tmp_path):
    task_dir = _write_task(
        tmp_path,
        instruction="Users report checkout totals are wrong after a promo code "
        "stacks with a loyalty discount. Find the double-application bug.",
        required_files=[
            {"path": "billing/promo.py", "repo": "shop"},
            {"path": "billing/loyalty.py", "repo": "shop"},
            {"path": "billing/cart.py", "repo": "shop"},
        ],
    )
    result = lc.decide_task_dir(task_dir)
    assert result is not None
    assert result.bucket is lc.Bucket.GREP_HARD
    assert result.admit is True


# ---------------------------------------------------------------------------
# Real-corpus integration: the tasks that used to be waived through
# ---------------------------------------------------------------------------

# These 11 carried a `[[qa.waivers]]` entry, and under the withdrawn Option B
# rule 8 of them were ADMITTED despite naming their own oracle. They are leaky
# by the one rule Gate B now has, and they reject. Keeping them enumerated (with
# the leak each one carries) is what makes the policy change auditable: if a
# future edit quietly re-admits any of them, this fails.
PREVIOUSLY_WAIVED_TASK_DIRS = [
    "dependency_management/api-contract-001",
    "dependency_management/api-contract-002",
    "dependency_management/api-contract-005",
    "feature_delivery/monorepo-boundary-001",
    "feature_delivery/monorepo-boundary-006",
    "feature_delivery/schema-evolution-001",
    "feature_delivery/schema-evolution-002",
    "feature_delivery/schema-evolution-004",
    "platform_engineering/config-drift-001",
    "platform_engineering/config-drift-004",
    "security_operations/rbac-audit-001",
]


@pytest.mark.parametrize("rel_dir", PREVIOUSLY_WAIVED_TASK_DIRS)
def test_previously_waived_corpus_tasks_now_reject(rel_dir):
    task_dir = BENCHMARKS / rel_dir
    result = lc.decide_task_dir(task_dir)
    assert result is not None, f"could not load {rel_dir}"
    assert result.bucket in lc.REJECT
    assert result.admit is False, (
        f"{rel_dir} names its own oracle ({result.token!r}) and must reject; "
        f"no waiver forgives a localization leak")


def test_config_drift_001_leaks_on_real_fragment_not_generic_basename():
    """config-drift-001 is the case that proves the generic-basename filter
    (jn73.2) narrows rather than blanket-admits.

    Its leak token was originally recorded as the generic basename `values.yaml`.
    With the filter in, `values.yaml` no longer localizes -- but the instruction
    independently names the path fragment `bitnami/consul` verbatim, which IS a
    real localization leak. So the task still buckets exact_path and still
    rejects: the filter suppressed the false-positive token and left the genuine
    one.

    Asserting the *token* is the point. A filter that admitted this task would be
    over-broad; one that still reported `values.yaml` would be inert."""
    task_dir = BENCHMARKS / "platform_engineering" / "config-drift-001"
    result = lc.decide_task_dir(task_dir)
    assert result is not None
    assert result.bucket is lc.Bucket.EXACT_PATH
    assert result.token == "bitnami/consul"
    assert result.admit is False


# ---------------------------------------------------------------------------
# Corpus-wide leak-bucket distribution
# ---------------------------------------------------------------------------


def test_corpus_run_produces_bucket_distribution(capsys):
    lc._run_corpus(BENCHMARKS, as_json=True)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["total"] > 0
    assert set(payload["bucket_distribution"]) <= {b.value for b in lc.Bucket}
    assert payload["admitted"] + payload["rejected"] == payload["total"]
    # Every reject carries the full leaked path, not just the matched fragment.
    assert all(r["source_path"] for r in payload["rejects"])


# ---------------------------------------------------------------------------
# Generic build-manifest basenames are not localization leaks (jn73.2)
# ---------------------------------------------------------------------------
#
# Every Go repo has a go.mod; every Node repo a package.json. Naming one in a
# prompt tells the agent nothing about WHICH file to investigate, so it is not
# an exact_path leak. Ported from the F2 checker (76f5469), where the same
# false positive inflated the Gate B reject set by 14 of 45.


@pytest.mark.parametrize("basename", ["go.mod", "package.json", "pom.xml", "values.yaml"])
def test_generic_manifest_basename_is_not_an_exact_path_leak(basename):
    """A generic manifest basename must not localize (no exact_path).

    The oracle carries a real source file alongside the manifest, which is the
    ONLY situation where the filter's rationale holds: the manifest is an entry
    point and the scored discovery lies downstream of it. When the manifest is
    the whole oracle, naming it names the answer -- see
    test_generic_filter_does_not_apply_when_the_manifest_IS_the_oracle.
    """
    leak = lc.classify(
        instruction=f"The build fails after a dependency bump. Inspect the {basename}.",
        oracle_files=[f"svc/deps/{basename}", "svc/internal/resolver/graph.go"],
        oracle_symbols=[],
    )
    assert leak.bucket is not lc.Bucket.EXACT_PATH
    assert leak.admit is True


def test_qualified_generic_manifest_path_still_leaks():
    """`envoy/go.mod` has a directory component -- still specific, still a leak."""
    leak = lc.classify(
        instruction="The bump landed in envoy/go.mod and broke the build.",
        oracle_files=["envoy/go.mod"],
        oracle_symbols=[],
    )
    assert leak.bucket is lc.Bucket.EXACT_PATH
    assert leak.admit is False


def test_non_generic_basename_still_leaks():
    """The filter must not blanket-suppress real basename leaks."""
    leak = lc.classify(
        instruction="The regression is in retry_policy.go somewhere.",
        oracle_files=["svc/net/retry_policy.go"],
        oracle_symbols=[],
    )
    assert leak.bucket is lc.Bucket.EXACT_PATH
    assert leak.admit is False


def test_generic_filter_does_not_apply_when_the_manifest_IS_the_oracle():
    """The filter's rationale -- "naming go.mod does not say WHICH file holds the
    answer" -- requires the manifest to be an ENTRY POINT to something else. The
    dep-traversal tasks' entire oracle is ["go.mod", "go.sum"]: there the manifest
    IS the answer, so naming it localizes perfectly.

    Suppressing it there would promote a grep-trivial task into grep_hard -- the
    *preferred*, hardest bucket -- and pass Gate B. The filter must narrow, never
    launder."""
    leak = lc.classify(
        instruction="A CVE landed in a transitive dep. Check go.mod for the pin.",
        oracle_files=["go.mod", "go.sum"],   # the manifest IS the oracle
        oracle_symbols=[],
    )
    assert leak.bucket is lc.Bucket.EXACT_PATH
    assert leak.admit is False

    # ...but with a real source file alongside it, the manifest is once again a
    # mere entry point, and the filter applies.
    leak = lc.classify(
        instruction="A CVE landed in a transitive dep. Check go.mod for the pin.",
        oracle_files=["go.mod", "internal/auth/verify.go"],
        oracle_symbols=[],
    )
    assert leak.bucket is not lc.Bucket.EXACT_PATH
    assert leak.admit is True


def test_dep_traversal_manifest_only_tasks_reject_on_the_real_corpus():
    """The three tasks the filter regressed, pinned against the real corpus."""
    for tid in ("dep-traversal-004", "dep-traversal-005", "dep-traversal-006"):
        hits = list(BENCHMARKS.glob(f"*/{tid}"))
        if not hits:
            continue
        r = lc.decide_task_dir(hits[0])
        assert r is not None
        assert r.bucket is lc.Bucket.EXACT_PATH, f"{tid} should leak: oracle is only manifests"
        assert r.admit is False, f"{tid} must not be admitted"
