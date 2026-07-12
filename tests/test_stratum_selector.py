"""Tests for scripts/validation/stratum_selector.py -- the Gate A-F harness.

The load-bearing property under test is NOT "does it score gates correctly".
It is: **a gate whose measurement has not been run can never be mistaken for a
gate that passed.** Gates C (turns-to-first-relevant), E (gold reward) and F
(verifier calibration) are measurements whose input artifacts do not exist on
disk. If any of them silently defaulted to pass, the selector would emit a
stratum that looks final and is not -- which is precisely the failure that
would poison every downstream arm comparison built on it.

So the tests below are weighted toward the pending-measurement contract and
the structural guarantee that guards it (`admitted_task_ids` raises rather
than returns on a non-final artifact), not toward the arithmetic.
"""

from __future__ import annotations

import json

import pytest

from scripts.validation import stratum_selector as ss


# ---------------------------------------------------------------------------
# The pending-measurement contract
# ---------------------------------------------------------------------------


def test_pending_status_is_not_a_pass():
    """The whole design rests on this: PENDING must never read as PASS."""
    assert ss.GateStatus.PASS.passed is True
    assert ss.GateStatus.FAIL.passed is False
    assert ss.GateStatus.PENDING_MEASUREMENT.passed is False


def test_pending_status_is_not_truthy():
    """Guard the `if status:` misuse -- a bare truthiness check must not pass.

    An enum member is truthy by default in Python, so without an explicit
    __bool__ a downstream `if gate.status:` would treat PENDING as a pass.
    """
    assert not ss.GateStatus.PENDING_MEASUREMENT
    assert not ss.GateStatus.FAIL
    assert ss.GateStatus.PASS


def test_every_pending_gate_names_its_input_contract():
    """A pending gate must say what measurement would resolve it.

    Without this, `pending` is indistinguishable from `we forgot`.
    """
    for gate in ("C", "E", "F"):
        contract = ss.INPUT_CONTRACTS[gate]
        assert contract.strip(), f"gate {gate} has no input contract"


def test_task_with_any_pending_gate_is_not_admitted():
    """Admission requires ALL SIX gates to pass. C/E/F are pending today, so
    no task in the real corpus can be admitted -- only a candidate."""
    result = ss.TaskGates(
        task_id="t",
        suite="s",
        gates={
            "A": ss.GateEvidence("A", ss.GateStatus.PASS, "ok"),
            "B": ss.GateEvidence("B", ss.GateStatus.PASS, "ok"),
            "C": ss.GateEvidence("C", ss.GateStatus.PENDING_MEASUREMENT, "no traces"),
            "D": ss.GateEvidence("D", ss.GateStatus.PASS, "ok"),
            "E": ss.GateEvidence("E", ss.GateStatus.PENDING_MEASUREMENT, "no gold run"),
            "F": ss.GateEvidence("F", ss.GateStatus.PENDING_MEASUREMENT, "no calibration"),
        },
    )
    assert result.is_candidate is True   # every COMPUTABLE gate passed
    assert result.admitted is False      # but it is not admitted
    assert result.is_final is False


def test_task_failing_a_computable_gate_is_not_even_a_candidate():
    result = ss.TaskGates(
        task_id="t",
        suite="s",
        gates={
            "A": ss.GateEvidence("A", ss.GateStatus.FAIL, "oracle repo not cloned"),
            "B": ss.GateEvidence("B", ss.GateStatus.PASS, "ok"),
            "C": ss.GateEvidence("C", ss.GateStatus.PENDING_MEASUREMENT, "no traces"),
            "D": ss.GateEvidence("D", ss.GateStatus.PASS, "ok"),
            "E": ss.GateEvidence("E", ss.GateStatus.PENDING_MEASUREMENT, "no gold run"),
            "F": ss.GateEvidence("F", ss.GateStatus.PENDING_MEASUREMENT, "no calibration"),
        },
    )
    assert result.is_candidate is False
    assert result.admitted is False


