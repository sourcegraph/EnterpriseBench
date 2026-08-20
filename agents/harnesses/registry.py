"""Typed execution plans for benchmark coding-agent harnesses."""

from __future__ import annotations

import hashlib
import re
import shlex
from dataclasses import dataclass

CODEX_NPM_PACKAGE = "@openai/codex@0.145.0"
OPENCODE_NPM_PACKAGE = "opencode-ai@1.18.4"

HARNESS_NAMES = ("claude", "codex", "opencode")
# The claude command used when --agent is not given. It lives here so the
# model flag is applied in exactly one place: while it was built inside
# run_task, an explicit --model was recorded in results.json and never passed
# to the CLI, and a sweep labelled r3-opus ran claude-sonnet-5 for all 40 runs.
# Turn budget. This is a runaway guard, not an experimental parameter, and it
# must stay far above what any arm legitimately needs. At 50 it was neither:
# the baseline and direct-MCP arms ran a median of 17-45 turns and pressed
# against the ceiling, so the cap truncated the over-exploration those arms
# exist to measure and discarded the worst offenders as `max_turns` errors
# instead of recording them. Turn bloat is a finding; censoring it is not.
# Wall-clock (`--timeout`, 1800s) remains the real bound on a runaway run.
MAX_AGENT_TURNS = 200
DEFAULT_CLAUDE_COMMAND = (
    "claude",
    "--dangerously-skip-permissions",
    "--max-turns",
    str(MAX_AGENT_TURNS),
    "--verbose",
    "--output-format",
    "stream-json",
    "-p",
)
_BASELINE_MODE = "baseline"
_MCP_MODES = frozenset(
    {"mcp_only", "mcp_default", "mcp_treatment", "mcp_code_finder", "mcp_assisted"}
)
_SUPPORTED_MODES = {
    "codex": frozenset({_BASELINE_MODE, *_MCP_MODES, "cli"}),
    "opencode": frozenset({_BASELINE_MODE, *_MCP_MODES, "cli"}),
}
_SAFE_LABEL_CHARS = re.compile(r"[^a-z0-9]+")

# Environment that forces a harness to put every MCP tool's full schema and
# description in the model's context, rather than offering names and loading
# descriptions on demand.
#
# This is a comparability requirement, not a tuning knob. Claude Code defers MCP
# tool schemas by default: the model is shown tool *names* and selects which to
# load. Measured on the retrieval slice, every run made exactly one such
# selection call and none of them selected code_finder, so no run in either the
# mcp_default or mcp_treatment arm ever had a Sourcegraph tool description in
# context. An arm that claims to measure "the server as shipped" cannot
# silently withhold the server's own text.
#
# Harnesses without a deferral mechanism need no variable; the contract is
# satisfied by default and asserted after the run either way.
_FULL_TOOL_SCHEMA_ENV: dict[str, dict[str, str]] = {
    "claude": {"ENABLE_TOOL_SEARCH": "false"},
    "codex": {},
    "opencode": {},
}
# Trace markers proving a harness deferred tool schemas anyway.
_DEFERRED_TOOL_MARKERS: dict[str, tuple[str, ...]] = {
    "claude": ("ToolSearch",),
    "codex": (),
    "opencode": (),
}


def full_tool_schema_env(harness: str) -> dict[str, str]:
    """Environment that keeps every MCP tool description in the model's context."""
    normalized = harness.strip().lower()
    if normalized not in HARNESS_NAMES:
        raise HarnessConfigurationError(f"unknown harness {harness!r}")
    return dict(_FULL_TOOL_SCHEMA_ENV[normalized])


def deferred_tool_markers(harness: str) -> tuple[str, ...]:
    """Tool names whose appearance in a trace proves schemas were withheld."""
    normalized = harness.strip().lower()
    if normalized not in HARNESS_NAMES:
        raise HarnessConfigurationError(f"unknown harness {harness!r}")
    return _DEFERRED_TOOL_MARKERS[normalized]


class HarnessConfigurationError(ValueError):
    """The requested harness configuration cannot produce a comparable run."""


@dataclass(frozen=True)
class HarnessPlan:
    """Immutable command, installation, and credential contract for one run."""

    name: str
    model: str | None
    command: tuple[str, ...]
    binary: str
    npm_package: str | None = None
    required_env: tuple[str, ...] = ()

    @property
    def rendered_command(self) -> str:
        """Shell-escaped display form for logs and result metadata."""
        return shlex.join(self.command)


def _require_model(harness: str, model: str | None) -> str:
    value = (model or "").strip()
    if not value:
        raise HarnessConfigurationError(f"harness={harness} requires --model")
    return value


