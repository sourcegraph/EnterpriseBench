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
from pathlib import Path

import pytest

from scripts.validation import leak_classifier as lc
from scripts.validation import stratum_gates as sg
from scripts.validation import stratum_model as sm
from scripts.validation import stratum_selector as ss

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCHMARKS = REPO_ROOT / "benchmarks"


# ---------------------------------------------------------------------------
# The pending-measurement contract
# ---------------------------------------------------------------------------


def test_pending_status_is_not_a_pass():
    """The whole design rests on this: PENDING must never read as PASS."""
    assert sm.GateStatus.PASS.passed is True
    assert sm.GateStatus.FAIL.passed is False
    assert sm.GateStatus.PENDING_MEASUREMENT.passed is False


def test_pending_status_is_not_truthy():
    """Guard the `if status:` misuse -- a bare truthiness check must not pass.

    An enum member is truthy by default in Python, so without an explicit
    __bool__ a downstream `if gate.status:` would treat PENDING as a pass.
    """
    assert not sm.GateStatus.PENDING_MEASUREMENT
    assert not sm.GateStatus.FAIL
    assert sm.GateStatus.PASS


def test_every_pending_gate_names_its_input_contract():
    """A pending gate must say what measurement would resolve it.

    Without this, `pending` is indistinguishable from `we forgot`.
    """
    for gate in ("C", "E", "F"):
        contract = sm.INPUT_CONTRACTS[gate]
        assert contract.strip(), f"gate {gate} has no input contract"


def test_task_with_any_pending_gate_is_not_admitted():
    """Admission requires ALL SIX gates to pass. C/E/F are pending today, so
    no task in the real corpus can be admitted -- only a candidate."""
    result = sm.TaskGates(
        task_id="t",
        suite="s",
        gates={
            "A": sm.GateEvidence("A", sm.GateStatus.PASS, "ok"),
            "B": sm.GateEvidence("B", sm.GateStatus.PASS, "ok"),
            "C": sm.GateEvidence("C", sm.GateStatus.PENDING_MEASUREMENT, "no traces"),
            "D": sm.GateEvidence("D", sm.GateStatus.PASS, "ok"),
            "E": sm.GateEvidence("E", sm.GateStatus.PENDING_MEASUREMENT, "no gold run"),
            "F": sm.GateEvidence("F", sm.GateStatus.PENDING_MEASUREMENT, "no calibration"),
        },
    )
    assert result.is_candidate is True   # every COMPUTABLE gate passed
    assert result.admitted is False      # but it is not admitted
    assert result.is_final is False


def test_task_failing_a_computable_gate_is_not_even_a_candidate():
    result = sm.TaskGates(
        task_id="t",
        suite="s",
        gates={
            "A": sm.GateEvidence("A", sm.GateStatus.FAIL, "oracle repo not cloned"),
            "B": sm.GateEvidence("B", sm.GateStatus.PASS, "ok"),
            "C": sm.GateEvidence("C", sm.GateStatus.PENDING_MEASUREMENT, "no traces"),
            "D": sm.GateEvidence("D", sm.GateStatus.PASS, "ok"),
            "E": sm.GateEvidence("E", sm.GateStatus.PENDING_MEASUREMENT, "no gold run"),
            "F": sm.GateEvidence("F", sm.GateStatus.PENDING_MEASUREMENT, "no calibration"),
        },
    )
    assert result.is_candidate is False
    assert result.admitted is False


def test_task_is_admitted_only_when_all_six_pass():
    result = sm.TaskGates(
        task_id="t",
        suite="s",
        gates={g: sm.GateEvidence(g, sm.GateStatus.PASS, "ok")
               for g in ("A", "B", "C", "D", "E", "F")},
    )
    assert result.admitted is True
    assert result.is_final is True


def _prov(**kw) -> sm.Provenance:
    base = dict(source_sha="deadbeef", source_dirty=False, corpus_root="benchmarks",
                runs_root="/runs")
    base.update(kw)
    return sm.Provenance(**base)


# ---------------------------------------------------------------------------
# The structural guarantee: you cannot ASK a non-final artifact for its
# admitted set. A flag can be ignored; a raised exception cannot.
# ---------------------------------------------------------------------------