def test_task_is_admitted_only_when_all_six_pass():
    result = ss.TaskGates(
        task_id="t",
        suite="s",
        gates={g: ss.GateEvidence(g, ss.GateStatus.PASS, "ok")
               for g in ("A", "B", "C", "D", "E", "F")},
    )
    assert result.admitted is True
    assert result.is_final is True


# ---------------------------------------------------------------------------
# The structural guarantee: you cannot ASK a non-final artifact for its
# admitted set. A flag can be ignored; a raised exception cannot.
# ---------------------------------------------------------------------------


def _candidate_artifact() -> ss.StratumArtifact:
    tasks = [
        ss.TaskGates(
            task_id="t1",
            suite="s",
            gates={
                "A": ss.GateEvidence("A", ss.GateStatus.PASS, "ok"),
                "B": ss.GateEvidence("B", ss.GateStatus.PASS, "ok"),
                "C": ss.GateEvidence("C", ss.GateStatus.PENDING_MEASUREMENT, "no traces"),
                "D": ss.GateEvidence("D", ss.GateStatus.PASS, "ok"),
                "E": ss.GateEvidence("E", ss.GateStatus.PENDING_MEASUREMENT, "no gold run"),
                "F": ss.GateEvidence("F", ss.GateStatus.PENDING_MEASUREMENT, "no calib"),
            },
        )
    ]
    return ss.StratumArtifact(
        version="enterprise_v1",
        params=ss.SelectorParams(),
        tasks=tasks,
        source_sha="deadbeef",
    )


def test_admitted_task_ids_raises_on_non_final_artifact():
    """THE guard. A consumer that wants the admitted set out of a stratum whose
    C/E/F were never measured gets an exception, not a plausible-looking list."""
    art = _candidate_artifact()
    assert art.is_final is False
    with pytest.raises(ss.StratumNotFinalError) as exc:
        art.admitted_task_ids()
    # The error must say WHICH gates are unmeasured, or it is not actionable.
    msg = str(exc.value)
    assert "C" in msg and "E" in msg and "F" in msg


def test_candidate_task_ids_is_the_explicit_non_final_accessor():
    """Reading candidates off a non-final artifact is legitimate and allowed --
    but the caller has to name it `candidate`, which is the honest word."""
    art = _candidate_artifact()
    assert art.candidate_task_ids() == ["t1"]


def test_admitted_task_ids_works_once_all_gates_measured():
    art = ss.StratumArtifact(
        version="enterprise_v1",
        params=ss.SelectorParams(),
        tasks=[ss.TaskGates(
            task_id="t1", suite="s",
            gates={g: ss.GateEvidence(g, ss.GateStatus.PASS, "ok")
                   for g in ("A", "B", "C", "D", "E", "F")},
        )],
        source_sha="deadbeef",
    )
    assert art.is_final is True
    assert art.admitted_task_ids() == ["t1"]


def test_artifact_declares_itself_candidate_not_final():
    art = _candidate_artifact()
    d = art.to_dict()
    assert d["is_final"] is False
    assert d["stratum_status"] == "CANDIDATE"
    assert set(d["unmeasured_gates"]) == {"C", "E", "F"}


# ---------------------------------------------------------------------------
# Artifact round-trip
# ---------------------------------------------------------------------------


def test_artifact_round_trips_through_json():
    art = _candidate_artifact()
    reloaded = ss.StratumArtifact.from_dict(json.loads(json.dumps(art.to_dict())))
    assert reloaded.version == art.version
    assert reloaded.is_final is False
    assert reloaded.candidate_task_ids() == ["t1"]
    assert reloaded.tasks[0].gates["C"].status is ss.GateStatus.PENDING_MEASUREMENT
    # A round-tripped non-final artifact keeps its teeth.
    with pytest.raises(ss.StratumNotFinalError):
        reloaded.admitted_task_ids()


