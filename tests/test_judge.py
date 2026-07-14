"""Unit tests for lib/eb_verify/judge/* — the live Tier-2 LLM scorer.

Covers (per EnterpriseBench-u3jb / DEEP_AUDIT F2):
  1. backend dispatch (factory + SDK/urllib fallback) with the SDK mocked — no live API
  2. _parse_json on bare / fenced / malformed / truncated JSON
  3. prompt assembly from CheckpointJudgeInput (incl. agent_output truncation)
  4. judge-init-failure handling — the judge surfaces init failure by RAISING
     (the contract the caller relies on to flag verifier_failure rather than
     silently disable Tier-2)

All backends are mocked at their IO boundary; nothing here hits the network,
the Anthropic SDK, or the claude CLI.
"""

from __future__ import annotations

import subprocess
import sys
import types
import urllib.error
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure lib/ is importable (mirrors tests/test_eb_verify_smoke.py).
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from eb_verify.judge import CheckpointJudgeInput, CheckpointJudgeResult, LLMJudge
from eb_verify.judge.backends import (
    AnthropicBackend,
    ClaudeCodeBackend,
    JudgeBackendError,
    _parse_json,
    create_backend,
)
from eb_verify.judge.models import JudgeScoreError, validate_score
from eb_verify.judge.prompts import CHECKPOINT_EVAL_SYSTEM


# ---------------------------------------------------------------------------
# _parse_json
# ---------------------------------------------------------------------------

class TestParseJson:
    def test_bare_json(self):
        assert _parse_json('{"score": 1.0}') == {"score": 1.0}

    def test_bare_json_with_surrounding_whitespace(self):
        assert _parse_json('  \n {"score": 0.5}\n  ') == {"score": 0.5}

    def test_fenced_json_block(self):
        raw = 'Here is my answer:\n```json\n{"score": 0.5, "passed": false}\n```\n'
        assert _parse_json(raw) == {"score": 0.5, "passed": False}

    def test_fenced_plain_block(self):
        raw = '```\n{"score": 0.0}\n```'
        assert _parse_json(raw) == {"score": 0.0}

    def test_malformed_raises(self):
        with pytest.raises(JudgeBackendError, match="Failed to parse JSON"):
            _parse_json("this is not json at all")

    def test_truncated_json_raises(self):
        # Partial / cut-off response — neither bare nor fenced parse succeeds.
        with pytest.raises(JudgeBackendError, match="Failed to parse JSON"):
            _parse_json('{"score": 1.0, "reasoning": "the agent')

    def test_fenced_block_with_invalid_inner_json_raises(self):
        with pytest.raises(JudgeBackendError, match="Failed to parse JSON"):
            _parse_json("```json\nnot valid {{{ json\n```")


# ---------------------------------------------------------------------------
# validate_score
# ---------------------------------------------------------------------------

class TestValidateScore:
    @pytest.mark.parametrize("val,expected", [(0.5, 0.5), (1.0, 1.0), (0.0, 0.0), (1, 1.0)])
    def test_real_scores_pass_through(self, val, expected):
        assert validate_score(val) == expected

    @pytest.mark.parametrize("val", [1.0 + 1e-9, -1e-9])
    def test_float_slop_at_the_bounds_is_trimmed(self, val):
        """A judge's rounding can land a hair outside; that is not a broken judge."""
        assert validate_score(val) in (0.0, 1.0)

    @pytest.mark.parametrize(
        "val",
        [
            # Over-credit: a clamp turns each of these into a free 1.0.
            float("nan"),  # min(1.0, nan) is 1.0 in CPython
            float("inf"),
            True,          # float(True) == 1.0, i.e. {"score": true}
            999,
            1.5,
            "1.0",         # a string is not a score, even when it parses
            # Under-credit: a clamp turns each of these into a false 0.0.
            None,          # judge response carried no "score" key
            "not a number",
            -0.3,
            object(),
        ],
    )
    def test_non_score_raises(self, val):
        with pytest.raises(JudgeScoreError):
            validate_score(val)

    def test_message_names_the_offending_value(self):
        """Operators triaging a re-run must see what the judge actually said."""
        with pytest.raises(JudgeScoreError, match="999"):
            validate_score(999)


