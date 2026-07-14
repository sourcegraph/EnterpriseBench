"""Authoring invariants for the enterprise-hard authored stratum (jn73.2.7).

These tests assert what MUST be true of every task authored into
``benchmarks/strata/enterprise_hard_authored.json`` — the tasks written because
the existing corpus could not reach >=50 enterprise-win-regime tasks by
selection. They guard against the two failure modes the bead names: an agent
quietly relaxing hardness to hit a count, and authored-not-measured being read as
"Gate C passed".

The manifest is the artifact under test. If it is absent (no tasks authored yet),
every test skips rather than fails — the stratum simply does not exist until the
authoring bead produces it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.validation.build_enterprise_hard_manifest import (
    GATE_C_TIER1_MIN_TURNS,
    GATE_D_MIN_FILES,
    GATE_D_MIN_MODULES,
    NON_GROUNDING_ARTIFACTS,
    build_entry,
    score_gate_b,
    score_gate_d,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = PROJECT_ROOT / "benchmarks" / "strata" / "enterprise_hard_authored.json"
COMPUTABLE_GATES = ("A", "B", "D")


def _load_manifest() -> dict:
    if not MANIFEST.is_file():
        pytest.skip(f"no authored-stratum manifest yet at {MANIFEST}")
    return json.loads(MANIFEST.read_text())


def _tasks() -> list[dict]:
    return _load_manifest().get("tasks", [])


def _task_ids() -> list[str]:
    return [t["task_id"] for t in _tasks()]


# ── manifest-level shape ──────────────────────────────────────────


def test_manifest_parses_and_is_nonempty():
    tasks = _tasks()
    assert tasks, "manifest exists but authored zero tasks"


def test_manifest_counts_match_task_list():
    m = _load_manifest()
    tasks = m["tasks"]
    counts = m["counts"]
    assert counts["authored"] == len(tasks)
    assert counts["requiring_code_patch"] == sum(
        1 for t in tasks if t["requires_code_patch"]
    )
    clean = [
        t
        for t in tasks
        if all(t["gates"][g]["status"] == "pass" for g in COMPUTABLE_GATES)
        and not t["grounding_artifact_conflict"]
    ]
    assert counts["passing_computable_gates"] == len(clean)


def test_task_ids_unique():
    ids = _task_ids()
    assert len(ids) == len(set(ids)), "duplicate task_id in manifest"


def test_manifest_entries_match_regenerated():
    """The stored manifest must equal a fresh build from the task dirs on disk.

    Catches a manifest edited by hand out of sync with the task.toml it claims
    to describe — the gate evidence must be reproducible, not asserted.
    """
    for entry in _tasks():
        task_dir = PROJECT_ROOT / entry["path"]
        assert (task_dir / "task.toml").is_file(), (
            f"{entry['task_id']}: manifest path {entry['path']} has no task.toml"
        )
        assert build_entry(task_dir) == entry, (
            f"{entry['task_id']}: stored entry is stale vs task.toml on disk"
        )


# ── per-task authoring invariants ─────────────────────────────────


def test_every_task_is_multi_repo():
    for t in _tasks():
        assert len(t["repos"]) >= 2, f"{t['task_id']}: <2 repos, not multi-repo"


def test_every_task_is_crnt_positive():
    """Every declared repo is load-bearing (covered by required_files)."""
    for t in _tasks():
        assert t["crnt_positive"], (
            f"{t['task_id']}: CRNT-negative — a declared repo is decorative"
        )


def test_oracle_dispersed_across_files_and_modules():
    """>=3 required files across >=2 modules — Gate D's dispersion floor."""
    for t in _tasks():
        req = t["required_files"]
        assert len(req) >= GATE_D_MIN_FILES, (
            f"{t['task_id']}: {len(req)} required files, need >={GATE_D_MIN_FILES}"
        )
        d_ev = t["gates"]["D"]["evidence"]
        assert d_ev["n_modules"] >= GATE_D_MIN_MODULES, (
            f"{t['task_id']}: oracle spans {d_ev['n_modules']} modules, "
            f"need >={GATE_D_MIN_MODULES}"
        )


def test_oracle_spans_at_least_two_repos():
    for t in _tasks():
        repos = {rf["repo"] for rf in t["required_files"] if rf["repo"]}
        assert len(repos) >= 2, (
            f"{t['task_id']}: required files touch {len(repos)} repo(s), the root "
            f"cause is not cross-repo"
        )


