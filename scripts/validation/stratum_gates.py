"""The Gate A-F scorers: one task in, six pieces of evidence out.

Every gate returns a GateEvidence, and a gate whose input has not been measured
returns PENDING_MEASUREMENT with a named input contract -- never a pass. Gates A,
B and D are computable from the corpus today; C, E and F are measurements, and
they say so.

Depends on stratum_model (the evidence types) and never the other way round.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

# No sys.path bootstrap here: this module has no __main__ and is only ever
# imported as `scripts.validation.stratum_gates`, by which point the CLI (or
# pytest) has already made the repo root importable. A script-mode shim here
# would be unreachable code pretending to be a safety net.
from eb_metrics.retrieval_extraction import compute_ttfr
from scripts.lib.tasks import find_task_dirs
from scripts.validation import crnt_validator
from scripts.validation import leak_classifier as lc
from scripts.validation.stratum_model import (
    COMPUTABLE_GATES,
    EXCLUDED_DIFFICULTY_STRATA,
    GATE_C_TIER1_MIN,
    GATE_C_TIER2_MIN,
    GATE_C_TRIVIAL_MAX,
    GATE_D_MIN_FILES,
    GATE_D_MIN_MODULES,
    GATE_E_REWARD_FLOOR,
    GATE_F_ADVERSARIAL_MAX,
    GATE_F_GOLDEN_MIN,
    GATE_F_NULL_MAX,
    INPUT_CONTRACTS,
    MEASUREMENT_GATES,
    GateEvidence,
    GateStatus,
    TaskGates,
    SelectorParams,
)

# ---------------------------------------------------------------------------
# Gate scorers -- computable
# ---------------------------------------------------------------------------


def score_gate_a(declared_repos: set[str],
                 oracle_files: list[lc.OracleFile]) -> GateEvidence:
    """PARITY: the baseline arm must be able to see every oracle file locally.

    The baseline Dockerfile clones exactly the task's declared `[[repos]]`
    (scripts/sandbox/dockerfile_generator.generate_standard_dockerfile). If an
    oracle file lives in a repo that is never cloned, the baseline arm is
    solving blind -- the empty-baseline confound this gate exists to reject.

    Scored off the same ``OracleFile`` list the other gates read, so an entry
    this gate cannot check is one the others cannot silently disagree about.
    """
    untagged = sorted(o.path for o in oracle_files if not o.repo)
    oracle_repos = {o.repo for o in oracle_files if o.repo}
    ev: dict[str, Any] = {"declared_repos": sorted(declared_repos),
                          "oracle_repos": sorted(oracle_repos)}

    if not declared_repos:
        return GateEvidence(
            "A", GateStatus.FAIL,
            "task declares no repos: baseline arm would get an empty workspace",
            ev,
        )
    if untagged:
        # An untagged entry used to be DROPPED from the parity set, so a task
        # whose oracle was partly tagged had only the tagged part checked and
        # passed on it. The schema requires a `repo` on every required_files
        # entry; an entry that cannot be checked must fail the gate, not leave
        # it. Covers the all-untagged case (where `missing` would otherwise be
        # empty and the gate would pass vacuously) and the mixed case alike.
        return GateEvidence(
            "A", GateStatus.FAIL,
            f"{len(untagged)} oracle file(s) carry no `repo` tag, so parity "
            f"cannot be verified for them: {', '.join(untagged)} "
            f"(schemas/task.schema.json requires `repo` on every required_files entry)",
            {**ev, "untagged_oracle_files": untagged},
        )
    missing = oracle_repos - declared_repos
    if missing:
        return GateEvidence(
            "A", GateStatus.FAIL,
            f"oracle files live in repo(s) the baseline never clones: "
            f"{', '.join(sorted(missing))}",
            {**ev, "missing_repos": sorted(missing)},
        )
    return GateEvidence(
        "A", GateStatus.PASS,
        "baseline clones every repo the oracle files live in",
        ev,
    )


def score_gate_b(result: lc.GateBResult | None) -> GateEvidence:
    """NON-LEAKY: the instruction must not localize the oracle for free.

    Gate B does not re-derive the verdict -- it reports `leak_classifier.decide()`,
    the one implementation of the rule. Re-litigating it here is exactly how the
    selector and `make verify-leak` came to disagree about the same task.
    """
    if result is None:
        return GateEvidence(
            "B", GateStatus.FAIL,
            "no instruction+oracle could be loaded; Gate B cannot clear",
        )
    return GateEvidence(
        "B", GateStatus.PASS if result.admit else GateStatus.FAIL, result.reason,
        {
            "bucket": result.bucket.value,
            "token": result.token,
            "source_path": result.source_path,
        },
    )


def _modules(paths: list[str], granularity: str) -> set[str]:
    out: set[str] = set()
    for p in paths:
        segs = [s for s in lc.strip_repo_slug(p).split("/") if s]
        if granularity == "top_segment":
            out.add(segs[0] if len(segs) > 1 else ".")
        else:  # parent_dir
            out.add("/".join(segs[:-1]) or ".")
    return out


def score_gate_d(declared_repos: set[str],
                 oracle_files: list[lc.OracleFile],
                 granularity: str,
                 crnt_positive: bool,
                 difficulty_stratum: str) -> GateEvidence:
    """ENTERPRISE CHARACTER: CRNT-positive, or a dispersed single-repo oracle.

    A one-file, one-directory task is not an enterprise context-gathering
    problem however hard the individual file is.

    Two things this gate must get right, both of which the bead's wording
    glosses over:

    * "CRNT-positive (multi-repo)" is NOT "declares >= 2 repos". crnt_validator
      additionally requires every declared repo to be covered by a required_files
      entry -- a 3-repo task where one repo is only touched by sufficient_files
      is CRNT-negative. Declaring repos is not the same as needing them.
    * Nothing else in gates A-F tests SCALE, so this is the only place that can
      keep a calibration task (small-repo by construction) out of a stratum
      labelled "enterprise-complex". The excluded strata are named in
      ``EXCLUDED_DIFFICULTY_STRATA`` and recorded in the artifact's
      ``gate_thresholds`` -- an exclusion that auto-fails ~14 tasks is a constant
      that moves the pool, and a reader of the artifact has to be able to see it.
    """
    ev: dict[str, Any] = {
        "n_repos": len(declared_repos),
        "repos": sorted(declared_repos),
        "crnt_positive": crnt_positive,
        "difficulty_stratum": difficulty_stratum,
        "n_oracle_files": len(oracle_files),
        "module_granularity": granularity,
    }

    if difficulty_stratum in EXCLUDED_DIFFICULTY_STRATA:
        return GateEvidence(
            "D", GateStatus.FAIL,
            f"{difficulty_stratum} task: small-repo by design, so it carries no "
            f"enterprise character regardless of dispersion",
            ev,
        )

    if crnt_positive:
        return GateEvidence(
            "D", GateStatus.PASS,
            f"CRNT-positive across {len(declared_repos)} repos: the task genuinely "
            f"requires more than one repo",
            ev,
        )

    mods = _modules([o.path for o in oracle_files], granularity)
    ev["n_modules"] = len(mods)
    ev["modules"] = sorted(mods)
    dispersed = len(oracle_files) >= GATE_D_MIN_FILES and len(mods) >= GATE_D_MIN_MODULES
    if dispersed:
        return GateEvidence(
            "D", GateStatus.PASS,
            f"single-repo but dispersed: {len(oracle_files)} oracle files across "
            f"{len(mods)} modules",
            ev,
        )
    return GateEvidence(
        "D", GateStatus.FAIL,
        f"not CRNT-positive and concentrated: {len(oracle_files)} oracle file(s) "
        f"across {len(mods)} module(s); needs >={GATE_D_MIN_FILES} files and "
        f">={GATE_D_MIN_MODULES} modules",
        ev,
    )


def unloadable_task_gates(task_id: str, suite: str, why: str) -> TaskGates:
    """A task whose oracle cannot be loaded FAILS loudly.

    The corpus runner used to `continue` past these, so they landed in neither
    the admitted nor the rejected set -- an invisible drop. A task with no
    loadable oracle cannot satisfy parity, non-leakiness or dispersion, and
    saying so is the only honest scoring.
    """
    unloadable = f"task oracle could not be loaded ({why})"
    gates = {
        **{g: GateEvidence(g, GateStatus.FAIL,
                           f"{unloadable}; gate cannot be evaluated")
           for g in COMPUTABLE_GATES},
        **{g: _pending(g, unloadable) for g in MEASUREMENT_GATES},
    }
    return TaskGates(task_id=task_id, suite=suite, gates=gates)


# ---------------------------------------------------------------------------
# Gate scorers -- measurements. PENDING unless handed a measurement.
# ---------------------------------------------------------------------------


def _pending(gate: str, reason: str,
             evidence: dict[str, Any] | None = None) -> GateEvidence:
    return GateEvidence(
        gate, GateStatus.PENDING_MEASUREMENT, reason,
        evidence or {},
        input_contract=INPUT_CONTRACTS[gate],
    )


def score_gate_c(measurement: dict[str, Any] | None) -> GateEvidence:
    """HARD-TO-LOCALIZE: baseline turns-to-first-relevant, from a real trace.

    This is a MEASUREMENT, computed by ``compute_ttfr`` (jn73.1) over the
    baseline arm's ``agent_trace.jsonl``. There is deliberately no static proxy:
    estimating localizability from oracle-file counts or instruction length
    would be a heuristic standing in for the very quantity the stratum is
    defined by.

    It is a PARTIAL gate. The locked run set covers only some of the corpus, so
    each task lands in one of three places, and collapsing them would be the
    whole bug this module exists to prevent:

      coverage="ok"         -> scored. ttfr <= 7 is trivial and FAILS.
      coverage="not_found"  -> the baseline made tool calls but the extractor
                               never saw it open an oracle file. PENDING -- see
                               below; this is NOT evidence of difficulty.
      coverage="no_trace"   -> genuinely unmeasured. PENDING.

    On ``not_found``. ``compute_ttfr``'s contract claims it is "a genuine miss ...
    distinct from an observability gap", and an earlier version of this gate took
    that at face value and scored it PASS ("the baseline never localized, so the
    task is maximally hard"). That was WRONG, and the traces falsify it: the
    baseline for dep-traversal-005 delegates its investigation to an ``Agent``
    subagent and only ``Write``s the result, and the extractor recognizes
    Read/Grep/Glob/Bash/WebFetch/WebSearch/MCP -- not Agent/Task/Skill/Workflow.
    The subagent's reads are simply invisible. Corroborating: the tasks in that
    same family whose traces ARE observable measure ttfr=2-4, i.e. trivial.

    So ``not_found`` cannot distinguish "never localized" (hard) from "localized
    somewhere we cannot see" (possibly trivial). Asserting difficulty from an
    unobserved trace is exactly the silent-pass this module exists to prevent, so
    it is PENDING: the task stays a candidate, but the stratum never claims it is
    hard on evidence it does not have.
    """
    if not measurement:
        return _pending("C", "no baseline trace for this task")
    coverage = measurement.get("coverage")
    # Carried through every branch -- including PENDING, where the run key is
    # precisely what you need to work out WHY a trace was not found.
    base = {"coverage": coverage, "run_key": measurement.get("run_key")}

    if coverage == "not_found":
        return _pending(
            "C",
            "baseline trace shows no oracle-file read, but the extractor cannot "
            "see inside Agent/Task/Skill delegations -- 'never localized' and "
            "'localized out of view' are indistinguishable here, so difficulty "
            "is NOT asserted",
            {**base, "ttfr": None, "tier": None},
        )
    if coverage != "ok" or measurement.get("ttfr") is None:
        return _pending(
            "C", f"no usable baseline trace for this task (coverage={coverage!r})",
            base,
        )

    ttfr = measurement["ttfr"]
    if ttfr <= GATE_C_TRIVIAL_MAX:
        return GateEvidence(
            "C", GateStatus.FAIL,
            f"trivial to localize: turns-to-first-relevant={ttfr} "
            f"(<={GATE_C_TRIVIAL_MAX})",
            {**base, "ttfr": ttfr, "tier": None},
        )
    tier: int | None
    if ttfr >= GATE_C_TIER1_MIN:
        tier = 1
    elif ttfr >= GATE_C_TIER2_MIN:
        tier = 2
    else:
        tier = None  # the unlabelled 8-11 band: not trivial, not yet tiered
    return GateEvidence(
        "C", GateStatus.PASS,
        f"hard to localize: turns-to-first-relevant={ttfr}"
        + (f" (tier-{tier})" if tier else " (above the trivial cut, untiered band 8-11)"),
        {**base, "ttfr": ttfr, "tier": tier},
    )


def score_gate_e(measurement: dict[str, Any] | None) -> GateEvidence:
    """SOLVABLE-NOT-FLOORED: a strong agent must actually be able to score."""
    if not measurement or measurement.get("gold_reward") is None:
        return _pending("E", "no gold / strong-agent run for this task")
    reward = measurement["gold_reward"]
    ok = reward >= GATE_E_REWARD_FLOOR
    return GateEvidence(
        "E", GateStatus.PASS if ok else GateStatus.FAIL,
        f"gold reward {reward:.3f} "
        f"{'>=' if ok else '<'} floor {GATE_E_REWARD_FLOOR}",
        {"gold_reward": reward},
    )


def score_gate_f(measurement: dict[str, Any] | None) -> GateEvidence:
    """VERIFIER-CALIBRATED: the scorer must separate null from golden."""
    if not measurement or any(measurement.get(k) is None
                              for k in ("null", "golden", "adversarial")):
        return _pending("F", "no verifier calibration run for this task")
    null, golden, adv = (measurement["null"], measurement["golden"],
                         measurement["adversarial"])
    failures = []
    if null > GATE_F_NULL_MAX:
        failures.append(f"null {null:.2f} > {GATE_F_NULL_MAX}")
    if golden < GATE_F_GOLDEN_MIN:
        failures.append(f"golden {golden:.2f} < {GATE_F_GOLDEN_MIN}")
    if adv > GATE_F_ADVERSARIAL_MAX:
        failures.append(f"adversarial {adv:.2f} > {GATE_F_ADVERSARIAL_MAX}")
    ev = {"null": null, "golden": golden, "adversarial": adv}
    if failures:
        return GateEvidence("F", GateStatus.FAIL,
                            "verifier miscalibrated: " + "; ".join(failures), ev)
    return GateEvidence("F", GateStatus.PASS, "verifier calibrated", ev)


# ---------------------------------------------------------------------------
# Corpus scoring
# ---------------------------------------------------------------------------


def _declared_repos(task_toml: dict[str, Any]) -> set[str]:
    """The repo identities the baseline actually clones.

    Keyed on ``[[repos]].path`` -- the directory the sandbox clones INTO
    (dockerfile_generator clones to /workspace/{path}), which is also the key
    ground_truth's ``repo`` tag refers to. Keying on the URL basename instead
    silently diverges whenever a task picks a disambiguating path (docker/cli
    is cloned to `docker-cli`, aws/aws-cli to `awscli`), which made Gate A
    compute `missing = {docker-cli} - {cli}` and FAIL 6 tasks whose repos were
    in fact cloned.

    ``crnt_validator.extract_repos`` already reads exactly this key, and Gate D
    scores CRNT off the same parse -- so both gates read one definition of "a
    declared repo" rather than two that can drift.
    """
    return {r.path for r in crnt_validator.extract_repos(task_toml) if r.path}


def _crnt_positive(task_toml: dict[str, Any]) -> bool:
    """Per-task CRNT boolean. Stricter than "declares >= 2 repos": every
    declared repo must be covered by a required_files entry.

    Deliberately NOT wrapped in a try/except. ``evaluate_crnt`` is deterministic
    over already-parsed TOML and raises for none of the 119 task.toml files in
    the corpus, so a swallow would be dead code today -- and a landmine
    tomorrow: a task that started raising would be silently scored CRNT-negative,
    quietly downgrading Gate D to its dispersion fallback instead of failing.
    A gate that cannot be evaluated must never evaluate to "no".
    """
    return bool(crnt_validator.evaluate_crnt(task_toml).passes_crnt)


def measure_ttfr(run_key: str, oracle_files: list[lc.OracleFile],
                 runs_root: Path) -> dict[str, Any]:
    """Gate C's measurement, from the baseline arm's real trace.

    ``run_key`` is the task's ``[task].id`` (see ``lc.task_run_key``), which the
    locked run set is keyed by.

    Note the oracle is passed IN. ``compute_ttfr`` takes ``required_files`` as
    an argument, so Gate C can be fed the task.toml-aware oracle without
    touching ``retrieval_extraction``'s own ground-truth resolution -- which is
    what keeps this out of the already-published retrieval-recall aggregate.
    """
    trace = runs_root / run_key / "baseline" / "agent_trace.jsonl"
    r = compute_ttfr(trace, [o.qualified for o in oracle_files])
    return {"coverage": r.coverage, "ttfr": r.turns_to_first_relevant,
            "run_key": run_key, "trace_sha256": _sha256_file(trace)}


def _sha256_file(path: Path) -> str:
    """Content hash of one trace, or "" when it is absent.

    Gate C's verdict is a function of the trace's CONTENT, so the content is what
    provenance has to pin. Hashing the path would pin nothing.
    """
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def score_task(task_dir: Path,
               params: SelectorParams,
               measurements: dict[str, dict[str, Any]] | None = None) -> TaskGates | None:
    """Score one task against all six gates off a SINGLE read of it.

    ``lc.load_task`` is the one loader: it parses task.toml, instruction.md and
    the merged ground truth once, and every gate is fed from that parse. No gate
    re-opens the task behind its own loader -- that costs redundant I/O and, far
    worse, lets two gates disagree about what the same task says.

    Gate C's measurement is handed IN via ``measurements``; there is deliberately
    no ``runs_root`` here for this function to measure it lazily off. Two ways to
    obtain the same measurement is two places for the policy to drift, and the
    lazy one was never on the production path -- ``score_corpus`` has always fed
    the pre-computed dict from ``measure_corpus_ttfr``.
    """
    if not (task_dir / "task.toml").is_file():
        return None

    task_id = task_dir.name
    suite = task_dir.parent.name

    try:
        oracle = lc.load_task(task_dir)
    except Exception as exc:
        return unloadable_task_gates(task_id, suite, f"unparseable task.toml: {exc}")
    if oracle is None:
        # M2: never a silent skip.
        return unloadable_task_gates(
            task_id, suite, "no instruction+ground_truth oracle could be loaded"
        )

    task_toml = oracle.task_toml
    try:
        run_key = lc.task_run_key(task_toml, task_id)
    except lc.UnsafeTaskIdError as exc:
        # An id that cannot be a path segment is a malformed task, and malformed
        # must FAIL -- not degrade to a missing trace, which reads as Gate C
        # PENDING and leaves the task a candidate.
        return unloadable_task_gates(task_id, suite, str(exc))

    # Keyed by run_key, not task_id: the directory basename is not unique across
    # suites (three tasks are called calibration-001), so a task_id lookup hands
    # colliding tasks each other's Gate C trace. See measure_corpus_ttfr.
    m = (measurements or {}).get(run_key, {})

    declared = _declared_repos(task_toml)
    # difficulty_stratum is a TOP-LEVEL key in task.toml, not nested.
    stratum = task_toml.get("difficulty_stratum", "")

    gates = {
        "A": score_gate_a(declared, oracle.oracle_files),
        "B": score_gate_b(lc.decide(run_key, oracle)),
        "C": score_gate_c(m.get("C")),
        "D": score_gate_d(declared, oracle.oracle_files, params.module_granularity,
                          crnt_positive=_crnt_positive(task_toml),
                          difficulty_stratum=stratum),
        "E": score_gate_e(m.get("E")),
        "F": score_gate_f(m.get("F")),
    }
    return TaskGates(task_id=task_id, suite=suite, gates=gates)


def score_corpus(root: Path,
                 params: SelectorParams,
                 measurements: dict[str, dict[str, Any]] | None = None) -> list[TaskGates]:
    out: list[TaskGates] = []
    for task_dir in find_task_dirs(root):
        scored = score_task(task_dir, params, measurements)
        if scored is not None:
            out.append(scored)
    return out


def measure_corpus_ttfr(root: Path, runs_root: Path) -> dict[str, dict[str, Any]]:
    """Gate C's measurement for every task in the corpus, computed once.

    Gate C reads baseline traces, which depend on neither knob below, so a knob
    sweep must not re-read them once per combination.

    Keyed by RUN KEY -- the task's ``[task].id`` -- and not by the task directory's
    basename, which is not unique. Three tasks are called ``calibration-001``
    (customer_escalation, platform_engineering, technical_debt) and three
    ``calibration-002``; keyed by basename they collapse onto one entry, so the
    last one walked silently donates its trace to the other two and they are
    scored on a ttfr, a run_key and a trace_sha256 that are not their own. The id
    is unique across all 116 tasks, and it is what the locked run set is keyed by
    -- the same identity ``measure_ttfr`` resolves the trace with.
    """
    out: dict[str, dict[str, Any]] = {}
    for task_dir in find_task_dirs(root):
        oracle = lc.load_task(task_dir)
        if oracle is None:
            continue
        run_key = lc.task_run_key(oracle.task_toml, task_dir.name)
        out[run_key] = {
            "C": measure_ttfr(run_key, oracle.oracle_files, runs_root)
        }
    return out