# ---------------------------------------------------------------------------
# create_backend factory dispatch
# ---------------------------------------------------------------------------

class TestCreateBackend:
    def test_claude_model_returns_anthropic_backend(self):
        backend = create_backend("claude-haiku-4-5-20251001")
        assert isinstance(backend, AnthropicBackend)
        assert backend.model == "claude-haiku-4-5-20251001"

    def test_anthropic_backend_forwards_params(self):
        backend = create_backend("claude-sonnet-4-6", temperature=0.7, max_tokens=2048)
        assert isinstance(backend, AnthropicBackend)
        assert backend.temperature == 0.7
        assert backend.max_tokens == 2048

    @patch("eb_verify.judge.backends.shutil.which", return_value="/usr/bin/claude")
    def test_claude_code_alias_returns_cc_backend(self, _which):
        for alias in ("claude-code", "cc"):
            backend = create_backend(alias)
            assert isinstance(backend, ClaudeCodeBackend)
            # default submodel is haiku
            assert backend.model == "haiku"

    @patch("eb_verify.judge.backends.shutil.which", return_value="/usr/bin/claude")
    def test_cc_submodel_dispatch(self, _which):
        backend = create_backend("cc:sonnet")
        assert isinstance(backend, ClaudeCodeBackend)
        assert backend.model == "sonnet"

    def test_unknown_provider_raises_valueerror(self):
        with pytest.raises(ValueError, match="Cannot determine provider"):
            create_backend("gpt-4o")


@contextmanager
def _fake_anthropic_module():
    """Inject a fake `anthropic` module into sys.modules for the duration.

    `_call_sdk` does `import anthropic` at call time, so a stand-in module
    lets us exercise the SDK error-mapping branches without the real SDK.
    """
    fake = types.ModuleType("anthropic")

    class RateLimitError(Exception):
        pass

    class APIStatusError(Exception):
        def __init__(self, message="", status_code=None):
            super().__init__(message)
            self.status_code = status_code

    fake.Anthropic = MagicMock()
    fake.RateLimitError = RateLimitError
    fake.APIStatusError = APIStatusError
    with patch.dict(sys.modules, {"anthropic": fake}):
        yield fake


# ---------------------------------------------------------------------------
# AnthropicBackend.call — retry + parse, with SDK/urllib mocked
# ---------------------------------------------------------------------------