def test_artifact_records_params_in_provenance():
    """The pool size swings 46-59 with these two knobs, so an artifact that
    does not record which reading produced it is not auditable."""
    art = ss.StratumArtifact(
        version="enterprise_v1",
        params=ss.SelectorParams(gate_b_mode="strict", module_granularity="top_segment"),
        tasks=[],
        source_sha="abc123",
    )
    d = art.to_dict()
    assert d["params"]["gate_b_mode"] == "strict"
    assert d["params"]["module_granularity"] == "top_segment"
    assert d["source_sha"] == "abc123"


# ---------------------------------------------------------------------------
# Gate A -- baseline parity (reject empty-baseline)
# ---------------------------------------------------------------------------


def test_gate_a_fails_when_oracle_lives_in_an_undeclared_repo():
    """The 48-task confound: baseline cannot solve what it cannot see."""
    ev = ss.score_gate_a(declared_repos={"repo-a"}, oracle_repos={"repo-a", "repo-b"})
    assert ev.status is ss.GateStatus.FAIL
    assert "repo-b" in ev.reason


def test_gate_a_passes_when_baseline_clones_every_oracle_repo():
    ev = ss.score_gate_a(declared_repos={"repo-a", "repo-b"}, oracle_repos={"repo-a"})
    assert ev.status is ss.GateStatus.PASS


def test_gate_a_fails_on_empty_baseline():
    ev = ss.score_gate_a(declared_repos=set(), oracle_repos={"repo-a"})
    assert ev.status is ss.GateStatus.FAIL


# ---------------------------------------------------------------------------
# Gate D -- enterprise character: CRNT-positive, or a dispersed single repo,
# and never a calibration task.
# ---------------------------------------------------------------------------


def _d(declared, files, granularity="parent_dir", crnt=False, stratum="large_single"):
    return ss.score_gate_d(
        declared_repos=declared, oracle_files=files, granularity=granularity,
        crnt_positive=crnt, difficulty_stratum=stratum,
    )


def test_gate_d_passes_on_crnt_positive_multirepo():
    ev = _d({"a", "b"}, ["a/x.go"], crnt=True)
    assert ev.status is ss.GateStatus.PASS
    assert "CRNT" in ev.reason


def test_gate_d_multirepo_but_crnt_negative_falls_back_to_dispersion():
    """A 2-repo task where one repo has no required_files entry is NOT
    CRNT-positive (crnt_validator requires every declared repo be covered).
    Declaring two repos is not the same as needing two repos."""
    ev = _d({"a", "b"}, ["a/x.go"], crnt=False)
    assert ev.status is ss.GateStatus.FAIL


def test_gate_d_rejects_calibration_tasks():
    """H2: nothing in A-F tests SCALE. Calibration tasks are small-repo by
    design; without this, an 'enterprise-complex' stratum can seat one, and
    'enterprise' becomes a label rather than a property."""
    ev = _d({"a"}, ["svc/net/a.go", "svc/net/b.go", "svc/auth/c.go"],
            stratum="calibration")
    assert ev.status is ss.GateStatus.FAIL
    assert "calibration" in ev.reason


def test_gate_d_single_repo_needs_three_files_and_two_modules():
    # 3 files but all in ONE module -> not dispersed
    assert _d({"a"}, ["svc/net/a.go", "svc/net/b.go", "svc/net/c.go"]
              ).status is ss.GateStatus.FAIL
    # 3 files across 2 modules -> dispersed
    assert _d({"a"}, ["svc/net/a.go", "svc/net/b.go", "svc/auth/c.go"]
              ).status is ss.GateStatus.PASS


def test_gate_d_two_files_never_disperses():
    assert _d({"a"}, ["svc/net/a.go", "svc/auth/b.go"]).status is ss.GateStatus.FAIL