def test_computable_gates_pass():
    """A/B/D are properties of the task and must hold at authoring time.

    Gate B's evidence is re-derived from instruction.md on disk by
    test_manifest_entries_match_regenerated, so asserting on it here is an
    assertion about the source, not about a stored string.
    """
    for t in _tasks():
        for g in COMPUTABLE_GATES:
            assert t["gates"][g]["status"] == "pass", (
                f"{t['task_id']}: Gate {g} {t['gates'][g]['status']} — "
                f"{t['gates'][g]['reason']}"
            )
        assert t["gates"]["B"]["evidence"]["bucket"] in ("behavioral", "grep_hard"), (
            f"{t['task_id']}: instruction leaks the oracle"
        )


# ── Gate C must never be asserted as passed ───────────────────────


def test_gate_c_is_always_pending():
    """Designed-tier-1 is not measured-tier-1. Gate C is a measurement, and no
    baseline run exists for these tasks, so it must be pending for every one."""
    for t in _tasks():
        c = t["gates"]["C"]
        assert c["status"] == "pending_measurement", (
            f"{t['task_id']}: Gate C is {c['status']!r} — an authored task may "
            f"never claim Gate C passed"
        )
        assert c["evidence"]["designed_for"] == "tier_1"
        assert c["evidence"]["tier1_min_turns"] == GATE_C_TIER1_MIN_TURNS


def test_gate_c_records_measurement_contract():
    """Pending is only actionable if it says what would measure it."""
    for t in _tasks():
        contract = t["gates"]["C"]["evidence"]["input_contract"]
        for key in ("needs", "scorer", "admits_if"):
            assert contract.get(key), f"{t['task_id']}: Gate C contract missing {key}"


# ── artifact / grounding consistency ──────────────────────────────


def test_no_code_patch_grounding_conflict():
    """code_patch's validator has no require_grounded_citations kwarg, so a task
    that requires code_patch must not set the flag — the runner would hard-fail
    the artifact otherwise."""
    for t in _tasks():
        assert not t["grounding_artifact_conflict"], (
            f"{t['task_id']}: require_grounded_citations set alongside a "
            f"non-grounding artifact ({NON_GROUNDING_ARTIFACTS & set(t['artifacts_required'])})"
        )


def test_code_patch_tasks_have_answer_too():
    """A code_patch task still needs a retrievable-answer surface for scoring."""
    for t in _tasks():
        if t["requires_code_patch"]:
            assert "answer" in t["artifacts_required"], (
                f"{t['task_id']}: code_patch task has no 'answer' artifact"
            )


# ── unit coverage of the gate scorers themselves ──────────────────


def test_gate_b_flags_basename_leak():
    gate = score_gate_b(
        "The bug is in default.py, look there.",
        ["httpx/_transports/default.py"],
        {"httpx"},
    )
    assert gate.status == "fail"
    assert gate.evidence["bucket"] == "exact_path"


def test_gate_b_admits_symptom_only():
    gate = score_gate_b(
        "Requests intermittently fail with 'peer closed connection' behind a proxy.",
        ["httpx/_transports/default.py", "httpcore/_async/http11.py"],
        {"httpx", "httpcore"},
    )
    assert gate.status == "pass"
    assert gate.evidence["bucket"] == "grep_hard"


def test_gate_b_two_files_one_repo_is_behavioral():
    """The repo set comes from required_files[].repo, not the path prefix."""
    gate = score_gate_b(
        "Uploads intermittently truncate under load.",
        ["src/codec/framed_read.rs", "src/proto/h1/decode.rs"],
        {"h2"},
    )
    assert gate.status == "pass"
    assert gate.evidence["bucket"] == "behavioral"


def test_gate_d_rejects_single_module():
    gate = score_gate_d(
        {"repo_a", "repo_b"},
        ["a/x.py", "a/y.py", "a/z.py"],
        crnt_positive=True,
        stratum="dual_repo",
    )
    assert gate.status == "fail"


def test_gate_d_rejects_calibration():
    gate = score_gate_d(
        {"repo_a", "repo_b"},
        ["a/x.py", "b/y.py", "c/z.py"],
        crnt_positive=True,
        stratum="calibration",
    )
    assert gate.status == "fail"