class TestAnthropicBackend:
    def test_call_parses_raw_response(self):
        backend = AnthropicBackend(model="claude-haiku-4-5-20251001")
        with patch.object(backend, "_raw_call", return_value='{"score": 1.0}'):
            assert backend.call("sys", "user") == {"score": 1.0}

    @patch("eb_verify.judge.backends.time.sleep")
    def test_call_retries_on_429_then_succeeds(self, sleep):
        backend = AnthropicBackend(model="claude-haiku-4-5-20251001")
        attempts = [
            JudgeBackendError("rate limited", status_code=429),
            '{"score": 0.5}',
        ]

        def side_effect(_sys, _user):
            item = attempts.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with patch.object(backend, "_raw_call", side_effect=side_effect):
            assert backend.call("sys", "user") == {"score": 0.5}
        sleep.assert_called_once()  # backed off exactly once before the retry

    @patch("eb_verify.judge.backends.time.sleep")
    def test_call_retries_on_5xx(self, sleep):
        backend = AnthropicBackend(model="claude-haiku-4-5-20251001")
        attempts = [
            JudgeBackendError("server error", status_code=503),
            '{"score": 0.0}',
        ]

        def side_effect(_sys, _user):
            item = attempts.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with patch.object(backend, "_raw_call", side_effect=side_effect):
            assert backend.call("sys", "user") == {"score": 0.0}
        sleep.assert_called_once()  # backed off once before the 5xx retry

    def test_call_does_not_retry_on_4xx_client_error(self):
        backend = AnthropicBackend(model="claude-haiku-4-5-20251001")
        err = JudgeBackendError("bad request", status_code=400)
        with patch.object(backend, "_raw_call", side_effect=err) as mock_raw:
            with pytest.raises(JudgeBackendError, match="bad request"):
                backend.call("sys", "user")
        mock_raw.assert_called_once()  # 4xx must NOT be retried

    @patch("eb_verify.judge.backends.time.sleep")
    def test_call_exhausts_retries_and_raises(self, _sleep):
        backend = AnthropicBackend(model="claude-haiku-4-5-20251001")
        err = JudgeBackendError("still down", status_code=500)
        with patch.object(backend, "_raw_call", side_effect=err):
            with pytest.raises(JudgeBackendError, match="Exhausted 3 retries"):
                backend.call("sys", "user")

    def test_raw_call_falls_back_to_urllib_on_sdk_importerror(self):
        backend = AnthropicBackend(model="claude-haiku-4-5-20251001")
        with patch.object(backend, "_call_sdk", side_effect=ImportError("no sdk")), patch.object(
            backend, "_call_urllib", return_value='{"score": 1.0}'
        ) as urllib_call:
            assert backend._raw_call("sys", "user") == '{"score": 1.0}'
            urllib_call.assert_called_once_with("sys", "user")

    def test_urllib_without_api_key_raises(self):
        backend = AnthropicBackend(model="claude-haiku-4-5-20251001")
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(JudgeBackendError, match="ANTHROPIC_API_KEY not set"):
                backend._call_urllib("sys", "user")

    def test_urllib_success_returns_text(self):
        backend = AnthropicBackend(model="claude-haiku-4-5-20251001")
        body = b'{"content": [{"text": "{\\"score\\": 1.0}"}]}'
        resp_cm = MagicMock()
        resp_cm.__enter__.return_value.read.return_value = body
        resp_cm.__exit__.return_value = False
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}), patch(
            "urllib.request.urlopen", return_value=resp_cm
        ):
            assert backend._call_urllib("sys", "user") == '{"score": 1.0}'

    def test_urllib_http_error_maps_status_code(self):
        backend = AnthropicBackend(model="claude-haiku-4-5-20251001")
        http_err = urllib.error.HTTPError(
            url="https://api.anthropic.com/v1/messages",
            code=429,
            msg="Too Many Requests",
            hdrs=None,
            fp=BytesIO(b"rate limited"),
        )
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}), patch(
            "urllib.request.urlopen", side_effect=http_err
        ):
            with pytest.raises(JudgeBackendError) as excinfo:
                backend._call_urllib("sys", "user")
        assert excinfo.value.status_code == 429

    def test_sdk_call_returns_text(self):
        backend = AnthropicBackend(model="claude-haiku-4-5-20251001")
        with _fake_anthropic_module() as fake:
            fake.Anthropic.return_value.messages.create.return_value = types.SimpleNamespace(
                content=[types.SimpleNamespace(text='{"score": 0.5}')]
            )
            assert backend._call_sdk("sys", "user") == '{"score": 0.5}'

    def test_sdk_rate_limit_maps_to_429(self):
        backend = AnthropicBackend(model="claude-haiku-4-5-20251001")
        with _fake_anthropic_module() as fake:
            fake.Anthropic.return_value.messages.create.side_effect = fake.RateLimitError(
                "slow down"
            )
            with pytest.raises(JudgeBackendError) as excinfo:
                backend._call_sdk("sys", "user")
        assert excinfo.value.status_code == 429

    def test_sdk_api_status_error_preserves_status_code(self):
        backend = AnthropicBackend(model="claude-haiku-4-5-20251001")
        with _fake_anthropic_module() as fake:
            err = fake.APIStatusError("server fault")
            err.status_code = 503
            fake.Anthropic.return_value.messages.create.side_effect = err
            with pytest.raises(JudgeBackendError) as excinfo:
                backend._call_sdk("sys", "user")
        assert excinfo.value.status_code == 503


# ---------------------------------------------------------------------------
# ClaudeCodeBackend — init failure + subprocess envelope handling
# ---------------------------------------------------------------------------

