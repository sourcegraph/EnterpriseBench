"""Neither invalidity channel may publish a secret, or an unbounded blob.

``session_failure`` and ``verifier_infra_error`` both carry a ``detail`` built
from text the harness does not author — an agent's exception, a verifier's
stderr, a judge's HTTP error body — and each fans out to three sinks that keep
it verbatim: ``chain_result.json`` (persisted and aggregated across runs),
stdout via ``ChainResult.summary``, and ``logger.error``. The harness runs
agents with ``ANTHROPIC_API_KEY`` in their environment, so the exception this
path catches can embed a live credential:

    Session 1: FAIL (401 {"error": {"message": "invalid x-api-key sk-ant-..."}})

``run_task`` already refuses to put agent secrets in ps-visible argv; this is
the same control on the failure path, which had none (bead otgzo).

Both channels are covered because they are one rule, not two: ``InfraError``
scrubs on construction and ``chain_runner`` builds ``session_failure`` from an
``InfraError`` too. The session channel needs its own scrub at birth regardless
— ``ChainResult.summary`` reads ``SessionResult.error`` directly, never passing
through ``InfraError`` at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "lib"))

from eb_verify.redact import MAX_DETAIL_CHARS, bound, redact, safe_detail
from eb_verify.scorer_guard import InfraError, guard_checkpoint_verdict
from orchestration.chain_runner import ChainTaskDefinition, run_chain
from orchestration.session import AgentFailure, SessionConfig

# Shaped like a real Anthropic key so the pattern is exercised, but not one.
FAKE_KEY = "sk-ant-api03-" + "A" * 40


def task_def() -> ChainTaskDefinition:
    """The smallest chain that reaches the session channel: one session, and the
    one final checkpoint every chain must declare. Built here rather than imported
    from its sibling module, per this corpus's convention of keeping each vector's
    fixtures standalone.

    On the infra vectors the checkpoint never runs — the session dies first and an
    infra failure gates the final checkpoints off — so its verifier stays a path
    that satisfies validation and nothing more. The agent vectors DO run it, and
    call ``write_checkpoint`` to put a real script at that path.
    """
    return ChainTaskDefinition(
        task_id="chain-redaction",
        suite="customer_escalation",
        difficulty="medium",
        session_count=1,
        repos=[{"path": "repo-a"}],
        sessions=[SessionConfig(session_number=1, prompt="session 1")],
        final_checkpoints=[{"name": "cp", "weight": 1.0, "verifier": "checks/cp.sh"}],
    )


# ---------------------------------------------------------------------------
# redact — the secret shapes, and what must survive
# ---------------------------------------------------------------------------


class TestRedact:
    def test_anthropic_key_is_destroyed(self) -> None:
        assert FAKE_KEY not in redact(f"401 invalid x-api-key {FAKE_KEY}")

    def test_bearer_token_is_destroyed(self) -> None:
        out = redact("Authorization: Bearer abc123.def-456_ghi")
        assert "abc123.def-456_ghi" not in out
        assert "Bearer [REDACTED]" in out

    def test_aws_access_key_id_is_destroyed(self) -> None:
        assert "AKIAIOSFODNN7EXAMPLE" not in redact("creds AKIAIOSFODNN7EXAMPLE here")

    def test_env_dump_keeps_the_name_and_destroys_the_value(self) -> None:
        """An operator still needs to learn WHICH credential leaked into the
        failure path; only its value is destroyed."""
        out = redact("env: ANTHROPIC_API_KEY=hunter2 PATH=/usr/bin")
        assert "hunter2" not in out
        assert "ANTHROPIC_API_KEY=[REDACTED]" in out
        assert "PATH=/usr/bin" in out, "a non-secret env var is not a secret"

    def test_json_error_body_value_is_destroyed(self) -> None:
        """The judge path formats a raised HTTP error straight into `detail`."""
        out = redact('{"error": {"api_key": "sk-ant-xyz", "token": "t0ps3cret"}}')
        assert "sk-ant-xyz" not in out
        assert "t0ps3cret" not in out

    @staticmethod
    def _unchanged(text: str) -> None:
        assert redact(text) == text

    def test_real_failure_text_is_not_mangled(self) -> None:
        """The negative control. Redaction runs on EVERY infra error, so
        over-reaching here would corrupt the diagnostics of every unrelated
        failure the guard reports."""
        for text in (
            "checkpoint 'cp' carries no verifier_ran=true attestation",
            "test.sh produced no output (exit 137)",
            "verifier could not import the eb_verify harness (exit 1)",
            "verifier timed out after 30.0s",
            "verifier 'score' was not a real number in [0.0, 1.0]: nan",
        ):
            self._unchanged(text)

    def test_is_idempotent(self) -> None:
        """A string may cross more than one channel boundary."""
        once = redact(f"key={FAKE_KEY}")
        assert redact(once) == once


# ---------------------------------------------------------------------------
# bound — a hard cap, not an approximate one
# ---------------------------------------------------------------------------


class TestBound:
    def test_short_text_is_untouched(self) -> None:
        assert bound("brief") == "brief"

    def test_long_text_is_capped_and_says_so(self) -> None:
        out = bound("x" * 100_000)
        assert len(out) == MAX_DETAIL_CHARS, "the cap is the cap, note included"
        assert out.endswith("[truncated]"), "a silent cut reads as the whole story"

    def test_is_idempotent(self) -> None:
        """The note fits INSIDE the cap precisely so re-bounding is a no-op —
        otherwise a string crossing two boundaries collects two notes."""
        once = bound("x" * 100_000)
        assert bound(once) == once

    def test_secret_past_the_cap_cannot_survive_the_cut(self) -> None:
        """Order-of-operations regression: redact-then-bound. A key sliced
        mid-token stops matching its own pattern, so bounding first would let
        the fragment through as plaintext."""
        out = safe_detail("x" * (MAX_DETAIL_CHARS - 10) + "AKIAIOSFODNN7EXAMPLE")
        assert "AKIA" not in out


# ---------------------------------------------------------------------------
# verifier_infra_error — scrubbed on construction, so no caller can forget
# ---------------------------------------------------------------------------


class TestInfraErrorChannel:
    def test_detail_is_redacted_on_construction(self) -> None:
        err = InfraError(reason="r", stage="s", detail=f"boom {FAKE_KEY}")
        assert FAKE_KEY not in err.detail
        assert FAKE_KEY not in err.as_verifier_error()["detail"]

    def test_detail_is_bounded_on_construction(self) -> None:
        err = InfraError(reason="r", stage="s", detail="x" * 100_000)
        assert len(err.detail) == MAX_DETAIL_CHARS

    def test_context_evidence_is_redacted_at_any_depth(self) -> None:
        """`stderr` and `raw_output` are the verifier's own bytes, and ride in
        `context` rather than `detail`."""
        err = InfraError(
            reason="r",
            stage="s",
            detail="ok",
            context={
                "stderr": f"traceback: {FAKE_KEY}",
                "candidates": [f"/tmp/{FAKE_KEY}"],
                "nested": {"raw_output": FAKE_KEY},
            },
        )
        assert FAKE_KEY not in str(err.as_verifier_error())

    def test_non_string_context_keeps_its_type(self) -> None:
        """The negative control: `returncode` must stay an int for the results
        payload, and no number can carry a secret."""
        err = InfraError(
            reason="r",
            stage="s",
            detail="ok",
            context={"returncode": 137, "timeout_seconds": 30.0, "ran": False},
        )
        payload = err.as_verifier_error()
        assert payload["returncode"] == 137
        assert payload["timeout_seconds"] == 30.0
        assert payload["ran"] is False

    def test_verifier_stderr_reaches_the_channel_scrubbed(self) -> None:
        """End-to-end through the real guard: a verifier that dumps its env."""
        out = guard_checkpoint_verdict(
            stdout="",
            returncode=1,
            stderr=f"Traceback: auth failed with ANTHROPIC_API_KEY={FAKE_KEY}",
        )
        assert isinstance(out, InfraError)
        assert FAKE_KEY not in str(out.as_verifier_error())


# ---------------------------------------------------------------------------
# session_failure — the same rule, through the whole chain
# ---------------------------------------------------------------------------


def agent_raising(message: str):
    def agent(workspace: str, prompt: str) -> str:
        raise RuntimeError(message)

    return agent


class TestSessionFailureChannel:
    def _run(self, tmp_path: Path, message: str):
        return run_chain(
            task_def(),
            workspace_root=str(tmp_path / "ws"),
            agent_callable=agent_raising(message),
            task_dir=str(tmp_path),
        )

    def test_agent_exception_reaches_no_sink_with_its_secret(self, tmp_path) -> None:
        """THE BUG, at the session channel. The agent runs with the API key in
        its environment; an HTTP error it raises embeds the key, and all three
        sinks used to take it verbatim."""
        result = self._run(tmp_path, f'401 {{"message": "invalid {FAKE_KEY}"}}')

        assert result.session_failure is not None
        assert FAKE_KEY not in result.session_failure["detail"], "persisted sink"
        assert FAKE_KEY not in result.summary(), "stdout sink"
        assert FAKE_KEY not in result.session_results[0].error, "the source field"

    def test_summary_is_covered_though_it_bypasses_infra_error(self, tmp_path) -> None:
        """`summary` prints `SessionResult.error` directly, never touching the
        `session_failure` dict. Scrubbing only at `InfraError` would look like a
        fix and leave this path publishing the raw exception."""
        result = self._run(tmp_path, f"boom {FAKE_KEY}")
        assert FAKE_KEY not in result.summary()

    def test_runaway_exception_cannot_flood_the_sinks(self, tmp_path) -> None:
        result = self._run(tmp_path, "x" * 100_000)
        assert len(result.session_failure["detail"]) == MAX_DETAIL_CHARS
        assert len(result.session_results[0].error) == MAX_DETAIL_CHARS

    def test_the_cause_still_reads_as_the_cause(self, tmp_path) -> None:
        """The negative control: scrubbing must not cost an operator the reason
        the session died."""
        result = self._run(tmp_path, "connection refused to localhost:8080")
        assert "connection refused to localhost:8080" in result.session_failure["detail"]


# ---------------------------------------------------------------------------
# agent_failure — not an invalidity channel, but the same untrusted text
# ---------------------------------------------------------------------------


def write_checkpoint(task_dir: Path) -> None:
    """Materialize the checkpoint ``task_def`` only declares.

    An agent failure is SCORED, so unlike the infra vectors it runs the final
    checkpoints. Left as a bare path the verifier would reach no verdict, set
    ``verifier_infra_error``, and divert these tests into the channel above —
    where they would pass while asserting nothing about the agent one.
    """
    checks = task_dir / "checks"
    checks.mkdir(exist_ok=True)
    script = checks / "cp.sh"
    script.write_text('#!/bin/sh\necho \'{"score": 0.0}\'\nexit 0\n')
    script.chmod(0o755)


class TestAgentFailureChannel:
    """The declared channel carries the same untrusted text, and one field more.

    Declaring whose fault the failure was says nothing about what text it
    carries: an adapter builds ``detail`` from the provider's response exactly as
    an undeclared exception does. ``reason`` is the field the channel above never
    has to scrub — every infra reason is the harness constant ``f"{stage}_error"``,
    so an ``AgentFailure``'s adapter-supplied key is scrubbed here or nowhere.
    """

    def _run(self, tmp_path: Path, reason: str, detail: str):
        write_checkpoint(tmp_path)

        def agent(workspace: str, prompt: str) -> str:
            raise AgentFailure(reason, detail)

        return run_chain(
            task_def(),
            workspace_root=str(tmp_path / "ws"),
            agent_callable=agent,
            task_dir=str(tmp_path),
        )

    def test_detail_reaches_no_sink_with_its_secret(self, tmp_path) -> None:
        result = self._run(
            tmp_path, "provider_error", f'401 {{"message": "invalid {FAKE_KEY}"}}'
        )

        assert result.agent_failure is not None, "the agent channel, not the infra one"
        assert FAKE_KEY not in result.agent_failure["detail"], "persisted sink"
        assert FAKE_KEY not in result.summary(), "stdout sink"
        assert FAKE_KEY not in result.session_results[0].error, "the source field"

    def test_reason_is_scrubbed_though_no_infra_reason_ever_needs_it(
        self, tmp_path
    ) -> None:
        """``InfraError`` leaves its own ``reason`` alone, and correctly: those are
        harness-authored constants. This one is the adapter's string, so an adapter
        that passes a response body as its machine key publishes it verbatim."""
        result = self._run(tmp_path, f"auth_failed_{FAKE_KEY}", "boom")

        assert FAKE_KEY not in result.agent_failure["reason"], "persisted sink"
        assert FAKE_KEY not in result.summary(), "stdout sink — summary prints reason"

    def test_runaway_reason_cannot_flood_the_sinks(self, tmp_path) -> None:
        result = self._run(tmp_path, "x" * 100_000, "boom")
        assert len(result.agent_failure["reason"]) == MAX_DETAIL_CHARS

    def test_the_failure_still_reads_as_the_failure(self, tmp_path) -> None:
        """The negative control. ``reason`` is routed on downstream, so scrubbing
        that mangled an ordinary machine key would break the channel it protects."""
        result = self._run(
            tmp_path, "context_budget_exhausted", "the agent exhausted its context"
        )

        assert result.agent_failure["reason"] == "context_budget_exhausted"
        assert result.agent_failure["detail"] == "the agent exhausted its context"
