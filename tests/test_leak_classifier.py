"""Tests for scripts/validation/leak_classifier.py -- task-authoring Gate B.

Covers: bucket-classification parity with the CSB reference's own
--selftest cases, the EB task.toml [ground_truth] loader (replacing the
CSB-shipped adapter's broken expected_solution.json{"files"} reader), the
qa.waivers admit path (Option B, EnterpriseBench-jn73.3), and a real-corpus
run producing the leak-bucket distribution.
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


def test_selftest_passes():
    assert lc._selftest() == 0


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
# Gate B decision: Option B waiver-admit path
# ---------------------------------------------------------------------------


def test_exact_path_leak_admits_when_waived(tmp_path):
    task_dir = _write_task(
        tmp_path,
        instruction="The producer entry point is `metadata/metadata.go`; find every "
        "consumer of the old API in the etcd codebase.",
        required_files=[{"path": "metadata/metadata.go", "repo": "grpc-go"}],
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
    assert result.admit is True
    assert result.waived is True


def test_exact_path_leak_rejects_when_not_waived(tmp_path):
    """Synthetic non-waived exact-path task -> REJECT (jn73.3 acceptance
    criterion)."""
    task_dir = _write_task(
        tmp_path,
        instruction="Fix the bug in `metadata/metadata.go`.",
        required_files=[{"path": "metadata/metadata.go", "repo": "grpc-go"}],
    )
    result = lc.decide_task_dir(task_dir)
    assert result is not None
    assert result.bucket is lc.Bucket.EXACT_PATH
    assert result.admit is False
    assert result.waived is False


def test_waiver_for_different_token_does_not_admit(tmp_path):
    """Token matching is exact -- a waiver recorded for one path must not
    suppress a different path's leak (mirrors qa_adapter's exact-match
    guarantee: 'a waiver for one path cannot suppress a different path's
    F2')."""
    task_dir = _write_task(
        tmp_path,
        instruction="Fix the bug in `metadata/metadata.go`.",
        required_files=[{"path": "metadata/metadata.go", "repo": "grpc-go"}],
        waivers=[{
            "code": "F2",
            "token": "other/file.go",
            "decision": "waive",
            "rationale": "unrelated waiver, must not match",
        }],
    )
    result = lc.decide_task_dir(task_dir)
    assert result is not None
    assert result.admit is False
    assert result.waived is False


def test_dir_bucket_not_waiver_admissible(tmp_path):
    """Per Option B, only EXACT_PATH is waiver-admissible -- DIR/SYMBOL always
    reject even if the matching token happens to appear in qa.waivers (a
    waiver entry for the wrong bucket must not paper over a dir-level leak)."""
    task_dir = _write_task(
        tmp_path,
        instruction="Look under escalation/ for the handler that drops context.",
        required_files=[{"path": "escalation/handler.go", "repo": "svc"}],
        waivers=[{
            "code": "F2",
            "token": "escalation",
            "decision": "waive",
            "rationale": "invalid attempt to waive a dir-bucket token",
        }],
    )
    result = lc.decide_task_dir(task_dir)
    assert result is not None
    assert result.bucket is lc.Bucket.DIR
    assert result.admit is False


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
    assert result.waived is False


# ---------------------------------------------------------------------------
# Real-corpus integration: the F2-waived tasks (EnterpriseBench-1is,
# docs/qa/eb_f2_triage_2026_06_04.md)
# ---------------------------------------------------------------------------

# The triage doc enumerates 12 tasks / 14 F2 tokens. One of them --
# incident_response/incident-investigation-dual-nats-001 -- does not exist
# anywhere in benchmarks/ on `main` OR on `fix/eb-f2-leak-triage` (confirmed
# via `git ls-tree -r`); it was apparently never created or was removed
# pre-merge. Only the 11 present tasks are checkable here.
F2_WAIVED_TASK_DIRS = [
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

# Of those 11, 3 carry a SECOND, un-triaged locational leak elsewhere in the
# same task's oracle files, distinct from the token F2 actually waived:
#   - schema-evolution-002: basename `deferred_work.py` (a required_files
#     entry) is named verbatim in instruction.md ("A background worker in
#     `deferred_work.py`") -- F2's check only matches a full recorded path
#     verbatim, so a bare basename leak was never caught/triaged.
#   - schema-evolution-004: basenames `realms.py` AND `users.py` (two of the
#     three required_files) are both named verbatim ("`realms.py` for the
#     addition, `users.py` for the removal").
#   - rbac-audit-001: instruction.md literally says "hint: look in
#     `apiserver/pkg/registry/projectcalico/` for ..." -- a directory-form
#     hint naming the parent of 4 of the 5 oracle files.
# Gate B's classify() reports one bucket per task (first oracle-file hit, in
# required_files-then-sufficient_files order); for these 3 that first hit is
# the un-triaged leak, not the waived one, so they correctly REJECT under the
# decided Option B policy. This is a real, additional leak that predates this
# port -- not a bug in it -- and needs its own triage pass, not a silent
# widening of the waiver-admit rule to force these through.
KNOWN_ADDITIONAL_UNWAIVED_LEAK = {
    "feature_delivery/schema-evolution-002",
    "feature_delivery/schema-evolution-004",
    "security_operations/rbac-audit-001",
}


@pytest.mark.parametrize("rel_dir", F2_WAIVED_TASK_DIRS)
def test_f2_waived_tasks_gate_b_verdict(rel_dir):
    task_dir = BENCHMARKS / rel_dir
    result = lc.decide_task_dir(task_dir)
    assert result is not None, f"could not load {rel_dir}"
    if rel_dir in KNOWN_ADDITIONAL_UNWAIVED_LEAK:
        assert result.admit is False
        assert result.waived is False
    else:
        assert result.admit is True, f"{rel_dir}: {result.reason}"
        assert result.waived is True


def test_config_drift_001_generic_basename_admits_via_waiver():
    """config-drift-001's leak token is the generic basename `values.yaml` --
    the false-positive class flagged in jn73.3's verification checklist. It
    IS a recorded qa.waivers entry, so it must ADMIT; Gate B does not
    special-case generic basenames on its own (that would be a silent scope
    change -- see the corpus report for the broader go.mod/package.json/
    values.yaml false-positive count across un-waived tasks)."""
    task_dir = BENCHMARKS / "platform_engineering" / "config-drift-001"
    result = lc.decide_task_dir(task_dir)
    assert result is not None
    assert result.token == "values.yaml"
    assert result.admit is True
    assert result.waived is True


# ---------------------------------------------------------------------------
# Corpus-wide leak-bucket distribution
# ---------------------------------------------------------------------------


def test_corpus_run_produces_bucket_distribution(capsys):
    lc._run_corpus(BENCHMARKS, as_json=True)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["total"] > 0
    assert set(payload["bucket_distribution"]) <= {b.value for b in lc.Bucket}
    # 8 of the 11 present F2-waived tasks admit cleanly via the waiver path
    # (the other 3 are the KNOWN_ADDITIONAL_UNWAIVED_LEAK set above).
    waived_ids = {w["task_id"] for w in payload["waived_tasks"]}
    assert len(waived_ids) >= 8