class TestClaudeCodeBackend:
    @patch("eb_verify.judge.backends.shutil.which", return_value=None)
    def test_init_raises_when_cli_missing(self, _which):
        with pytest.raises(JudgeBackendError, match="claude CLI not found"):
            ClaudeCodeBackend(model="haiku")

    @patch("eb_verify.judge.backends.shutil.which", return_value="/usr/bin/claude")
    def test_init_maps_model_alias(self, _which):
        backend = ClaudeCodeBackend(model="claude-sonnet-4-6")
        assert backend.model == "sonnet"

    @patch("eb_verify.judge.backends.shutil.which", return_value="/usr/bin/claude")
    def test_raw_call_success(self, _which):
        backend = ClaudeCodeBackend(model="haiku")
        envelope = '{"is_error": false, "result": "{\\"score\\": 1.0}"}'
        completed = subprocess.CompletedProcess([], 0, stdout=envelope, stderr="")
        with patch("eb_verify.judge.backends.subprocess.run", return_value=completed):
            assert backend._raw_call("sys", "user") == {"score": 1.0}

    @patch("eb_verify.judge.backends.shutil.which", return_value="/usr/bin/claude")
    def test_raw_call_nonzero_returncode_raises(self, _which):
        backend = ClaudeCodeBackend(model="haiku")
        completed = subprocess.CompletedProcess([], 1, stdout="", stderr="boom")
        with patch("eb_verify.judge.backends.subprocess.run", return_value=completed):
            with pytest.raises(JudgeBackendError, match="claude CLI failed"):
                backend._raw_call("sys", "user")

    @patch("eb_verify.judge.backends.shutil.which", return_value="/usr/bin/claude")
    def test_raw_call_timeout_raises(self, _which):
        backend = ClaudeCodeBackend(model="haiku")
        with patch(
            "eb_verify.judge.backends.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=120),
        ):
            with pytest.raises(JudgeBackendError, match="timed out"):
                backend._raw_call("sys", "user")

    @patch("eb_verify.judge.backends.shutil.which", return_value="/usr/bin/claude")
    def test_raw_call_error_envelope_raises(self, _which):
        backend = ClaudeCodeBackend(model="haiku")
        envelope = '{"is_error": true, "result": "context too long"}'
        completed = subprocess.CompletedProcess([], 0, stdout=envelope, stderr="")
        with patch("eb_verify.judge.backends.subprocess.run", return_value=completed):
            with pytest.raises(JudgeBackendError, match="context too long"):
                backend._raw_call("sys", "user")

    @patch("eb_verify.judge.backends.shutil.which", return_value="/usr/bin/claude")
    def test_raw_call_rate_limit_envelope_sets_429(self, _which):
        backend = ClaudeCodeBackend(model="haiku")
        envelope = '{"is_error": true, "result": "rate limit exceeded"}'
        completed = subprocess.CompletedProcess([], 0, stdout=envelope, stderr="")
        with patch("eb_verify.judge.backends.subprocess.run", return_value=completed):
            with pytest.raises(JudgeBackendError) as excinfo:
                backend._raw_call("sys", "user")
        assert excinfo.value.status_code == 429

    @patch("eb_verify.judge.backends.shutil.which", return_value="/usr/bin/claude")
    def test_raw_call_empty_result_raises(self, _which):
        backend = ClaudeCodeBackend(model="haiku")
        envelope = '{"is_error": false, "result": ""}'
        completed = subprocess.CompletedProcess([], 0, stdout=envelope, stderr="")
        with patch("eb_verify.judge.backends.subprocess.run", return_value=completed):
            with pytest.raises(JudgeBackendError, match="empty result"):
                backend._raw_call("sys", "user")

    @patch("eb_verify.judge.backends.shutil.which", return_value="/usr/bin/claude")
    def test_raw_call_unparseable_stdout_raises(self, _which):
        backend = ClaudeCodeBackend(model="haiku")
        completed = subprocess.CompletedProcess([], 0, stdout="not json", stderr="")
        with patch("eb_verify.judge.backends.subprocess.run", return_value=completed):
            with pytest.raises(JudgeBackendError, match="parse claude CLI output"):
                backend._raw_call("sys", "user")

    @patch("eb_verify.judge.backends.time.sleep")
    @patch("eb_verify.judge.backends.shutil.which", return_value="/usr/bin/claude")
    def test_call_retries_on_429_then_succeeds(self, _which, sleep):
        backend = ClaudeCodeBackend(model="haiku")
        attempts = [
            JudgeBackendError("rate limited", status_code=429),
            {"score": 1.0},
        ]

        def side_effect(_sys, _user):
            item = attempts.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with patch.object(backend, "_raw_call", side_effect=side_effect):
            assert backend.call("sys", "user") == {"score": 1.0}
        sleep.assert_called_once()

    @patch("eb_verify.judge.backends.shutil.which", return_value="/usr/bin/claude")
    def test_call_does_not_retry_on_non_retryable(self, _which):
        backend = ClaudeCodeBackend(model="haiku")
        err = JudgeBackendError("CLI failed", status_code=None)
        with patch.object(backend, "_raw_call", side_effect=err) as mock_raw:
            with pytest.raises(JudgeBackendError, match="CLI failed"):
                backend.call("sys", "user")
        mock_raw.assert_called_once()  # no status_code => not retryable

    @patch("eb_verify.judge.backends.time.sleep")
    @patch("eb_verify.judge.backends.shutil.which", return_value="/usr/bin/claude")
    def test_call_exhausts_retries_and_raises(self, _which, _sleep):
        backend = ClaudeCodeBackend(model="haiku")
        err = JudgeBackendError("still rate limited", status_code=429)
        with patch.object(backend, "_raw_call", side_effect=err):
            with pytest.raises(JudgeBackendError, match="Exhausted 3 retries"):
                backend.call("sys", "user")