def test_gate_d_module_granularity_changes_the_verdict():
    """The 18-task swing, pinned. Same task, two defensible readings of
    'module': parent-dir sees 2 modules, top-segment sees 1."""
    files = ["svc/net/a.go", "svc/net/b.go", "svc/auth/c.go"]
    assert _d({"a"}, files, granularity="parent_dir").status is ss.GateStatus.PASS
    assert _d({"a"}, files, granularity="top_segment").status is ss.GateStatus.FAIL


def test_gate_d_root_level_files_have_no_parent_module():
    """H5: `path = "package.json"` at the repo root has no directory. It must
    not silently count as its own module."""
    ev = _d({"a"}, ["package.json", "go.mod", "Makefile"])
    assert ev.status is ss.GateStatus.FAIL
    assert ev.evidence["n_modules"] == 1


# ---------------------------------------------------------------------------
# Gate B -- waivers must be auditable, not asserted (M3)
# ---------------------------------------------------------------------------


def test_waiver_is_only_sound_when_a_downstream_target_exists():
    """A waived exact_path leak is defensible only if the named file is an
    ENTRY POINT and the scored discovery lies downstream. If the leaked path
    is the task's ONLY oracle file, the task is grep-trivial and the waiver is
    wrong. That is a deterministic check, so make it one."""
    assert ss.waiver_has_downstream_target(
        waived_token="svc/api.go", oracle_files=["svc/api.go", "svc/deep/impl.go"]
    ) is True
    assert ss.waiver_has_downstream_target(
        waived_token="svc/api.go", oracle_files=["svc/api.go"]
    ) is False


# ---------------------------------------------------------------------------
# Gates C / E / F -- measurements
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scorer", [ss.score_gate_c, ss.score_gate_e, ss.score_gate_f])
def test_measurement_gates_are_pending_with_no_measurement_input(scorer):
    ev = scorer(measurement=None)
    assert ev.status is ss.GateStatus.PENDING_MEASUREMENT
    assert ev.input_contract, "a pending gate must name the measurement that resolves it"


def test_gate_c_excludes_trivial_and_admits_hard():
    """Gate C's whole job: drop tasks the baseline localizes trivially."""
    ok = lambda n: {"coverage": "ok", "ttfr": n}  # noqa: E731
    assert ss.score_gate_c(measurement=ok(25)).status is ss.GateStatus.PASS   # tier-1
    assert ss.score_gate_c(measurement=ok(15)).status is ss.GateStatus.PASS   # tier-2
    assert ss.score_gate_c(measurement=ok(5)).status is ss.GateStatus.FAIL    # trivial
    assert ss.score_gate_c(measurement=ok(7)).status is ss.GateStatus.FAIL    # boundary


def test_gate_c_reports_tier():
    assert ss.score_gate_c(measurement={"coverage": "ok", "ttfr": 25}).evidence["tier"] == 1
    assert ss.score_gate_c(measurement={"coverage": "ok", "ttfr": 15}).evidence["tier"] == 2


def test_gate_c_not_found_is_maximally_hard_not_a_failure():
    """The baseline made tool calls and NEVER opened an oracle file. That is
    the strongest possible evidence of hard-to-localize -- it must not be
    confused with a trivial task. compute_ttfr's contract states not_found is
    distinct from an observability gap."""
    ev = ss.score_gate_c(measurement={"coverage": "not_found", "ttfr": None})
    assert ev.status is ss.GateStatus.PASS
    assert ev.evidence["tier"] == "unlocalized"
    assert ev.evidence["coverage"] == "not_found"


def test_gate_c_is_pending_when_the_task_has_no_baseline_trace():
    """75 of 111 corpus tasks have no baseline trace. Those are genuinely
    unmeasured -- not passes, not failures."""
    ev = ss.score_gate_c(measurement={"coverage": "no_trace", "ttfr": None})
    assert ev.status is ss.GateStatus.PENDING_MEASUREMENT
    assert ev.input_contract


