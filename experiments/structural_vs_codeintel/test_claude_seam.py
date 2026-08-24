"""The Claude Code seam must not quietly relax the controls the experiment needs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "claude_seam"))

import run_claude_conditions  # noqa: E402
from claude_seam import allowlist_hook, mcp_server  # noqa: E402
from conditions import CONDITIONS  # noqa: E402


# --- tool surface ----------------------------------------------------------


def test_served_tools_are_exactly_the_condition_tools():
    for name, condition in CONDITIONS.items():
        served = [t["name"] for t in mcp_server.tool_schemas(name)]
        assert served == [t["function"]["name"] for t in condition.tools], name


def test_descriptions_are_carried_over_verbatim():
    """Rewording for MCP would reintroduce the coaching parity forbids."""
    for name, condition in CONDITIONS.items():
        served = {t["name"]: t for t in mcp_server.tool_schemas(name)}
        for tool in condition.tools:
            function = tool["function"]
            assert served[function["name"]]["description"] == function["description"]
            assert served[function["name"]]["inputSchema"] == function["parameters"]


def test_structural_arm_is_not_served_a_lexical_tool():
    assert "grep" not in {t["name"] for t in mcp_server.tool_schemas("structural")}
    assert "ast_search" not in {t["name"] for t in mcp_server.tool_schemas("lexical")}


# --- the fail-closed gate --------------------------------------------------


def test_hook_allows_only_this_condition_s_mcp_tools():
    decision, _ = allowlist_hook.decide("mcp__retrieval__grep", "lexical")
    assert decision == "allow"
    decision, _ = allowlist_hook.decide("mcp__retrieval__ast_search", "lexical")
    assert decision == "deny"


def test_hook_denies_built_ins_that_would_leak_lexical_search():
    """The clones are readable on disk; a shell in the structural arm ends the study."""
    for tool in ("Bash", "Grep", "Read", "Glob", "Task", "WebFetch"):
        decision, _ = allowlist_hook.decide(tool, "structural")
        assert decision == "deny", tool


def test_hook_denies_a_tool_name_that_does_not_exist_yet():
    """Fail closed: this is the property a --disallowed-tools denylist lacks."""
    decision, _ = allowlist_hook.decide("SomeFutureToolShipped2027", "combined")
    assert decision == "deny"


def test_hook_denies_when_unconfigured(tmp_path):
    """No condition means no permission, not blanket permission."""
    completed = subprocess.run(
        [sys.executable, str(HERE / "claude_seam" / "allowlist_hook.py")],
        input=json.dumps({"tool_name": "Bash"}),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "EB_RUNDIR": str(tmp_path)},
    )
    payload = json.loads(completed.stdout)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"


# --- reporting -------------------------------------------------------------


def test_input_tokens_count_cached_context_too():
    """Reporting only `input_tokens` shows ~6 tokens for a 100k-token run."""
    usage = run_claude_conditions._usage(
        {
            "usage": {
                "input_tokens": 6,
                "cache_creation_input_tokens": 32_000,
                "cache_read_input_tokens": 90_000,
                "output_tokens": 500,
            }
        }
    )
    assert usage["input_tokens"] == 122_006
    assert usage["input_tokens_uncached"] == 6
    assert usage["output_tokens"] == 500


def test_cli_invocation_pins_the_tool_surface():
    argv = run_claude_conditions.cli_argv(
        Path("/run"), "claude-sonnet-5", "prompt", Path("/run/mcp.json"), Path("/run/s.json")
    )
    assert "--strict-mcp-config" in argv
    assert "--exclude-dynamic-system-prompt-sections" in argv
    assert "--system-prompt" in argv
    assert "--max-turns" in argv
    # A denylist would be fail-open; the hook is the gate, so no denylist flag
    # should be present pretending to be one.
    assert "--disallowed-tools" not in argv
    assert "--dangerously-skip-permissions" not in argv


def test_seam_note_warns_against_cross_seam_token_comparison():
    assert "openrouter" in run_claude_conditions.GENERATION_SEAM_NOTE
    assert run_claude_conditions.SEAM == "claude_code_cli"


def test_a_missing_submission_is_invalid_not_a_zero(tmp_path, monkeypatch):
    """The OpenRouter seam forces a final submit; this one cannot. The gap is
    the harness's, so it must not be charged to the arm as a measured 0.0."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "instruction.md").write_text("find the files")
    (task_dir / "ground_truth.json").write_text(
        json.dumps({"required_files": ["src/a.py"]})
    )
    monkeypatch.setattr(run_claude_conditions, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        run_claude_conditions,
        "required_files_from_ground_truth",
        lambda path: ["src/a.py"],
    )

    class _Completed:
        stdout = json.dumps({"num_turns": 3, "usage": {}, "result": "I gave up"})
        stderr = ""

    # The CLI runs, answers, and never calls submit_files: submission.json is
    # never written into the run directory.
    monkeypatch.setattr(
        run_claude_conditions.subprocess, "run", lambda *a, **k: _Completed()
    )
    report = run_claude_conditions.run_one(
        {"task_id": "t1", "path": "task", "store_identifiers": ["github.com/x/y"]},
        "lexical",
        "claude-sonnet-5",
        account=1,
    )
    assert report["status"] == "invalid_no_submission"
    assert report["ir_scores"] is None
    assert report["retrieved_paths"] == []


def test_a_cli_failure_is_invalid_not_a_zero(tmp_path, monkeypatch):
    """A disabled account must not be reported as a bad retrieval arm."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "instruction.md").write_text("find the files")
    monkeypatch.setattr(run_claude_conditions, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        run_claude_conditions, "required_files_from_ground_truth", lambda p: ["src/a.py"]
    )

    class _Completed:
        stdout = json.dumps(
            {
                "is_error": True,
                "num_turns": 1,
                "usage": {},
                "result": "Your organization has disabled Claude subscription access",
            }
        )
        stderr = ""

    monkeypatch.setattr(
        run_claude_conditions.subprocess, "run", lambda *a, **k: _Completed()
    )
    report = run_claude_conditions.run_one(
        {"task_id": "t1", "path": "task", "store_identifiers": ["github.com/x/y"]},
        "lexical",
        "claude-sonnet-5",
        account=1,
    )
    assert report["status"] == "invalid_cli_error"
    assert report["ir_scores"] is None


def test_hitting_the_turn_cap_gets_its_own_status(tmp_path, monkeypatch):
    """The cap binds unevenly across conditions, so its exclusions must be
    countable separately rather than buried in a generic error bucket."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "instruction.md").write_text("find the files")
    monkeypatch.setattr(run_claude_conditions, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        run_claude_conditions, "required_files_from_ground_truth", lambda p: ["src/a.py"]
    )

    class _Completed:
        stdout = json.dumps(
            {"is_error": True, "subtype": "error_max_turns", "num_turns": 40, "usage": {}}
        )
        stderr = ""

    monkeypatch.setattr(
        run_claude_conditions.subprocess, "run", lambda *a, **k: _Completed()
    )
    report = run_claude_conditions.run_one(
        {"task_id": "t1", "path": "task", "store_identifiers": ["github.com/x/y"]},
        "codeintel",
        "claude-sonnet-5",
        account=1,
    )
    assert report["status"] == "invalid_turn_limit"
    assert report["ir_scores"] is None


def test_turn_cap_is_generous_enough_not_to_bind_on_the_slowest_arm():
    """Code intel needs a starting location before it can navigate, so it spends
    more turns; the first smoke run exceeded a cap of 12."""
    assert run_claude_conditions._MAX_TURNS >= 30