# ---------------------------------------------------------------------------
# LLMJudge — prompt assembly, scoring, backend-error handling, init failure
# ---------------------------------------------------------------------------

def _make_judge_with_mock_backend(response: dict) -> tuple[LLMJudge, MagicMock]:
    """Build an LLMJudge whose backend is a mock returning `response`.

    Uses an Anthropic model so __init__ does no IO, then swaps the backend.
    """
    judge = LLMJudge(model="claude-haiku-4-5-20251001")
    mock_backend = MagicMock()
    mock_backend.call.return_value = response
    judge._backend = mock_backend
    return judge, mock_backend


class TestLLMJudgePromptAssembly:
    def test_prompt_includes_checkpoint_fields(self):
        judge, backend = _make_judge_with_mock_backend(
            {"score": 1.0, "reasoning": "ok", "evidence": "e", "confidence": "high"}
        )
        judge.evaluate_checkpoint(
            CheckpointJudgeInput(
                task_id="incident-inv-004",
                checkpoint_name="root_cause",
                agent_output="the bug is in monitor.go line 42",
                expected_solution="monitor.go heartbeat goroutine leak",
                evaluation_criteria=["Must identify monitor.go", "Must cite goroutine"],
            ),
            task_description="Find the root cause of the incident",
        )
        system_arg, user_arg = backend.call.call_args.args
        assert system_arg == CHECKPOINT_EVAL_SYSTEM
        assert "root_cause" in user_arg
        assert "monitor.go heartbeat goroutine leak" in user_arg
        assert "Find the root cause of the incident" in user_arg
        # criteria rendered as a bulleted list
        assert "- Must identify monitor.go" in user_arg
        assert "- Must cite goroutine" in user_arg

    def test_empty_criteria_renders_placeholder(self):
        judge, backend = _make_judge_with_mock_backend({"score": 0.0})
        judge.evaluate_checkpoint(
            CheckpointJudgeInput(
                task_id="t",
                checkpoint_name="cp",
                agent_output="out",
                expected_solution="sol",
                evaluation_criteria=[],
            )
        )
        _system, user_arg = backend.call.call_args.args
        assert "(none provided)" in user_arg

    def test_agent_output_truncated_to_12000_chars(self):
        judge, backend = _make_judge_with_mock_backend({"score": 0.0})
        long_output = "x" * 20000
        judge.evaluate_checkpoint(
            CheckpointJudgeInput(
                task_id="t",
                checkpoint_name="cp",
                agent_output=long_output,
                expected_solution="sol",
            )
        )
        _system, user_arg = backend.call.call_args.args
        # exactly 12000 'x' kept, not 20000
        assert "x" * 12000 in user_arg
        assert "x" * 12001 not in user_arg


