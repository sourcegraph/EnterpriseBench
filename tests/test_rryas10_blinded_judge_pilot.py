"""Invariant tests for the blinded PR-review-like judging pilot (rryas.10).

The pilot (``scripts/analysis/rryas10_blinded_judge_pilot.py``) is only worth
reporting if three properties hold, so they are pinned here rather than trusted:

1. **Blinding fails closed.** A payload that names an arm is refused, not judged.
2. **An infra error never becomes a score.** A backend/parse failure is recorded
   as an infra error and dropped from the agreement stats, honoring the
   ``scorer_guard`` rule that a missing score never turns into a real one.
3. **No blending.** The judge score stays a parallel column; it is never mixed
   into (or min()'d against) the deterministic reward.

All three run offline: ``LLMJudge`` is stubbed, so no judge backend is called.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "analysis"))

import rryas10_blinded_judge_pilot as pilot  # noqa: E402

from eb_verify.judge.backends import JudgeBackendError  # noqa: E402
from eb_verify.judge.models import (  # noqa: E402
    CheckpointJudgeResult,
    JudgeScoreError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubJudge:
    """Stand-in for LLMJudge: returns canned scores, or raises per checkpoint.

    ``raises`` maps checkpoint name -> exception instance to raise instead of
    returning a verdict, which is how an infra failure is simulated.
    """

    def __init__(self, scores: dict, raises: dict | None = None) -> None:
        self.scores = scores
        self.raises = raises or {}
        self.seen: list = []
        self.pass_threshold = 0.5
        # None means "generate a payload from the checkpoint name". Set it to a
        # dict to pin an exact payload -- including {}, which is what
        # models.py's default_factory produces.
        self.raw_payload: dict | None = None

    def evaluate_checkpoint(self, judge_input, task_description, checkpoint_description):
        self.seen.append(judge_input)
        name = judge_input.checkpoint_name
        if name in self.raises:
            raise self.raises[name]
        score = self.scores[name]
        return CheckpointJudgeResult(
            checkpoint_name=name,
            score=score,
            passed=score >= 0.5,
            reasoning=f"stub reasoning for {name}",
            evidence=f"stub evidence for {name}",
            confidence="high",
            # Mirrors the real contract: backends parse before returning, so
            # `raw_response` is the decoded payload (models.py:41), not text.
            # It carries fields CheckpointJudgeResult drops -- `notes` here.
            raw_response=(
                {
                    "score": score,
                    "reasoning": f"stub reasoning for {name}",
                    "notes": f"unmapped field for {name}",
                }
                if self.raw_payload is None
                else self.raw_payload
            ),
        )


def _make_unit(
    tmp_path: Path,
    *,
    task_id: str = "task-001",
    arm: str = "baseline",
    answer: str = '{"summary": "an arm-neutral answer"}',
    checkpoints: dict | None = None,
    det_scores: dict | None = None,
    det_total: float | None = 1.0,
) -> pilot.Unit:
    """Build a Unit backed by real on-disk answer + expected_solution files."""
    checkpoints = checkpoints or {"cp_a": {}}
    det_scores = det_scores if det_scores is not None else dict.fromkeys(checkpoints, 1.0)

    unit_dir = tmp_path / f"{task_id}-{arm}"
    unit_dir.mkdir(parents=True)
    answer_path = unit_dir / "answer.json"
    answer_path.write_text(answer)

    task_dir = unit_dir / "benchmark"
    task_dir.mkdir()
    (task_dir / "expected_solution.json").write_text(
        json.dumps(
            {
                "checkpoints": {
                    name: {
                        "expected_solution": f"expected for {name}",
                        "evaluation_criteria": [f"must mention {name}"],
                    }
                    for name in checkpoints
                }
            }
        )
    )
    return pilot.Unit(
        task_id=task_id,
        arm=arm,
        answer_path=str(answer_path),
        task_dir=str(task_dir),
        deterministic_total=det_total,
        deterministic_checkpoints={
            name: {"score": det_scores.get(name), "weight": 1.0} for name in checkpoints
        },
    )


# ---------------------------------------------------------------------------
# 1. Blinding fails closed
# ---------------------------------------------------------------------------


class TestArmBlindingFailsClosed:
    @pytest.mark.parametrize(
        "payload",
        [
            '{"mode": "mcp_only", "summary": "found it"}',
            '{"MODE": "Baseline"}',  # case must not launder the tell
            # --- key name the guard was never told about ---
            # The leak that motivated the value scan: keying every pass off one
            # list of field names made an unanticipated name invisible to all
            # of them at once.
            '{"provenance": "mcp_only"}',
            '{"harness_mode": "hybrid"}',
            '{"generated_by": "cli"}',
            # Non-dict top level: no key exists to match on at all.
            '["mode", "mcp_only"]',
            '["baseline"]',
            # --- separator/case spellings of an identity key ---
            '{"toolAccess": "some_future_arm_v3"}',
            '{"tool-access": "some_future_arm_v3"}',
            '{"experimentArm": "some_future_arm_v3"}',
            # --- tool provenance surviving under an unrecognized wrapper ---
            '{"events": [{"type": "tool_usage", "grep_calls": 24}]}',
            '{"stats": {"sourcegraph_searches": 8}}',
            # --- shapes a substring-per-serialization guard let through ---
            # Compact JSON: no space after the colon. Plain json.dumps with
            # separators=(",", ":") emits this.
            '{"mode":"mcp_only","summary":"x"}',
            # The short arm label this repo actually writes to run_log.json.
            # A needle list containing only "mcp_only" misses it entirely.
            '{"mode": "mcp", "summary": "x"}',
            # A differently-named key carrying the same identity.
            '{"arm": "hybrid", "summary": "x"}',
            '{"experiment_arm": "baseline"}',
            '{"tool_access": "mcp_only"}',
            # Python-repr / single-quoted payload, not valid JSON.
            "{'mode': 'mcp_only', 'summary': 'x'}",
            # Nested anywhere in the structure, not just at the top level.
            '{"meta": {"run": {"mode": "hybrid"}}}',
            '{"runs": [{"mode": "cli"}]}',
        ],
    )
    def test_arm_tell_refuses_to_judge(self, payload: str) -> None:
        with pytest.raises(ValueError, match="refusing to judge"):
            pilot._assert_arm_neutral(payload)

    @pytest.mark.parametrize(
        "payload",
        [
            '{"summary": "solved via hybrid retrieval"}',
            # Same class, and it used to be refused: an arm token inside a
            # longer sentence in parseable JSON. Catching this spelling of
            # prose while allowing the one above was incoherent, and the regex
            # that caught it also refused "...retry mode: cli" -- aborting the
            # whole run over a legitimate answer.
            '{"summary": "ran with mode=hybrid"}',
        ],
    )
    def test_arm_token_inside_prose_is_a_known_residual(self, payload: str) -> None:
        """Pin the documented limit of this guard so it stays a decision.

        An arm token embedded in a sentence is NOT caught, because the same
        words ("cli", "baseline", "hybrid") are ordinary engineering vocabulary
        and blocking them inside prose would reject legitimate answers. If this
        assertion ever starts failing, the guard grew a semantic heuristic and
        the doc's limitations section needs revisiting.
        """
        pilot._assert_arm_neutral(payload)

    @pytest.mark.parametrize(
        "payload",
        [
            # The regression that made the text pass JSON-only. Anchored to the
            # end of a value, it fired on any sentence closing on `key: token`.
            '{"summary": "we validated the fix across every retry mode: cli"}',
            '{"summary": "the config validation condition: hybrid"}',
            '{\n  "summary": "the observed failure condition: cli"\n}',
        ],
    )
    def test_prose_ending_on_an_arm_token_is_judgeable(self, payload: str) -> None:
        """A legitimate answer must never abort the run.

        `_blind_all` refuses the whole batch on any offender, so a false
        positive here is not a dropped unit -- it is the entire pilot.
        """
        pilot._assert_arm_neutral(payload)

    @pytest.mark.parametrize("key", ["arm", "experiment_arm", "tool_access"])
    def test_identity_key_refused_regardless_of_value(self, key: str) -> None:
        """Fail closed on a condition-identity key even for an unknown arm name.

        These keys only ever mean "which experimental condition", so any value
        under one is a tell. That is what lets the guard survive a new arm
        being added: for this tier it keys off the field, not off a vocabulary
        that has to be kept in sync.
        """
        with pytest.raises(ValueError, match="refusing to judge"):
            pilot._assert_arm_neutral(json.dumps({key: "some_future_arm_v3"}))

    @pytest.mark.parametrize(
        "payload",
        [
            # Container-wrapped values must not launder the token. Before the
            # tier split these were caught only incidentally, by repr()-ing the
            # whole value, so a naive string compare would regress them.
            '{"mode": ["mcp_only"]}',
            '{"mode": {"name": "cli"}}',
            '{"condition": "baseline"}',
        ],
    )
    def test_ambiguous_key_refused_when_value_is_an_arm(self, payload: str) -> None:
        with pytest.raises(ValueError, match="refusing to judge"):
            pilot._assert_arm_neutral(payload)

    def test_unknown_arm_under_an_ambiguous_key_is_a_known_residual(self) -> None:
        """Pin the cost of the tier split so nobody silently re-tightens it.

        ``mode``/``condition`` are ordinary domain vocabulary, so they are
        matched by value, not by key. An arm named after this vocabulary was
        fixed would slip past. That is the accepted trade for not refusing
        every Ansible file mode; the identity-key tier above is where
        future-arm-proofing lives.
        """
        pilot._assert_arm_neutral('{"mode": "some_future_arm_v3"}')

    @pytest.mark.parametrize(
        "payload",
        [
            '{"summary": "no arm named here"}',
            # Guards against over-blocking: these must still be judgeable.
            # "mode" as prose, not as a condition label.
            '{"summary": "the failure mode was a null deref in parse()"}',
            # An arm token appearing as ordinary vocabulary.
            '{"summary": "the baseline latency budget is 200ms"}',
            '{"summary": "run the cli to reproduce"}',
            "not json at all, just prose about a bug",
            # --- ordinary domain vocabulary under an arm-shaped key ---
            # These are the shapes this repo's own suites produce. Refusing
            # them aborts the run on a legitimate answer, which is why `mode`
            # and `condition` are matched by value rather than by key.
            '{"tasks": [{"name": "write cfg", "mode": "0644"}]}',
            '{"rule": {"condition": "B", "data": []}}',
            '{"variable": {"validation": {"condition": "length(var.x) > 0"}}}',
            '{"fix": {"mode": "prefork"}}',
            # An arm token inside a longer value is prose, not a label. The
            # free-text pass must anchor to the whole value or it re-refuses
            # what the structural tier just allowed.
            '{"condition": "cli returned nonzero"}',
            '{"mode": "baseline comparison run"}',
        ],
    )
    def test_arm_neutral_payload_passes(self, payload: str) -> None:
        pilot._assert_arm_neutral(payload)

    def test_identity_key_in_non_json_prose_is_refused(self) -> None:
        """The free-text pass must cover every identity key, not a subset.

        ``experiment_arm`` is carried by the structural tier for real JSON, so
        a python-repr payload is the only thing that exercises it in the regex.
        """
        with pytest.raises(ValueError, match="refusing to judge"):
            pilot._assert_arm_neutral("{'experiment_arm': 'baseline'}")

    def test_arm_token_vocabulary_covers_the_harness_modes(self) -> None:
        """ARM_TOKENS is load-bearing for the value scan, so pin drift.

        A mode added to the harness but not here silently stops being detected.

        Loaded by explicit path rather than by importing ``lib.shared``: the
        repo has BOTH a top-level ``lib/`` (a PEP 420 namespace package, what
        every other test means by ``lib``) and ``scripts/lib/`` (a regular
        package with __init__.py). Putting ``scripts/`` on sys.path lets the
        regular package win and caches it as ``lib`` in sys.modules for the
        rest of the pytest process, so ``lib.eb_verify`` stops resolving for
        every test collected afterwards.
        """
        spec = importlib.util.spec_from_file_location(
            "_rryas10_shared_probe", REPO_ROOT / "scripts" / "lib" / "shared.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert set(module.VALID_MODES) <= pilot.ARM_TOKENS

    def test_vocabulary_normalizes_to_the_forms_the_walker_compares(self) -> None:
        """The regexes use the spellings; the walker uses normalized forms.

        They are derived from one declaration, and this pins that they stay in
        agreement -- when they briefly diverged, the text pass silently stopped
        matching anything at all.
        """
        assert pilot.ARM_IDENTITY_KEYS == {
            pilot._norm_key(k) for k in pilot.ARM_IDENTITY_SPELLINGS
        }
        assert pilot.TELEMETRY_KEYS == {
            pilot._norm_key(k) for k in pilot.TELEMETRY_SPELLINGS
        }
        assert pilot.PROVENANCE_TOKENS == {
            pilot._norm_key(k) for k in pilot.PROVENANCE_SPELLINGS
        }

    def test_compiled_patterns_are_stable_across_processes(self) -> None:
        """The vocabulary hash is only evidence if the pattern is deterministic.

        Sets iterate in hash-seed order, so an alternation built without a
        total ordering differs between processes.
        """
        script = (
            "import sys; sys.path.insert(0, %r); "
            "import rryas10_blinded_judge_pilot as p; "
            "print(p._ARM_TEXT_RE.pattern); print(p._TELEMETRY_TEXT_RE.pattern); "
            "print(p._blinding_vocabulary_hash())"
            % str(REPO_ROOT / "scripts" / "analysis")
        )
        env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "lib")}
        outs = {
            subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                check=True,
                env={**env, "PYTHONHASHSEED": seed},
            ).stdout
            for seed in ("0", "1", "2", "3")
        }
        assert len(outs) == 1

    def test_build_blinded_inputs_rejects_leaky_answer(self, tmp_path: Path) -> None:
        """The assertion is wired into the path that builds judge input."""
        unit = _make_unit(tmp_path, answer='{"mode": "mcp_only"}')
        with pytest.raises(ValueError, match="refusing to judge"):
            pilot.build_blinded_inputs(unit)

    def test_judge_input_carries_no_arm(self, tmp_path: Path) -> None:
        """Arm is held on the Unit for post-hoc analysis, never on the input."""
        unit = _make_unit(tmp_path, arm="mcp_only")
        for _, judge_input in pilot.build_blinded_inputs(unit):
            assert "mcp_only" not in json.dumps(judge_input.__dict__)


class TestHarnessTelemetryIsScrubbed:
    """The leak that was actually on disk, not the one the guard was aimed at.

    Every answer.json under results/sample_runs carries a ``tool_usage`` block
    echoed from run_log.json, and its *shape* separates the arms perfectly:
    baseline reports ``grep_calls``, mcp reports ``sourcegraph_searches`` and
    ``symbol_navigations``. No arm name appears, so a name-matching guard sees
    nothing, yet a judge reading the block can label the arm 6/6. It did: the
    demonstration ledger cites tool telemetry as scoring evidence in 6 of 14
    verdicts, including all four mcp rows that mention it, where it is cited as
    affirmative support for a 1.0 ("8 sourcegraph searches, 12 symbol
    navigations ... indicate actual code exploration"). Telemetry is harness
    state rather than answer content, so it is removed before judging rather
    than refused -- refusing would abort on every unit and blind nobody.
    """

    BASELINE_SHAPE = {"grep_calls": 15, "file_reads": 42, "total_tokens": 18500}
    MCP_SHAPE = {
        "sourcegraph_searches": 8,
        "symbol_navigations": 12,
        "file_reads": 28,
        "total_tokens": 14200,
    }

    @pytest.mark.parametrize("key", ["tool_usage", "toolUsage", "tool-usage", "TOOL_USAGE"])
    def test_scrub_is_not_defeated_by_key_spelling(
        self, tmp_path: Path, key: str
    ) -> None:
        """A closed list of exact spellings is how `toolUsage` walked past.

        The harness writes snake_case today; the guard must not depend on that
        staying true, since the whole point is catching a field nobody
        anticipated.
        """
        answer = json.dumps({"summary": "x", key: self.MCP_SHAPE})
        unit = _make_unit(tmp_path, answer=answer)

        _, judge_input = pilot.build_blinded_inputs(unit)[0]
        assert "sourcegraph_searches" not in judge_input.agent_output
        assert "symbol_navigations" not in judge_input.agent_output

    @pytest.mark.parametrize("shape", [BASELINE_SHAPE, MCP_SHAPE])
    def test_tool_usage_never_reaches_the_judge(
        self, tmp_path: Path, shape: dict
    ) -> None:
        answer = json.dumps({"summary": "the bug is in parse()", "tool_usage": shape})
        unit = _make_unit(tmp_path, answer=answer)

        for _, judge_input in pilot.build_blinded_inputs(unit):
            assert "tool_usage" not in judge_input.agent_output
            for tell in shape:
                assert tell not in judge_input.agent_output

    def test_scrubbing_keeps_the_answer_intact(self, tmp_path: Path) -> None:
        """Blinding must cost the judge no answer content."""
        answer = json.dumps(
            {
                "summary": "the bug is in parse()",
                "files": ["src/parse.py"],
                "tool_usage": self.MCP_SHAPE,
            }
        )
        unit = _make_unit(tmp_path, answer=answer)

        _, judge_input = pilot.build_blinded_inputs(unit)[0]
        kept = json.loads(judge_input.agent_output)
        assert kept == {"summary": "the bug is in parse()", "files": ["src/parse.py"]}

    def test_nested_tool_usage_is_scrubbed(self, tmp_path: Path) -> None:
        answer = json.dumps({"meta": {"run": {"tool_usage": self.MCP_SHAPE}}})
        unit = _make_unit(tmp_path, answer=answer)

        _, judge_input = pilot.build_blinded_inputs(unit)[0]
        assert "sourcegraph_searches" not in judge_input.agent_output

    def test_scrub_records_which_fields_it_removed(self, tmp_path: Path) -> None:
        """A per-run count cannot distinguish noise removed from answer lost."""
        answer = json.dumps({"meta": {"run": {"tool_usage": self.MCP_SHAPE}}})
        _, removed = pilot.blind_payload(answer)
        assert removed == ["$.meta.run.tool_usage"]

    def test_unscrubbable_telemetry_is_refused(self) -> None:
        """A non-JSON payload cannot be structurally scrubbed, so it fails closed.

        Removing the block requires parsing it. When that is impossible the
        guard has no safe middle ground and must refuse rather than judge a
        payload it knows carries tool provenance.
        """
        with pytest.raises(ValueError, match="refusing to judge"):
            pilot._assert_arm_neutral("notes: tool_usage sourcegraph_searches=8")


class TestBlindingSurvivesHostileInput:
    """answer.json is agent-generated, so the guard runs on untrusted input."""

    def test_deeply_nested_payload_is_refused_not_crashed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ~2KB answer must not take the batch down with a RecursionError.

        RecursionError is a RuntimeError, so neither blind_payload's
        JSONDecodeError handler nor _blind_all's ValueError handler caught it:
        it propagated out of run_pilot uncaught, killing the process with a
        bare traceback and defeating the pre-pass's whole purpose.
        """
        nested = "[" * 2000 + "1" + "]" * 2000
        good = _make_unit(tmp_path, task_id="ok-1")
        hostile = _make_unit(tmp_path, task_id="nested-1", answer=nested)
        monkeypatch.setattr(pilot, "LLMJudge", lambda **kw: _StubJudge({"cp_a": 1.0}))

        # Unparseable, so nothing is removable; it must resolve one way or the
        # other without raising RecursionError out of the batch.
        report = pilot.run_pilot(
            [good, hostile], model="stub", seed=1, out_dir=tmp_path / "out"
        )
        assert report["config"]["blinding"]["units_checked"] == 2

    def test_refusal_message_is_bounded(self) -> None:
        """An unbounded repr puts arbitrary untrusted text into logs."""
        with pytest.raises(ValueError) as exc:
            pilot._assert_arm_neutral(json.dumps({"arm": "A" * 50_000}))
        assert len(str(exc.value)) < 2_000


class TestBlindingRefusalDoesNotDestroyTheRun:
    def test_refusal_aborts_before_any_judge_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Blinding is checked as a pre-pass, so a refusal costs nothing.

        build_blinded_inputs used to run inside the judging loop, after the
        ledger was truncated -- a tell on unit 7 of 40 destroyed report.json
        having already paid for six units of judge calls. That is the same harm
        the broad backend except-clause exists to prevent.
        """
        good = _make_unit(tmp_path, task_id="ok-1")
        bad = _make_unit(tmp_path, task_id="leaky-1", answer='{"arm": "hybrid"}')
        stub = _StubJudge({"cp_a": 1.0})
        monkeypatch.setattr(pilot, "LLMJudge", lambda **kw: stub)

        out = tmp_path / "out"
        with pytest.raises(ValueError, match="refusing to judge"):
            pilot.run_pilot([good, bad], model="stub", seed=1, out_dir=out)

        assert stub.seen == []
        assert not (out / "verdicts.jsonl").exists()

    def test_every_offender_is_named_not_just_the_first(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One invocation must surface the whole problem, not one name per run."""
        units = [
            _make_unit(tmp_path, task_id="leaky-1", answer='{"arm": "hybrid"}'),
            _make_unit(tmp_path, task_id="leaky-2", answer='{"tool_access": "cli"}'),
        ]
        monkeypatch.setattr(pilot, "LLMJudge", lambda **kw: _StubJudge({"cp_a": 1.0}))

        with pytest.raises(ValueError) as exc:
            pilot.run_pilot(units, model="stub", seed=1, out_dir=tmp_path / "out")

        assert "leaky-1" in str(exc.value)
        assert "leaky-2" in str(exc.value)

    def test_report_records_what_blinding_did(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The artifact of record must evidence the blinding, not just assert it.

        Same reasoning as rubric_sha256: a reader should be able to tell which
        vocabulary ran and how many answers it had to scrub.
        """
        units = [
            _make_unit(tmp_path, task_id="clean-1"),
            _make_unit(
                tmp_path,
                task_id="noisy-1",
                answer=json.dumps({"summary": "x", "tool_usage": {"grep_calls": 3}}),
            ),
        ]
        monkeypatch.setattr(pilot, "LLMJudge", lambda **kw: _StubJudge({"cp_a": 1.0}))

        report = pilot.run_pilot(units, model="stub", seed=1, out_dir=tmp_path / "out")

        blinding = report["config"]["blinding"]
        assert blinding["units_checked"] == 2
        assert blinding["units_telemetry_scrubbed"] == 1
        assert blinding["refusal_policy"] == "abort before judging"
        assert len(blinding["vocabulary_sha256"]) == 64


# ---------------------------------------------------------------------------
# 2. An infra error never becomes a score
# ---------------------------------------------------------------------------


class TestInfraErrorIsNotAScore:
    @pytest.mark.parametrize(
        "exc",
        [
            JudgeBackendError("backend unreachable"),
            JudgeScoreError("judge returned a non-score: 'unknown'"),
        ],
    )
    def test_failure_is_recorded_not_scored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc: Exception
    ) -> None:
        unit = _make_unit(
            tmp_path,
            checkpoints={"cp_ok": {}, "cp_broken": {}},
            det_scores={"cp_ok": 1.0, "cp_broken": 1.0},
        )
        stub = _StubJudge({"cp_ok": 1.0}, raises={"cp_broken": exc})
        monkeypatch.setattr(pilot, "LLMJudge", lambda **kw: stub)

        out = tmp_path / "out"
        report = pilot.run_pilot([unit], model="stub", seed=1, out_dir=out)

        assert report["sample"]["infra_errors"] == 1
        assert report["sample"]["checkpoints_judged"] == 1
        detail = report["infra_errors_detail"][0]
        assert detail["checkpoint"] == "cp_broken"
        assert "score" not in detail

        # The failed checkpoint contributes no verdict at all — in particular
        # not a 0.0 that would read as "the judge disagreed".
        rows = [
            json.loads(line)
            for line in (out / "verdicts.jsonl").read_text().splitlines()
        ]
        assert [r["checkpoint"] for r in rows] == ["cp_ok"]

    def test_non_dict_backend_payload_is_an_infra_error_not_a_score(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`_parse_json` is annotated `-> dict` but cannot deliver on it.

        `backends.py:29` returns `json.loads(text)` directly, and `[1, 2]` or
        `"nope"` are valid JSON that are not objects. The engine then calls
        `response.get("score")` (`engine.py:78`) and raises AttributeError --
        an exception type the judge layer does not document.

        This pins where that lands: the broad except in run_pilot records it as
        an infra error, so it reaches neither a verdict nor a 0.0. It is also
        why the ledger needs no isinstance() guard on raw_response -- a non-dict
        payload never survives to construct a CheckpointVerdict.
        """
        unit = _make_unit(tmp_path, checkpoints={"cp_a": {}})
        stub = _StubJudge(
            {"cp_a": 1.0},
            raises={"cp_a": AttributeError("'list' object has no attribute 'get'")},
        )
        monkeypatch.setattr(pilot, "LLMJudge", lambda **kw: stub)

        out = tmp_path / "out"
        report = pilot.run_pilot([unit], model="stub", seed=1, out_dir=out)

        assert report["sample"]["infra_errors"] == 1
        assert report["sample"]["checkpoints_judged"] == 0
        assert report["infra_errors_detail"][0]["error_type"] == "AttributeError"
        assert (out / "verdicts.jsonl").read_text() == ""

    def test_infra_error_excluded_from_agreement_stats(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed checkpoint must not be banked as perfect disagreement.

        Deterministic says 1.0 on both checkpoints and the judge agrees on the
        one it reached. Coercing the failure to 0.0 would drag pass-agreement to
        0.5 and mean abs-diff to 0.5; excluding it leaves clean agreement.
        """
        unit = _make_unit(
            tmp_path,
            checkpoints={"cp_ok": {}, "cp_broken": {}},
            det_scores={"cp_ok": 1.0, "cp_broken": 1.0},
        )
        stub = _StubJudge(
            {"cp_ok": 1.0}, raises={"cp_broken": JudgeBackendError("boom")}
        )
        monkeypatch.setattr(pilot, "LLMJudge", lambda **kw: stub)

        report = pilot.run_pilot([unit], model="stub", seed=1, out_dir=tmp_path / "out")

        assert report["sample"]["checkpoints_paired_with_deterministic"] == 1
        assert report["checkpoint_agreement"]["pass_agreement_rate"] == 1.0
        assert report["checkpoint_agreement"]["abs_diff"] == {
            "n": 1,
            "mean": 0.0,
            "median": 0.0,
            "max": 0.0,
        }

    def test_undocumented_exception_is_also_recorded_not_fatal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A backend error outside the documented two must not abort the run.

        evaluate_checkpoint documents JudgeBackendError/JudgeScoreError, but the
        SDK can surface a bare connection error. If that escaped the loop, the
        whole run's report.json -- the artifact of record -- would be lost to
        one flaky call. It must degrade to an infra error like any other, and
        name its type so a real bug is not disguised as flakiness.
        """
        unit = _make_unit(
            tmp_path,
            checkpoints={"cp_ok": {}, "cp_broken": {}},
            det_scores={"cp_ok": 1.0, "cp_broken": 1.0},
        )
        stub = _StubJudge(
            {"cp_ok": 1.0}, raises={"cp_broken": ConnectionError("connection reset")}
        )
        monkeypatch.setattr(pilot, "LLMJudge", lambda **kw: stub)

        out = tmp_path / "out"
        report = pilot.run_pilot([unit], model="stub", seed=1, out_dir=out)

        assert (out / "report.json").exists()  # run survived
        assert report["sample"]["infra_errors"] == 1
        detail = report["infra_errors_detail"][0]
        assert detail["error_type"] == "ConnectionError"
        assert "score" not in detail
        assert report["checkpoint_agreement"]["pass_agreement_rate"] == 1.0


# ---------------------------------------------------------------------------
# 3. No blending into the deterministic reward
# ---------------------------------------------------------------------------


class TestNoBlending:
    def test_judge_and_deterministic_stay_separate_columns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Judge 0.0 against deterministic 1.0: the 1.0 must survive intact.

        Any blend — min(), mean(), or overwrite — would show up here as a
        deterministic_total that moved.
        """
        unit = _make_unit(
            tmp_path,
            checkpoints={"cp_a": {}},
            det_scores={"cp_a": 1.0},
            det_total=1.0,
        )
        monkeypatch.setattr(pilot, "LLMJudge", lambda **kw: _StubJudge({"cp_a": 0.0}))

        report = pilot.run_pilot([unit], model="stub", seed=1, out_dir=tmp_path / "out")

        row = report["units"][0]
        assert row["deterministic_total"] == 1.0
        assert row["judge_total"] == 0.0
        assert row["delta"] == -1.0
        assert report["config"]["blending"].startswith("none")

    def test_deterministic_artifacts_are_not_rewritten(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pilot is read-only w.r.t. the run it judges: only out_dir changes."""
        unit = _make_unit(tmp_path, checkpoints={"cp_a": {}})
        answer_before = Path(unit.answer_path).read_bytes()
        expected_path = Path(unit.task_dir) / "expected_solution.json"
        expected_before = expected_path.read_bytes()
        monkeypatch.setattr(pilot, "LLMJudge", lambda **kw: _StubJudge({"cp_a": 0.5}))

        out = tmp_path / "out"
        pilot.run_pilot([unit], model="stub", seed=1, out_dir=out)

        assert Path(unit.answer_path).read_bytes() == answer_before
        assert expected_path.read_bytes() == expected_before
        assert sorted(p.name for p in out.iterdir()) == ["report.json", "verdicts.jsonl"]

    def test_ledger_keeps_the_rationale_production_discards(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """runner.py returns only result.score; the pilot's value is the rest."""
        unit = _make_unit(tmp_path, arm="mcp_only", checkpoints={"cp_a": {}})
        monkeypatch.setattr(pilot, "LLMJudge", lambda **kw: _StubJudge({"cp_a": 0.5}))

        out = tmp_path / "out"
        pilot.run_pilot([unit], model="stub", seed=1, out_dir=out)

        row = json.loads((out / "verdicts.jsonl").read_text().strip())
        assert row["reasoning"] == "stub reasoning for cp_a"
        assert row["evidence"] == "stub evidence for cp_a"
        assert row["confidence"] == "high"
        # Arm appears in the post-hoc ledger only — it never reached the judge.
        assert row["arm"] == "mcp_only"

    def test_ledger_keeps_the_raw_response(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The doc's audit story needs the payload, not just our projection of it.

        `reasoning`/`evidence`/`confidence` are five mapped fields;
        `CheckpointJudgeResult` drops whatever else the model returned. At
        temperature 0 an LLM is only near-deterministic, so "a rescore is
        auditable rather than assumed identical" needs the payload too —
        otherwise a mapping change is indistinguishable from a model change.
        """
        unit = _make_unit(tmp_path, checkpoints={"cp_a": {}})
        monkeypatch.setattr(pilot, "LLMJudge", lambda **kw: _StubJudge({"cp_a": 0.5}))

        out = tmp_path / "out"
        pilot.run_pilot([unit], model="stub", seed=1, out_dir=out)

        row = json.loads((out / "verdicts.jsonl").read_text().strip())
        # The unmapped field is the point: it survives only via raw_response.
        assert row["raw_response"]["notes"] == "unmapped field for cp_a"
        assert row["raw_response"]["score"] == 0.5

    def test_ledger_tolerates_an_empty_payload(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """{} is models.py's own default_factory value, so it is reachable.

        Pinning the empty case matters because it is falsy: any `or`-style
        fallback on the write path would silently substitute something else.
        """
        unit = _make_unit(tmp_path, checkpoints={"cp_a": {}})
        judge = _StubJudge({"cp_a": 0.5})
        judge.raw_payload = {}
        monkeypatch.setattr(pilot, "LLMJudge", lambda **kw: judge)

        out = tmp_path / "out"
        pilot.run_pilot([unit], model="stub", seed=1, out_dir=out)

        row = json.loads((out / "verdicts.jsonl").read_text().strip())
        assert row["raw_response"] == {}


# ---------------------------------------------------------------------------
# 4. Measurement-validity guards
# ---------------------------------------------------------------------------


class TestCoverageDenominator:
    def test_partial_coverage_unit_is_flagged_and_excluded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A delta is only meaningful when both totals share a denominator.

        judge_total renormalizes over the checkpoints the pilot could pair;
        deterministic_total comes from run_log.json over ALL of the run's
        checkpoints. If curated expected_solution.json covers fewer, the delta
        measures coverage, not disagreement — so it must be visible per unit
        and kept out of the aggregate.
        """
        unit = _make_unit(tmp_path, checkpoints={"cp_a": {}}, det_scores={"cp_a": 1.0})
        # run_log knew about a second checkpoint the curated ground truth omits.
        unit.deterministic_checkpoints["cp_absent"] = {"score": 0.0, "weight": 1.0}
        monkeypatch.setattr(pilot, "LLMJudge", lambda **kw: _StubJudge({"cp_a": 1.0}))

        report = pilot.run_pilot([unit], model="stub", seed=1, out_dir=tmp_path / "out")

        row = report["units"][0]
        assert row["checkpoints_covered"] == 1
        assert row["checkpoints_in_run_log"] == 2
        assert row["full_coverage"] is False
        assert report["unit_total_agreement"]["units_excluded_partial_coverage"] == 1
        assert report["unit_total_agreement"]["abs_delta"]["n"] == 0

    def test_full_coverage_unit_counts_toward_aggregate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        unit = _make_unit(tmp_path, checkpoints={"cp_a": {}}, det_scores={"cp_a": 1.0})
        monkeypatch.setattr(pilot, "LLMJudge", lambda **kw: _StubJudge({"cp_a": 1.0}))

        report = pilot.run_pilot([unit], model="stub", seed=1, out_dir=tmp_path / "out")

        assert report["units"][0]["full_coverage"] is True
        assert report["unit_total_agreement"]["units_excluded_partial_coverage"] == 0
        assert report["unit_total_agreement"]["abs_delta"]["n"] == 1


class TestReproducibility:
    def test_same_seed_replays_identically(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The seed is claimed as a protocol property, so pin it.

        Same seed must reproduce the ledger byte-for-byte, ordering included.
        """
        checkpoints = {f"cp_{i}": {} for i in range(6)}
        scores = {name: 0.5 for name in checkpoints}
        units = [
            _make_unit(
                tmp_path, task_id=f"task-{i:03d}", arm=arm, checkpoints=checkpoints
            )
            for i, arm in enumerate(["baseline", "mcp", "hybrid", "cli"])
        ]
        monkeypatch.setattr(pilot, "LLMJudge", lambda **kw: _StubJudge(scores))

        first = tmp_path / "run1"
        second = tmp_path / "run2"
        pilot.run_pilot(units, model="stub", seed=99, out_dir=first)
        pilot.run_pilot(units, model="stub", seed=99, out_dir=second)

        assert (first / "verdicts.jsonl").read_text() == (
            second / "verdicts.jsonl"
        ).read_text()

    def test_different_seed_changes_processing_order(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Guards the assertion above against trivially passing on a no-op shuffle."""
        checkpoints = {f"cp_{i}": {} for i in range(6)}
        scores = {name: 0.5 for name in checkpoints}
        units = [
            _make_unit(
                tmp_path, task_id=f"task-{i:03d}", arm=arm, checkpoints=checkpoints
            )
            for i, arm in enumerate(["baseline", "mcp", "hybrid", "cli"])
        ]
        monkeypatch.setattr(pilot, "LLMJudge", lambda **kw: _StubJudge(scores))

        def order(out: Path, seed: int) -> list[str]:
            pilot.run_pilot(units, model="stub", seed=seed, out_dir=out)
            return [
                json.loads(line)["task_id"]
                for line in (out / "verdicts.jsonl").read_text().splitlines()
            ]

        assert order(tmp_path / "a", 1) != order(tmp_path / "b", 12345)


class TestCorruptGroundTruthIsLoud:
    def test_malformed_json_warns_rather_than_vanishing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """A corrupt fixture must not be indistinguishable from an absent one.

        Both degrade to "no ground truth", but only the corrupt case is a bug
        someone needs to see.
        """
        bad = tmp_path / "expected_solution.json"
        bad.write_text("{not valid json")

        assert pilot._read_json(str(bad)) is None
        assert "unreadable JSON" in capsys.readouterr().err

    def test_missing_file_is_silent(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        assert pilot._read_json(str(tmp_path / "nope.json")) is None
        assert capsys.readouterr().err == ""
