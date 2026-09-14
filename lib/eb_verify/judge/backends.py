"""LLM API backends for the EB LLM Judge.

Ported from CodeScaleBench csb_metrics/judge/backends.py.
Supports Anthropic (Claude) and Claude Code CLI backends.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time


class JudgeBackendError(Exception):
    """Non-retryable backend failure."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _require_dict(parsed: object, text: str) -> dict:
    """Ensure parsed JSON is a dict, raising with the source text on mismatch."""
    if not isinstance(parsed, dict):
        raise JudgeBackendError(
            f"Expected JSON object, got {type(parsed).__name__}: {text[:200]}...",
        )
    return parsed


def _try_parse_json(candidate: str) -> dict | None:
    """Parse candidate as JSON, returning None (not raising) on malformed JSON."""
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return _require_dict(parsed, candidate)


def _parse_json(raw: str) -> dict:
    """Extract and parse JSON from the model's text response."""
    text = raw.strip()
    if (parsed := _try_parse_json(text)) is not None:
        return parsed

    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m and (parsed := _try_parse_json(m.group(1).strip())) is not None:
        return parsed

    raise JudgeBackendError(
        f"Failed to parse JSON from response: {text[:200]}...",
    )


class AnthropicBackend:
    """Anthropic API backend with retry logic."""

    _MAX_RETRIES = 3

    def __init__(self, model: str, temperature: float = 0.0, max_tokens: int = 4096):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def call(self, system_prompt: str, user_prompt: str) -> dict:
        last_err: Exception | None = None
        for attempt in range(self._MAX_RETRIES):
            try:
                raw = self._raw_call(system_prompt, user_prompt)
                return _parse_json(raw)
            except JudgeBackendError as exc:
                if exc.status_code and (
                    exc.status_code == 429 or exc.status_code >= 500
                ):
                    last_err = exc
                    time.sleep(2**attempt)
                    continue
                raise
        raise JudgeBackendError(
            f"Exhausted {self._MAX_RETRIES} retries: {last_err}",
            status_code=getattr(last_err, "status_code", None),
        )

    def _raw_call(self, system_prompt: str, user_prompt: str) -> str:
        try:
            return self._call_sdk(system_prompt, user_prompt)
        except ImportError:
            return self._call_urllib(system_prompt, user_prompt)

    def _call_sdk(self, system_prompt: str, user_prompt: str) -> str:
        import anthropic

        client = anthropic.Anthropic()
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except anthropic.RateLimitError as exc:
            raise JudgeBackendError(str(exc), status_code=429) from exc
        except anthropic.APIStatusError as exc:
            raise JudgeBackendError(str(exc), status_code=exc.status_code) from exc
        return response.content[0].text

    def _call_urllib(self, system_prompt: str, user_prompt: str) -> str:
        import urllib.error
        import urllib.request

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise JudgeBackendError(
                "ANTHROPIC_API_KEY not set and anthropic SDK not installed"
            )
        payload = json.dumps(
            {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            }
        ).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                body = json.loads(resp.read())
                return body["content"][0]["text"]
        except urllib.error.HTTPError as exc:
            raise JudgeBackendError(
                f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:500]}",
                status_code=exc.code,
            ) from exc


class ClaudeCodeBackend:
    """Backend using the claude CLI in print mode (no API key needed)."""

    _MAX_RETRIES = 3
    _ALIAS_MAP = {
        "claude-haiku-4-5-20251001": "haiku",
        "claude-sonnet-4-6": "sonnet",
        "claude-opus-4-6": "opus",
    }

    def __init__(self, model: str = "haiku", **_kwargs: object):
        self.model = self._ALIAS_MAP.get(model, model)
        self._claude_bin = shutil.which("claude")
        if self._claude_bin is None:
            raise JudgeBackendError("claude CLI not found on PATH")

    def call(self, system_prompt: str, user_prompt: str) -> dict:
        last_err: Exception | None = None
        for attempt in range(self._MAX_RETRIES):
            try:
                return self._raw_call(system_prompt, user_prompt)
            except JudgeBackendError as exc:
                if exc.status_code and (
                    exc.status_code == 429 or exc.status_code >= 500
                ):
                    last_err = exc
                    time.sleep(2**attempt)
                    continue
                raise
        raise JudgeBackendError(
            f"Exhausted {self._MAX_RETRIES} retries: {last_err}",
            status_code=getattr(last_err, "status_code", None),
        )

    def _raw_call(self, system_prompt: str, user_prompt: str) -> dict:
        cmd = [
            self._claude_bin,
            "-p",
            "--output-format",
            "json",
            "--model",
            self.model,
            "--no-session-persistence",
            "--append-system-prompt",
            system_prompt,
            user_prompt,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            raise JudgeBackendError("claude CLI timed out after 120s")

        if result.returncode != 0:
            raise JudgeBackendError(
                f"claude CLI failed (rc={result.returncode}): {result.stderr[:500]}"
            )
        try:
            envelope = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise JudgeBackendError(f"Failed to parse claude CLI output: {exc}")

        if envelope.get("is_error"):
            error_msg = envelope.get("result", "unknown error")
            if "rate" in error_msg.lower() or "429" in error_msg:
                raise JudgeBackendError(error_msg, status_code=429)
            raise JudgeBackendError(f"claude CLI error: {error_msg}")

        raw_text = envelope.get("result", "")
        if not raw_text:
            raise JudgeBackendError("claude CLI returned empty result")
        return _parse_json(raw_text)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_CLAUDE_CODE_ALIASES = {"claude-code", "cc"}


def create_backend(
    model: str, temperature: float = 0.0, max_tokens: int = 4096
) -> AnthropicBackend | ClaudeCodeBackend:
    """Create the appropriate backend based on model identifier."""
    name = model.lower()
    if name in _CLAUDE_CODE_ALIASES or name.startswith("cc:"):
        sub_model = name.split(":", 1)[1] if ":" in name else "haiku"
        return ClaudeCodeBackend(model=sub_model)
    if name.startswith("claude"):
        return AnthropicBackend(
            model=model, temperature=temperature, max_tokens=max_tokens
        )
    raise ValueError(
        f"Cannot determine provider for model '{model}'. "
        f"Expected 'claude-code'/'cc[:model]' or 'claude-*'."
    )
