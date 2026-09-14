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


def _is_rate_limit_diagnostic(message: str) -> bool:
    lowered = message.lower()
    return (
        "429" in lowered
        or "usage limit" in lowered
        or "rate limit" in lowered
        or "rate-limit" in lowered
        or "rate limited" in lowered
    )


def _parse_json(raw: str) -> dict:
    """Extract and parse JSON from the model's text response."""
    text = raw.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

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

    @property
    def provenance(self) -> dict[str, object]:
        return {
            "backend": "anthropic_api",
            "provider": "anthropic",
            "model": self.model,
        }

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

    def __init__(
        self,
        model: str = "haiku",
        *,
        account: int | None = None,
        **_kwargs: object,
    ):
        if isinstance(account, bool) or (
            account is not None and (not isinstance(account, int) or account <= 0)
        ):
            raise ValueError("Claude judge account must be a positive integer")
        self.model = self._ALIAS_MAP.get(model, model)
        self.account = account
        self.executable = f"claude-{account}" if account is not None else "claude"
        self._claude_bin = shutil.which(self.executable)
        if self._claude_bin is None:
            raise JudgeBackendError(f"{self.executable} CLI not found on PATH")
        self._cli_version = self._read_cli_version()

    def _read_cli_version(self) -> str | None:
        try:
            result = subprocess.run(
                [self._claude_bin, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        version = result.stdout.strip()
        return version if result.returncode == 0 and version else None

    @property
    def provenance(self) -> dict[str, object]:
        provenance: dict[str, object] = {
            "backend": "claude_code_cli",
            "provider": "anthropic",
            "model": self.model,
            "account": self.account,
            "executable": self.executable,
        }
        if self._cli_version is not None:
            provenance["cli_version"] = self._cli_version
        return provenance

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
            diagnostic = (result.stderr.strip() or result.stdout.strip())[:500]
            status_code = 429 if _is_rate_limit_diagnostic(diagnostic) else None
            raise JudgeBackendError(
                f"claude CLI failed (rc={result.returncode}): {diagnostic}",
                status_code=status_code,
            )
        try:
            envelope = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise JudgeBackendError(f"Failed to parse claude CLI output: {exc}")

        if envelope.get("is_error"):
            error_msg = str(envelope.get("result", "unknown error"))
            if _is_rate_limit_diagnostic(error_msg):
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
    model: str,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    account: int | None = None,
) -> AnthropicBackend | ClaudeCodeBackend:
    """Create the appropriate backend based on model identifier."""
    name = model.lower()
    if name in _CLAUDE_CODE_ALIASES or name.startswith("cc:"):
        sub_model = name.split(":", 1)[1] if ":" in name else "haiku"
        return ClaudeCodeBackend(model=sub_model, account=account)
    if name.startswith("claude"):
        if account is not None:
            raise ValueError(
                "Claude judge account selection is only supported by cc:* models"
            )
        return AnthropicBackend(
            model=model, temperature=temperature, max_tokens=max_tokens
        )
    raise ValueError(
        f"Cannot determine provider for model '{model}'. "
        f"Expected 'claude-code'/'cc[:model]' or 'claude-*'."
    )