def _candidate_artifact() -> sm.StratumArtifact:
    tasks = [
        sm.TaskGates(
            task_id="t1",
            suite="s",
            gates={
                "A": sm.GateEvidence("A", sm.GateStatus.PASS, "ok"),
                "B": sm.GateEvidence("B", sm.GateStatus.PASS, "ok"),
                "C": sm.GateEvidence("C", sm.GateStatus.PENDING_MEASUREMENT, "no traces"),
                "D": sm.GateEvidence("D", sm.GateStatus.PASS, "ok"),
                "E": sm.GateEvidence("E", sm.GateStatus.PENDING_MEASUREMENT, "no gold run"),
                "F": sm.GateEvidence("F", sm.GateStatus.PENDING_MEASUREMENT, "no calib"),
            },
        )
    ]
    return sm.StratumArtifact(
        version="enterprise_v1",
        params=sm.SelectorParams(),
        tasks=tasks,
        provenance=_prov(),
    )


def test_admitted_task_ids_raises_on_non_final_artifact():
    """THE guard. A consumer that wants the admitted set out of a stratum whose
    C/E/F were never measured gets an exception, not a plausible-looking list."""
    art = _candidate_artifact()
    assert art.is_final is False
    with pytest.raises(sm.StratumNotFinalError) as exc:
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
    art = sm.StratumArtifact(
        version="enterprise_v1",
        params=sm.SelectorParams(),
        tasks=[sm.TaskGates(
            task_id="t1", suite="s",
            gates={g: sm.GateEvidence(g, sm.GateStatus.PASS, "ok")
                   for g in ("A", "B", "C", "D", "E", "F")},
        )],
        provenance=_prov(),
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
    reloaded = sm.StratumArtifact.from_dict(json.loads(json.dumps(art.to_dict())))
    assert reloaded.version == art.version
    assert reloaded.is_final is False
    assert reloaded.candidate_task_ids() == ["t1"]
    assert reloaded.tasks[0].gates["C"].status is sm.GateStatus.PENDING_MEASUREMENT
    # A round-tripped non-final artifact keeps its teeth.
    with pytest.raises(sm.StratumNotFinalError):
        reloaded.admitted_task_ids()


def test_artifact_records_its_inputs_not_just_its_knobs():
    """An artifact that records only the knobs is not reproducible, and that gap
    shipped a real defect: a run whose runs_root did not exist scored Gate C as
    PENDING for every task, dropped its 49 FAILs, and emitted a 46-candidate pool
    whose params/version/source_sha were byte-identical to the correct 35.

    So the INPUTS are pinned too: which corpus, which run set, which thresholds,
    how many traces actually resolved, and a hash of what those traces SAID."""
    art = sm.StratumArtifact(
        version="enterprise_v1",
        params=sm.SelectorParams(module_granularity="top_segment"),
        tasks=[],
        provenance=_prov(source_sha="abc123", corpus_root="benchmarks",
                         runs_root="/locked/runs",
                         gate_thresholds=dict(sm.GATE_THRESHOLDS),
                         trace_coverage={"ok": 60, "not_found": 23, "no_trace": 33},
                         traces_digest="f00dcafe"),
    )
    d = art.to_dict()
    assert d["params"]["module_granularity"] == "top_segment"
    p = d["provenance"]
    assert p["source_sha"] == "abc123"
    assert p["source_dirty"] is False
    assert p["corpus_root"] == "benchmarks"
    assert p["runs_root"] == "/locked/runs"
    assert p["trace_coverage"] == {"ok": 60, "not_found": 23, "no_trace": 33}
    assert p["traces_digest"] == "f00dcafe"
    # Every constant that can move the pool, not just Gate C's.
    assert p["gate_thresholds"]["gate_c_trivial_max"] == 7
    assert p["gate_thresholds"]["gate_d_min_files"] == 3
    # ...and it survives a round-trip, or a reader cannot check any of it.
    assert sm.StratumArtifact.from_dict(json.loads(json.dumps(d))).provenance == \
        art.provenance


# ---------------------------------------------------------------------------
# Gate A -- baseline parity (reject empty-baseline)
# ---------------------------------------------------------------------------


def test_gate_a_fails_when_oracle_lives_in_an_undeclared_repo():
    """The 48-task confound: baseline cannot solve what it cannot see."""
    ev = sg.score_gate_a(declared_repos={"repo-a"}, oracle_repos={"repo-a", "repo-b"})
    assert ev.status is sm.GateStatus.FAIL
    assert "repo-b" in ev.reason


def test_gate_a_passes_when_baseline_clones_every_oracle_repo():
    ev = sg.score_gate_a(declared_repos={"repo-a", "repo-b"}, oracle_repos={"repo-a"})
    assert ev.status is sm.GateStatus.PASS


def test_gate_a_fails_on_empty_baseline():
    ev = sg.score_gate_a(declared_repos=set(), oracle_repos={"repo-a"})
    assert ev.status is sm.GateStatus.FAIL


# ---------------------------------------------------------------------------
# Gate D -- enterprise character: CRNT-positive, or a dispersed single repo,
# and never a calibration task.
# ---------------------------------------------------------------------------


def _d(declared, files, granularity="parent_dir", crnt=False, stratum="large_single"):
    return sg.score_gate_d(
        declared_repos=declared, oracle_files=files, granularity=granularity,
        crnt_positive=crnt, difficulty_stratum=stratum,
    )


def test_gate_d_passes_on_crnt_positive_multirepo():
    ev = _d({"a", "b"}, ["a/x.go"], crnt=True)
    assert ev.status is sm.GateStatus.PASS
    assert "CRNT" in ev.reason


def test_gate_d_multirepo_but_crnt_negative_falls_back_to_dispersion():
    """A 2-repo task where one repo has no required_files entry is NOT
    CRNT-positive (crnt_validator requires every declared repo be covered).
    Declaring two repos is not the same as needing two repos."""
    ev = _d({"a", "b"}, ["a/x.go"], crnt=False)
    assert ev.status is sm.GateStatus.FAIL


def test_gate_d_rejects_calibration_tasks():
    """H2: nothing in A-F tests SCALE. Calibration tasks are small-repo by
    design; without this, an 'enterprise-complex' stratum can seat one, and
    'enterprise' becomes a label rather than a property."""
    ev = _d({"a"}, ["svc/net/a.go", "svc/net/b.go", "svc/auth/c.go"],
            stratum="calibration")
    assert ev.status is sm.GateStatus.FAIL
    assert "calibration" in ev.reason


def test_gate_d_single_repo_needs_three_files_and_two_modules():
    # 3 files but all in ONE module -> not dispersed
    assert _d({"a"}, ["svc/net/a.go", "svc/net/b.go", "svc/net/c.go"]
              ).status is sm.GateStatus.FAIL
    # 3 files across 2 modules -> dispersed
    assert _d({"a"}, ["svc/net/a.go", "svc/net/b.go", "svc/auth/c.go"]
              ).status is sm.GateStatus.PASS


def test_gate_d_two_files_never_disperses():
    assert _d({"a"}, ["svc/net/a.go", "svc/auth/b.go"]).status is sm.GateStatus.FAIL


def test_gate_d_module_granularity_changes_the_verdict():
    """The 18-task swing, pinned. Same task, two defensible readings of
    'module': parent-dir sees 2 modules, top-segment sees 1."""
    files = ["svc/net/a.go", "svc/net/b.go", "svc/auth/c.go"]
    assert _d({"a"}, files, granularity="parent_dir").status is sm.GateStatus.PASS
    assert _d({"a"}, files, granularity="top_segment").status is sm.GateStatus.FAIL


def test_gate_d_root_level_files_have_no_parent_module():
    """H5: `path = "package.json"` at the repo root has no directory. It must
    not silently count as its own module."""
    ev = _d({"a"}, ["package.json", "go.mod", "Makefile"])
    assert ev.status is sm.GateStatus.FAIL
    assert ev.evidence["n_modules"] == 1


# ---------------------------------------------------------------------------
# Gate B -- one rule, one implementation
# ---------------------------------------------------------------------------


def test_gate_b_reports_decide_rather_than_re_deriving_it():
    """The two-sources-of-truth defect, pinned. `make verify-leak` reported 69
    admitted while the artifact reported Gate B pass=61 over the same corpus and
    the same policy -- because the selector re-litigated the verdict instead of
    reporting it. An author who ran the documented command was told ADMITTED
    while the selector said REJECTED.

    Gate B now reports `decide()`, so the two cannot drift. Asserting agreement
    over the WHOLE corpus (not a fixture) is what makes that structural."""
    disagreements = []
    for task_dir in sorted(BENCHMARKS.glob("*/*/task.toml")):
        oracle = lc.load_task(task_dir.parent)
        if oracle is None:
            continue
        verdict = lc.decide(task_dir.parent.name, oracle)
        gate = sg.score_gate_b(verdict)
        if gate.status.passed is not verdict.admit:
            disagreements.append(task_dir.parent.name)
    assert not disagreements, (
        f"selector Gate B disagrees with leak_classifier.decide() on "
        f"{len(disagreements)} tasks: {disagreements[:5]}")


def test_gate_b_fails_a_task_whose_oracle_cannot_be_loaded():
    """Uncheckable is not the same as fine."""
    ev = sg.score_gate_b(None)
    assert ev.status is sm.GateStatus.FAIL


# ---------------------------------------------------------------------------
# Gates C / E / F -- measurements
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scorer", [sg.score_gate_c, sg.score_gate_e, sg.score_gate_f])
def test_measurement_gates_are_pending_with_no_measurement_input(scorer):
    ev = scorer(measurement=None)
    assert ev.status is sm.GateStatus.PENDING_MEASUREMENT
    assert ev.input_contract, "a pending gate must name the measurement that resolves it"


def test_gate_c_excludes_trivial_and_admits_hard():
    """Gate C's whole job: drop tasks the baseline localizes trivially."""
    ok = lambda n: {"coverage": "ok", "ttfr": n}  # noqa: E731
    assert sg.score_gate_c(measurement=ok(25)).status is sm.GateStatus.PASS   # tier-1
    assert sg.score_gate_c(measurement=ok(15)).status is sm.GateStatus.PASS   # tier-2
    assert sg.score_gate_c(measurement=ok(5)).status is sm.GateStatus.FAIL    # trivial
    assert sg.score_gate_c(measurement=ok(7)).status is sm.GateStatus.FAIL    # boundary


def test_gate_c_reports_tier():
    assert sg.score_gate_c(measurement={"coverage": "ok", "ttfr": 25}).evidence["tier"] == 1
    assert sg.score_gate_c(measurement={"coverage": "ok", "ttfr": 15}).evidence["tier"] == 2


# NOTE: the former test_gate_c_not_found_is_maximally_hard_not_a_failure asserted
# that coverage="not_found" PASSES Gate C. That ruling was refuted by the traces
# and has been removed -- see test_gate_c_not_found_is_pending_not_a_pass below.


def test_gate_c_is_pending_when_the_task_has_no_baseline_trace():
    """75 of 111 corpus tasks have no baseline trace. Those are genuinely
    unmeasured -- not passes, not failures."""
    ev = sg.score_gate_c(measurement={"coverage": "no_trace", "ttfr": None})
    assert ev.status is sm.GateStatus.PENDING_MEASUREMENT
    assert ev.input_contract


def test_gate_c_partial_scoring_still_blocks_finality():
    """A gate scored for SOME tasks is not a measured gate. Finality is
    per-artifact: if any task's C is pending, the stratum is not final."""
    tasks = [
        sm.TaskGates("scored", "s", {
            **{g: sm.GateEvidence(g, sm.GateStatus.PASS, "ok") for g in ("A", "B", "D")},
            "C": sm.GateEvidence("C", sm.GateStatus.PASS, "ttfr=25"),
            "E": sm.GateEvidence("E", sm.GateStatus.PASS, "ok"),
            "F": sm.GateEvidence("F", sm.GateStatus.PASS, "ok"),
        }),
        sm.TaskGates("unscored", "s", {
            **{g: sm.GateEvidence(g, sm.GateStatus.PASS, "ok") for g in ("A", "B", "D")},
            "C": sm.GateEvidence("C", sm.GateStatus.PENDING_MEASUREMENT, "no trace"),
            "E": sm.GateEvidence("E", sm.GateStatus.PASS, "ok"),
            "F": sm.GateEvidence("F", sm.GateStatus.PASS, "ok"),
        }),
    ]
    art = sm.StratumArtifact("v", sm.SelectorParams(), tasks, _prov())
    assert art.is_final is False
    with pytest.raises(sm.StratumNotFinalError):
        art.admitted_task_ids()


def test_a_task_failing_a_MEASURED_gate_is_not_a_candidate():
    """The 17 trivial tasks. Gate C is measured for them and they FAIL it, so
    they must drop out of the candidate pool -- a pending gate keeps you in,
    a failed gate does not."""
    t = sm.TaskGates("trivial", "s", {
        **{g: sm.GateEvidence(g, sm.GateStatus.PASS, "ok") for g in ("A", "B", "D")},
        "C": sm.GateEvidence("C", sm.GateStatus.FAIL, "ttfr=3 (trivial)"),
        "E": sm.GateEvidence("E", sm.GateStatus.PENDING_MEASUREMENT, "no gold run"),
        "F": sm.GateEvidence("F", sm.GateStatus.PENDING_MEASUREMENT, "no calib"),
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
    t = sg.unloadable_task_gates("broken-task", "some_suite", "no ground_truth")
    assert t.is_candidate is False
    assert t.admitted is False
    for gate in ("A", "B", "D"):
        assert t.gates[gate].status is sm.GateStatus.FAIL
        assert "oracle" in t.gates[gate].reason.lower()


def test_gate_e_floor():
    assert sg.score_gate_e(measurement={"gold_reward": 0.7}).status is sm.GateStatus.PASS
    assert sg.score_gate_e(measurement={"gold_reward": 0.3}).status is sm.GateStatus.FAIL


def test_gate_f_verifier_calibration():
    good = {"null": 0.05, "golden": 0.95, "adversarial": 0.2}
    assert sg.score_gate_f(measurement=good).status is sm.GateStatus.PASS
    # A verifier that rewards the null submission is broken, however good the
    # golden score looks.
    bad = {"null": 0.4, "golden": 0.95, "adversarial": 0.2}
    assert sg.score_gate_f(measurement=bad).status is sm.GateStatus.FAIL


# ---------------------------------------------------------------------------
# Two silent-miss bugs, pinned. Both scored WRONG without erroring, which is
# the only kind of bug that matters in a selector.
# ---------------------------------------------------------------------------


def test_calibration_exclusion_actually_fires_on_the_real_corpus():
    """difficulty_stratum is a TOP-LEVEL key in task.toml. Reading it from
    [metadata] or [task] silently yields "" for every task, and the H2
    calibration exclusion becomes dead code that looks like it works."""
    params = sm.SelectorParams()
    scored = sg.score_task(BENCHMARKS / "customer_escalation" / "calibration-001",
                           params, runs_root=None)
    assert scored is not None
    d = scored.gates["D"]
    assert d.evidence["difficulty_stratum"] == "calibration"
    assert d.status is sm.GateStatus.FAIL
    assert scored.is_candidate is False


def _fake_runs_root(tmp_path, run_key: str, lines: list[str]) -> Path:
    """A minimal locked-run-set tree: <runs>/<task-id>/baseline/agent_trace.jsonl.

    Built rather than borrowed. The real run set lives outside the repo on one
    machine, and a test that reaches for it is a test that passes or skips
    depending on whose laptop it runs on -- which is the same class of defect as
    the hardcoded DEFAULT_RUNS_ROOT this replaced.
    """
    trace = tmp_path / run_key / "baseline" / "agent_trace.jsonl"
    trace.parent.mkdir(parents=True)
    trace.write_text("\n".join(lines) + "\n")
    return tmp_path


def _read_tool_call(path: str) -> str:
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Read", "input": {"file_path": path}}]}})


def test_ttfr_is_keyed_by_task_id_not_directory_name(tmp_path):
    """benchmarks/.../calibration-001/ holds id = "cal-err-flask-blueprint-001",
    and the locked run set is keyed by the id, NOT the directory name. Keying by
    directory finds 40 baseline traces; keying by id finds 89. The wrong key does
    not raise -- it just reports `no_trace` and silently halves Gate C's coverage.

    So the fixture is keyed by the ID and left EMPTY at the directory name: if the
    selector ever keys by directory again, it finds nothing and `run_key` moves."""
    runs = _fake_runs_root(tmp_path, "cal-err-flask-blueprint-001",
                           [_read_tool_call("app/__init__.py")])
    scored = sg.score_task(BENCHMARKS / "customer_escalation" / "calibration-001",
                           sm.SelectorParams(), runs_root=runs)
    assert scored is not None
    assert scored.gates["C"].evidence.get("run_key") == "cal-err-flask-blueprint-001"
    assert scored.gates["C"].evidence.get("coverage") != "no_trace"


# ---------------------------------------------------------------------------
# Reproducibility: the artifact must pin its inputs, and refuse to guess them.
#
# The defect this section exists for: --runs-root defaulted to a hardcoded
# absolute path into one machine's checkout. Anywhere else, the SAME command
# emitted a 46-candidate artifact whose params, version and source_sha were
# byte-identical to the correct 35-candidate one -- Gate C had silently gone
# PENDING for every task, dropping the 49 FAILs that hold the pool down. Only a
# stderr WARNING distinguished them, and warnings do not survive into artifacts.
# ---------------------------------------------------------------------------


def test_runs_root_is_required(capsys):
    """No default. A default that points at one machine is worse than no default:
    it makes the wrong answer the easy one."""
    with pytest.raises(SystemExit):
        ss.main(["--corpus", str(BENCHMARKS)])
    assert "runs-root" in capsys.readouterr().err


def test_a_missing_runs_root_refuses_to_score(tmp_path):
    """It used to WARN and then score every task's Gate C as PENDING."""
    with pytest.raises(SystemExit) as exc:
        ss.main(["--corpus", str(BENCHMARKS),
                 "--runs-root", str(tmp_path / "nope")])
    assert "does not exist" in str(exc.value)


def test_an_empty_runs_root_refuses_to_score(tmp_path):
    """The one an existence check does NOT catch, and the reason the fix is a
    precondition rather than a provenance field. The directory exists, so
    `is_dir()` passes, so Gate C goes uniformly PENDING and the pool inflates
    from 35 to 46 -- silently, with no warning at all."""
    empty = tmp_path / "runs"
    empty.mkdir()
    with pytest.raises(SystemExit) as exc:
        ss.main(["--corpus", str(BENCHMARKS), "--runs-root", str(empty)])
    assert "no baseline traces" in str(exc.value)


def test_a_refused_run_writes_no_artifact(tmp_path):
    """Fail-closed means fail-closed: a rejected run must not leave a
    plausible-looking artifact behind for someone to pick up later."""
    empty = tmp_path / "runs"
    empty.mkdir()
    out = tmp_path / "stratum.json"
    with pytest.raises(SystemExit):
        ss.main(["--corpus", str(BENCHMARKS), "--runs-root", str(empty),
                 "--out", str(out)])
    assert not out.exists()


def test_traces_digest_discriminates_trace_CONTENT_not_just_presence(tmp_path):
    """Coverage counts alone cannot tell two run sets apart.

    A re-run baseline whose ttfr moves 3 -> 25 flips Gate C from FAIL to PASS and
    changes the pool, while reporting coverage="ok" both times. Identical counts,
    different answer. Only a hash of what the traces SAID separates them."""
    # Same oracle, same coverage ("ok" both times) -- but the baseline in `b`
    # took three turns to reach it instead of one. That is a real ttfr move, and
    # a big enough one flips Gate C's verdict and the pool with it.
    a = _fake_runs_root(tmp_path / "a", "t", [_read_tool_call("src/x.go")])
    b = _fake_runs_root(tmp_path / "b", "t", [
        _read_tool_call("noise/one.go"),
        _read_tool_call("noise/two.go"),
        _read_tool_call("src/x.go"),
    ])

    m_a = {"t": {"C": sg.measure_ttfr("t", ["src/x.go"], a)}}
    m_b = {"t": {"C": sg.measure_ttfr("t", ["src/x.go"], b)}}

    # Coverage is IDENTICAL and the ttfr differs -- which is exactly why the
    # counts cannot be the pin, and the content hash has to be.
    assert ss.trace_coverage(m_a) == ss.trace_coverage(m_b) == {
        "ok": 1, "not_found": 0, "no_trace": 0}
    assert m_a["t"]["C"]["ttfr"] != m_b["t"]["C"]["ttfr"]
    assert ss._traces_digest(m_a) != ss._traces_digest(m_b)
    assert ss._traces_digest(m_a) == ss._traces_digest(m_a)   # and it is stable


def test_resolved_traces_counts_found_traces_not_ok_ones(tmp_path):
    """`not_found` means a trace WAS read and the baseline never opened an oracle
    file; `no_trace` means there was no trace. Only the latter says the run set is
    empty, so conflating them would let a populated-but-unhelpful run set trip the
    empty-runs-root guard."""
    assert ss.resolved_traces({"ok": 3, "not_found": 2, "no_trace": 10}) == 5
    assert ss.resolved_traces({"ok": 0, "not_found": 0, "no_trace": 10}) == 0


def test_an_absent_gate_is_unmeasured_not_final():
    """Found by self-attack: `unmeasured_gates` originally counted only PENDING
    gates, so an artifact whose tasks simply OMITTED gates C/E/F reported
    is_final=True and admitted_task_ids() did not raise.

    An absent gate is strictly worse than a pending one -- it carries no status,
    no reason and no input contract, so nothing downstream can even see it was
    never evaluated. Finality must require presence, not just absence of PENDING.
    """
    partial = sm.TaskGates("t1", "s", {
        g: sm.GateEvidence(g, sm.GateStatus.PASS, "ok") for g in ("A", "B", "D")
    })
    assert partial.missing_gates == ["C", "E", "F"]
    assert partial.is_final is False
    assert partial.admitted is False

    art = sm.StratumArtifact("v", sm.SelectorParams(), [partial], _prov())
    assert art.unmeasured_gates == ["C", "E", "F"]
    assert art.is_final is False
    assert art.to_dict()["stratum_status"] == "CANDIDATE"
    with pytest.raises(sm.StratumNotFinalError):
        art.admitted_task_ids()


def test_gate_a_keys_declared_repos_on_path_not_url_basename():
    """The bug that falsely rejected 6 tasks. [[repos]] defines `path` (the
    directory the sandbox clones INTO, and what ground_truth's `repo` tag refers
    to), NOT `name`. Keying on the URL basename made docker/cli -> "cli" while
    the oracle said "docker-cli", so Gate A reported a repo missing that was in
    fact cloned."""
    toml = {"repos": [{"path": "docker-cli", "url": "https://github.com/docker/cli"},
                      {"path": "moby", "url": "https://github.com/moby/moby"}]}
    assert sg._declared_repos(toml) == {"docker-cli", "moby"}
    ev = sg.score_gate_a(sg._declared_repos(toml), {"docker-cli", "moby"})
    assert ev.status is sm.GateStatus.PASS


def test_gate_a_does_not_pass_vacuously_when_the_oracle_has_no_repo_tags():
    """`missing = {} - declared` is empty, so an oracle with no `repo` tags would
    otherwise PASS a parity gate that verified nothing. Uncheckable != fine."""
    ev = sg.score_gate_a({"repo-a"}, set(), has_oracle_files=True)
    assert ev.status is sm.GateStatus.FAIL
    assert "cannot be verified" in ev.reason


def test_gate_c_not_found_is_pending_not_a_pass():
    """The ruling this gate got WRONG at first. compute_ttfr's contract claims
    not_found is "distinct from an observability gap", and an earlier version
    scored it PASS ("baseline never localized => maximally hard"). The traces
    falsify that: dep-traversal-005's baseline delegates to an `Agent` subagent
    and the extractor sees Read/Grep/Glob/Bash/WebFetch/WebSearch/MCP but NOT
    Agent/Task/Skill/Workflow -- the reads are invisible. Its observable siblings
    measure ttfr=2-4, i.e. trivial.

    not_found therefore cannot separate "never localized" from "localized out of
    view". Asserting difficulty from an unobserved trace is the silent-pass this
    module exists to prevent."""
    ev = sg.score_gate_c(measurement={"coverage": "not_found", "ttfr": None})
    assert ev.status is sm.GateStatus.PENDING_MEASUREMENT
    assert ev.status.passed is False
    # and the contract must name the extraction gap, or it is not actionable
    assert "Agent/Task/Skill" in ev.input_contract


def test_is_candidate_is_not_vacuously_true_for_an_empty_gates_dict():
    """The sibling of the absent-gate fix: is_final and admitted were hardened to
    require gate PRESENCE, but is_candidate -- the property candidate_task_ids()
    actually relies on -- was left checking only `not failed_gates`, which is
    trivially true when there are no gates at all."""
    assert sm.TaskGates("x", "s", {}).is_candidate is False
    partial = sm.TaskGates("x", "s", {
        g: sm.GateEvidence(g, sm.GateStatus.PASS, "ok") for g in ("A", "B", "D")})
    assert partial.is_candidate is False