def _require_supported_mode(harness: str, mode: str) -> None:
    supported = _SUPPORTED_MODES.get(harness, frozenset({_BASELINE_MODE}))
    if mode not in supported:
        raise HarnessConfigurationError(
            f"harness={harness} does not support mode={mode!r}; "
            f"supported modes: {', '.join(sorted(supported))}"
        )


#: Reasoning-effort levels Codex accepts. A comparison that does not pin this
#: is not reproducible: the same model at a different effort is a different
#: system under test, and the default has changed between CLI releases.
CODEX_REASONING_EFFORTS = ("minimal", "low", "medium", "high")


def build_harness_plan(
    harness: str,
    *,
    model: str | None,
    mode: str,
    agent_command: str = "",
    reasoning_effort: str | None = None,
) -> HarnessPlan:
    """Resolve user-facing harness options into an executable CLI plan."""
    normalized = harness.strip().lower()
    if normalized not in HARNESS_NAMES:
        raise HarnessConfigurationError(
            f"unknown harness {harness!r}; choose from {', '.join(HARNESS_NAMES)}"
        )

    if reasoning_effort is not None and normalized != "codex":
        raise HarnessConfigurationError(
            "--reasoning-effort is a codex option and cannot be combined with "
            f"--harness {normalized}"
        )

    if normalized == "claude":
        try:
            command = tuple(shlex.split(agent_command)) if agent_command else ()
        except ValueError as exc:
            raise HarnessConfigurationError(f"invalid --agent command: {exc}") from exc
        # An explicit --model must reach the CLI, not merely be recorded.
        # Until this was added, `model` was stored in results.json's config
        # block while the command ran whatever the CLI defaulted to, so a
        # sweep labelled with one model could be produced by another -- a
        # provenance claim the artifacts could not support. The flag goes
        # directly after the binary because the trailing `-p` consumes the
        # prompt.
        if not command:
            command = DEFAULT_CLAUDE_COMMAND
        if model:
            command = (command[0], "--model", model, *command[1:])
        return HarnessPlan(
            name=normalized,
            model=model,
            command=command,
            binary="claude",
        )

    if agent_command:
        raise HarnessConfigurationError(
            f"--agent cannot be combined with --harness {normalized}; "
            "the selected harness generates its command"
        )

    _require_supported_mode(normalized, mode)
    selected_model = _require_model(normalized, model)

    if normalized == "codex":
        config_args = () if mode in _MCP_MODES else ("--ignore-user-config",)
        if reasoning_effort is not None:
            if reasoning_effort not in CODEX_REASONING_EFFORTS:
                raise HarnessConfigurationError(
                    f"unknown codex reasoning effort {reasoning_effort!r}; "
                    f"choose from {', '.join(CODEX_REASONING_EFFORTS)}"
                )
            config_args = (
                *config_args,
                "-c",
                f'model_reasoning_effort="{reasoning_effort}"',
            )
        return HarnessPlan(
            name=normalized,
            model=selected_model,
            command=(
                "codex",
                "exec",
                "--model",
                selected_model,
                "--json",
                "--ephemeral",
                *config_args,
                "--skip-git-repo-check",
                "--dangerously-bypass-approvals-and-sandbox",
                "-",
            ),
            binary="codex",
            npm_package=CODEX_NPM_PACKAGE,
        )

    if not selected_model.startswith("openrouter/") or selected_model.count("/") < 2:
        raise HarnessConfigurationError(
            "harness=opencode currently requires an OpenRouter model ID in "
            "provider/model form, for example "
            "openrouter/deepseek/deepseek-v4-pro"
        )
    return HarnessPlan(
        name=normalized,
        model=selected_model,
        command=(
            "opencode",
            "run",
            "--model",
            selected_model,
            "--format",
            "json",
            "--pure",
            "--auto",
        ),
        binary="opencode",
        npm_package=OPENCODE_NPM_PACKAGE,
        required_env=("OPENROUTER_API_KEY",),
    )


def harness_variant_label(harness: str, model: str | None) -> str | None:
    """Return a stable path-safe label for a generated non-Claude harness."""
    normalized = harness.strip().lower()
    if normalized == "claude":
        return None
    raw_identity = f"{normalized}-{model or 'default'}"
    display_identity = raw_identity.lower()
    label = _SAFE_LABEL_CHARS.sub("-", display_identity).strip("-")
    if not label:
        raise HarnessConfigurationError("harness/model did not produce a run label")
    if label == raw_identity and len(label) <= 96:
        return label
    digest = hashlib.sha256(raw_identity.encode()).hexdigest()[:10]
    prefix = label[: 96 - len(digest) - 1].rstrip("-")
    return f"{prefix}-{digest}"