def test_gate_c_partial_scoring_still_blocks_finality():
    """A gate scored for SOME tasks is not a measured gate. Finality is
    per-artifact: if any task's C is pending, the stratum is not final."""
    tasks = [
        ss.TaskGates("scored", "s", {
            **{g: ss.GateEvidence(g, ss.GateStatus.PASS, "ok") for g in ("A", "B", "D")},
            "C": ss.GateEvidence("C", ss.GateStatus.PASS, "ttfr=25"),
            "E": ss.GateEvidence("E", ss.GateStatus.PASS, "ok"),
            "F": ss.GateEvidence("F", ss.GateStatus.PASS, "ok"),
        }),
        ss.TaskGates("unscored", "s", {
            **{g: ss.GateEvidence(g, ss.GateStatus.PASS, "ok") for g in ("A", "B", "D")},
            "C": ss.GateEvidence("C", ss.GateStatus.PENDING_MEASUREMENT, "no trace"),
            "E": ss.GateEvidence("E", ss.GateStatus.PASS, "ok"),
            "F": ss.GateEvidence("F", ss.GateStatus.PASS, "ok"),
        }),
    ]
    art = ss.StratumArtifact("v", ss.SelectorParams(), tasks, "sha")
    assert art.is_final is False
    with pytest.raises(ss.StratumNotFinalError):
        art.admitted_task_ids()


def test_a_task_failing_a_MEASURED_gate_is_not_a_candidate():
    """The 17 trivial tasks. Gate C is measured for them and they FAIL it, so
    they must drop out of the candidate pool -- a pending gate keeps you in,
    a failed gate does not."""
    t = ss.TaskGates("trivial", "s", {
        **{g: ss.GateEvidence(g, ss.GateStatus.PASS, "ok") for g in ("A", "B", "D")},
        "C": ss.GateEvidence("C", ss.GateStatus.FAIL, "ttfr=3 (trivial)"),
        "E": ss.GateEvidence("E", ss.GateStatus.PENDING_MEASUREMENT, "no gold run"),
        "F": ss.GateEvidence("F", ss.GateStatus.PENDING_MEASUREMENT, "no calib"),
    })
    assert t.is_candidate is False
    assert t.admitted is False


# ---------------------------------------------------------------------------
# Unloadable tasks fail loudly (M2) -- never silently skipped
# ---------------------------------------------------------------------------


def test_unloadable_task_is_an_explicit_failure_not_a_silent_skip():
    """5 corpus tasks have no loadable oracle. leak_classifier.decide_task_dir
    returns None for them and the corpus runner `continue`s -- so they land in
    neither the admitted nor the rejected set. A task with no oracle cannot
    satisfy A, B or D; it must say so."""
    t = ss.unloadable_task_gates("broken-task", "some_suite", "no ground_truth")
    assert t.is_candidate is False
    assert t.admitted is False
    for gate in ("A", "B", "D"):
        assert t.gates[gate].status is ss.GateStatus.FAIL
        assert "oracle" in t.gates[gate].reason.lower()


def test_gate_e_floor():
    assert ss.score_gate_e(measurement={"gold_reward": 0.7}).status is ss.GateStatus.PASS
    assert ss.score_gate_e(measurement={"gold_reward": 0.3}).status is ss.GateStatus.FAIL


def test_gate_f_verifier_calibration():
    good = {"null": 0.05, "golden": 0.95, "adversarial": 0.2}
    assert ss.score_gate_f(measurement=good).status is ss.GateStatus.PASS
    # A verifier that rewards the null submission is broken, however good the
    # golden score looks.
    bad = {"null": 0.4, "golden": 0.95, "adversarial": 0.2}
    assert ss.score_gate_f(measurement=bad).status is ss.GateStatus.FAIL


# ---------------------------------------------------------------------------
# Two silent-miss bugs, pinned. Both scored WRONG without erroring, which is
# the only kind of bug that matters in a selector.
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCHMARKS = REPO_ROOT / "benchmarks"


