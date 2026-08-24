"""PreToolUse gate: allow this condition's MCP tools, deny everything else.

A denylist cannot express this experiment's requirement. ``--disallowed-tools``
enumerates what is forbidden, so it is fail-open by construction: a tool that
ships in a later CLI release is permitted by a list written before it existed,
and the corpus clones are readable on local disk, so one un-denied shell
primitive puts lexical search back into the structural and code-intel arms and
the independent variable is gone. This is the same reasoning
``scripts/orchestration/mode_gate.py`` records for the containerized arms; the
mechanism differs because there is no container here to hold kernel
permissions.

So the rule is inverted. Only ``mcp__retrieval__*`` names for tools this
condition actually declares are allowed, and an unrecognised name is denied
because it is unrecognised. A new built-in changes nothing.

Denials are appended to ``$EB_RUNDIR/denied_tools.jsonl`` rather than discarded.
A run where the model repeatedly reached for a denied tool is evidence about
tool preference, which is one of the four claims this experiment keeps apart,
and it would otherwise be invisible.

Reads the hook payload on stdin, writes a permission decision on stdout.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from conditions import CONDITIONS  # noqa: E402

MCP_PREFIX = "mcp__retrieval__"


def allowed_names(condition_name: str) -> set[str]:
    condition = CONDITIONS[condition_name]
    return {MCP_PREFIX + t["function"]["name"] for t in condition.tools}


def decide(tool_name: str, condition_name: str) -> tuple[str, str]:
    """(decision, reason) for one tool call."""
    if tool_name in allowed_names(condition_name):
        return "allow", f"{tool_name} is in condition {condition_name!r}"
    return (
        "deny",
        f"{tool_name} is not part of condition {condition_name!r}. Use only the "
        f"{MCP_PREFIX}* tools you were given.",
    )


def main() -> int:
    condition_name = os.environ.get("EB_CONDITION", "")
    if condition_name not in CONDITIONS:
        # Unconfigured means unknown, and unknown must not mean permitted.
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "EB_CONDITION is not set; no tool is allowed",
                    }
                }
            )
        )
        return 0

    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        payload = {}
    tool_name = payload.get("tool_name", "")
    decision, reason = decide(tool_name, condition_name)

    if decision == "deny":
        rundir = Path(os.environ.get("EB_RUNDIR", "."))
        rundir.mkdir(parents=True, exist_ok=True)
        with (rundir / "denied_tools.jsonl").open("a") as handle:
            handle.write(
                json.dumps(
                    {
                        "tool_name": tool_name,
                        "tool_input": payload.get("tool_input"),
                        "condition": condition_name,
                    }
                )
                + "\n"
            )

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