class TestLLMJudgeScoring:
    def test_passing_result_populated_from_response(self):
        judge, _ = _make_judge_with_mock_backend(
            {
                "score": 1.0,
                "reasoning": "matched file paths",
                "evidence": "monitor.go:42",
                "confidence": "high",
            }
        )
        result = judge.evaluate_checkpoint(
            CheckpointJudgeInput(
                task_id="t", checkpoint_name="root_cause",
                agent_output="o", expected_solution="s",
            )
        )
        assert isinstance(result, CheckpointJudgeResult)
        assert result.score == 1.0
        assert result.passed is True
        assert result.reasoning == "matched file paths"
        assert result.evidence == "monitor.go:42"
        assert result.confidence == "high"
        assert result.model == "claude-haiku-4-5-20251001"

    def test_score_below_threshold_fails(self):
        judge, _ = _make_judge_with_mock_backend({"score": 0.0})
        result = judge.evaluate_checkpoint(
            CheckpointJudgeInput(
                task_id="t", checkpoint_name="cp", agent_output="o", expected_solution="s"
            )
        )
        assert result.score == 0.0
        assert result.passed is False

    def test_score_at_threshold_passes(self):
        judge, _ = _make_judge_with_mock_backend({"score": 0.5})
        result = judge.evaluate_checkpoint(
            CheckpointJudgeInput(
                task_id="t", checkpoint_name="cp", agent_output="o", expected_solution="s"
            )
        )
        assert result.passed is True  # pass_threshold defaults to 0.5

    @pytest.mark.parametrize("bad", [5.0, float("nan"), True, "1.0"])
    def test_non_score_response_raises_instead_of_scoring(self, bad):
        """Over-credit regression: each of these used to clamp to a free 1.0."""
        judge, _ = _make_judge_with_mock_backend({"score": bad})
        with pytest.raises(JudgeScoreError):
            judge.evaluate_checkpoint(
                CheckpointJudgeInput(
                    task_id="t", checkpoint_name="cp", agent_output="o", expected_solution="s"
                )
            )

    def test_response_without_a_score_raises(self):
        judge, _ = _make_judge_with_mock_backend({"reasoning": "forgot to score"})
        with pytest.raises(JudgeScoreError):
            judge.evaluate_checkpoint(
                CheckpointJudgeInput(
                    task_id="t", checkpoint_name="cp", agent_output="o", expected_solution="s"
                )
            )

    def test_backend_error_propagates_and_is_never_scored_zero(self):
        """Under-credit regression: a judge OUTAGE is our failure, not the
        agent's. It used to be recorded as a legitimate 0.0, which — through
        min(grep, judge) — zeroed every checkpoint of the run."""
        judge = LLMJudge(model="claude-haiku-4-5-20251001")
        backend = MagicMock()
        backend.call.side_effect = JudgeBackendError("backend exploded")
        judge._backend = backend

        with pytest.raises(JudgeBackendError, match="backend exploded"):
            judge.evaluate_checkpoint(
                CheckpointJudgeInput(
                    task_id="t", checkpoint_name="cp", agent_output="o", expected_solution="s"
                )
            )


class TestLLMJudgeInitFailure:
    """Init failure must SURFACE (raise), not silently disable Tier-2.

    The caller (_apply_llm_judge in run_task.py) relies on the constructor
    raising so it can distinguish a real verifier failure from a no-op. A
    judge that swallowed the failure would let uncapped grep scores through.
    """

    @patch("eb_verify.judge.backends.shutil.which", return_value=None)
    def test_cc_backend_init_failure_propagates(self, _which):
        with pytest.raises(JudgeBackendError, match="claude CLI not found"):
            LLMJudge(model="cc:haiku")

    def test_unknown_model_init_raises_valueerror(self):
        with pytest.raises(ValueError, match="Cannot determine provider"):
            LLMJudge(model="gpt-4o")