def test_calibration_exclusion_actually_fires_on_the_real_corpus():
    """difficulty_stratum is a TOP-LEVEL key in task.toml. Reading it from
    [metadata] or [task] silently yields "" for every task, and the H2
    calibration exclusion becomes dead code that looks like it works."""
    params = ss.SelectorParams()
    scored = ss.score_task(BENCHMARKS / "customer_escalation" / "calibration-001",
                           params, runs_root=None)
    assert scored is not None
    d = scored.gates["D"]
    assert d.evidence["difficulty_stratum"] == "calibration"
    assert d.status is ss.GateStatus.FAIL
    assert scored.is_candidate is False


def test_ttfr_is_keyed_by_task_id_not_directory_name():
    """benchmarks/.../calibration-001/ holds id = "cal-err-flask-blueprint-001",
    and the locked run set is keyed by the id. Keying by directory name finds
    40 baseline traces; keying by id finds 89. The wrong key does not raise --
    it just reports `no_trace` and silently halves Gate C's coverage."""
    runs = ss.DEFAULT_RUNS_ROOT
    if not runs.is_dir():
        pytest.skip("locked run set not present")
    params = ss.SelectorParams()
    scored = ss.score_task(BENCHMARKS / "customer_escalation" / "calibration-001",
                           params, runs_root=runs)
    assert scored is not None
    assert scored.gates["C"].evidence.get("run_key") == "cal-err-flask-blueprint-001"


def test_an_absent_gate_is_unmeasured_not_final():
    """Found by self-attack: `unmeasured_gates` originally counted only PENDING
    gates, so an artifact whose tasks simply OMITTED gates C/E/F reported
    is_final=True and admitted_task_ids() did not raise.

    An absent gate is strictly worse than a pending one -- it carries no status,
    no reason and no input contract, so nothing downstream can even see it was
    never evaluated. Finality must require presence, not just absence of PENDING.
    """
    partial = ss.TaskGates("t1", "s", {
        g: ss.GateEvidence(g, ss.GateStatus.PASS, "ok") for g in ("A", "B", "D")
    })
    assert partial.missing_gates == ["C", "E", "F"]
    assert partial.is_final is False
    assert partial.admitted is False

    art = ss.StratumArtifact("v", ss.SelectorParams(), [partial], "sha")
    assert art.unmeasured_gates == ["C", "E", "F"]
    assert art.is_final is False
    assert art.to_dict()["stratum_status"] == "CANDIDATE"
    with pytest.raises(ss.StratumNotFinalError):
        art.admitted_task_ids()


def test_gate_a_keys_declared_repos_on_path_not_url_basename():
    """The bug that falsely rejected 6 tasks. [[repos]] defines `path` (the
    directory the sandbox clones INTO, and what ground_truth's `repo` tag refers
    to), NOT `name`. Keying on the URL basename made docker/cli -> "cli" while
    the oracle said "docker-cli", so Gate A reported a repo missing that was in
    fact cloned."""
    toml = {"repos": [{"path": "docker-cli", "url": "https://github.com/docker/cli"},
                      {"path": "moby", "url": "https://github.com/moby/moby"}]}
    assert ss._declared_repos(toml) == {"docker-cli", "moby"}
    ev = ss.score_gate_a(ss._declared_repos(toml), {"docker-cli", "moby"})
    assert ev.status is ss.GateStatus.PASS


def test_gate_a_does_not_pass_vacuously_when_the_oracle_has_no_repo_tags():
    """`missing = {} - declared` is empty, so an oracle with no `repo` tags would
    otherwise PASS a parity gate that verified nothing. Uncheckable != fine."""
    ev = ss.score_gate_a({"repo-a"}, set(), has_oracle_files=True)
    assert ev.status is ss.GateStatus.FAIL
    assert "cannot be verified" in ev.reason
