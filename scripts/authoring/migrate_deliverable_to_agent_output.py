#!/usr/bin/env python3
"""Migrate deliverable paths that live inside a gated repo dir to /workspace/agent_output/.

Root cause (found while root-causing the mcp_only sweep, 2026-07-22): a gated
arm's mode_gate denies the agent read/write on every ``[[repos]].path`` tree
(mode_gate.py), but 41 tasks direct the agent to write its graded deliverable
INSIDE one of those trees (``/workspace/<repo>/INCIDENT_REPORT.md`` and
siblings). In a gated arm the write EACCESs, the checkpoint reports the file
"not found", and every checkpoint scores 0.0 -- not because retrieval failed,
but because the deliverable had nowhere valid to land. ``/workspace`` itself
(and ``/workspace/agent_output/`` specifically) is deliberately left writable
by the gate in every arm (mode_gate.lockdown_commands' docstring), so that is
the one relocation target that is safe in baseline, mcp_only, cli, and hybrid
alike.

This is a MECHANICAL, literal-string migration -- it does not touch grading
logic, weights, or evidence tokens:
  1. In each affected checks/*.sh, rewrite the deliverable variable's RHS from
     ``"$WORKSPACE/<repo>/<file>"`` to ``"$WORKSPACE/agent_output/<file>"``.
  2. In instruction.md (and instruction_mcp.md, if present), replace the exact
     literal path string ``/workspace/<repo>/<file>`` with
     ``/workspace/agent_output/<file>`` everywhere it appears verbatim.

Only paths whose first segment matches a repo the task actually declares in
``[[repos]].path`` are touched -- a deliverable already routed to a non-repo
subdirectory (e.g. ``analysis/``) is left alone; it was never gated.

Usage:
  python3 scripts/authoring/migrate_deliverable_to_agent_output.py --dry-run
  python3 scripts/authoring/migrate_deliverable_to_agent_output.py --apply
  python3 scripts/authoring/migrate_deliverable_to_agent_output.py --apply benchmarks/technical_debt/dead-code-001
"""
from __future__ import annotations

import argparse
import glob
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BENCH = REPO_ROOT / "benchmarks"

# Matches a shell variable assignment whose RHS is a literal path under a repo
# subdirectory: VAR="$WORKSPACE/<segment>/<relative/file.ext>" or the
# equivalent curly-brace form VAR="${WORKSPACE}/...". Both spellings are live
# in the corpus (check_dead_code.sh uses ${WORKSPACE}; the median script uses
# $WORKSPACE) -- missing the curly form let dead-code-001..003's PRIMARY
# checkpoint (check_dead_code.sh) through the first migration pass unmigrated
# (caught by test_dead_code_verifiers.py going red after the fix, not by the
# corpus scan itself -- exactly the vacuous-sweep failure mode
# test_the_scan_is_actually_covering_tasks guards against). Deliberately
# anchored to line start and requires a file extension, so it cannot match an
# unrelated directory reference or a path built from other variables.
_DECL_RE = re.compile(
    r'^([A-Z_]+)="\$(?:\{WORKSPACE\}|WORKSPACE)/([a-zA-Z0-9_.-]+)/([A-Za-z0-9_./]+\.(?:md|json))"',
    re.M,
)


def find_gated_deliverables(bench: Path = BENCH) -> list[dict]:
    """One entry per (task, deliverable file) whose declared path's first
    segment is a repo the task actually clones -- the set mode_gate locks."""
    hits: list[dict] = []
    for toml_path in sorted(bench.glob("*/*/task.toml")):
        if "_archived" in toml_path.parts or "mined" in toml_path.parts:
            continue
        try:
            data = tomllib.loads(toml_path.read_text())
        except (OSError, tomllib.TOMLDecodeError):
            continue
        task_dir = toml_path.parent
        repo_paths = {r.get("path") for r in data.get("repos", []) if r.get("path")}
        repo_paths.discard("agent_output")  # the one guaranteed-writable drop; never touch
        if not repo_paths:
            continue
        by_file: dict[str, dict] = {}
        for chk in sorted(task_dir.glob("checks/*.sh")):
            text = chk.read_text(errors="replace")
            for m in _DECL_RE.finditer(text):
                var, seg, rel = m.group(1), m.group(2), m.group(3)
                if seg not in repo_paths:
                    continue  # not a repo dir -> not gated -> leave alone
                entry = by_file.setdefault(
                    rel,
                    {
                        "task_dir": task_dir,
                        "repo_seg": seg,
                        "rel_path": rel,
                        "old_path": f"/workspace/{seg}/{rel}",
                        "new_path": f"/workspace/agent_output/{rel}",
                        "check_files": set(),
                    },
                )
                if entry["repo_seg"] != seg:
                    raise ValueError(
                        f"{task_dir}: deliverable {rel!r} declared under two "
                        f"different repo segments ({entry['repo_seg']!r} and "
                        f"{seg!r}) -- ambiguous, needs a human"
                    )
                entry["check_files"].add(chk)
        hits.extend(by_file.values())
    return hits


def migrate_check_file(path: Path, old_seg: str, rel_path: str) -> tuple[str, int]:
    """Rewrite every VAR="$WORKSPACE/<old_seg>/<rel_path>" (bare or
    ``${WORKSPACE}`` curly-brace form -- both are live in the corpus) in
    *path* to the agent_output form. Returns (new_text, replacements_made)."""
    text = path.read_text(errors="replace")
    n = 0
    for ws_spelling in ("$WORKSPACE", "${WORKSPACE}"):
        old = f'"{ws_spelling}/{old_seg}/{rel_path}"'
        new = f'"{ws_spelling}/agent_output/{rel_path}"'
        n += text.count(old)
        text = text.replace(old, new)
    return text, n


def migrate_instruction_file(path: Path, old_path: str, new_path: str) -> tuple[str, int]:
    """Literal (non-regex) replacement of the exact deliverable path string."""
    text = path.read_text(errors="replace")
    n = text.count(old_path)
    return text.replace(old_path, new_path), n


def migrate(hits: list[dict], apply: bool) -> dict:
    plan: list[dict] = []
    flags: list[str] = []
    for h in hits:
        task_dir: Path = h["task_dir"]
        old_path, new_path = h["old_path"], h["new_path"]
        try:
            task_label = str(task_dir.relative_to(REPO_ROOT))
        except ValueError:
            task_label = str(task_dir)  # fixture/test dir outside REPO_ROOT
        item = {
            "task": task_label,
            "old_path": old_path,
            "new_path": new_path,
            "check_edits": {},
            "instruction_edits": {},
        }
        for chk in sorted(h["check_files"]):
            new_text, n = migrate_check_file(chk, h["repo_seg"], h["rel_path"])
            try:
                chk_label = str(chk.relative_to(REPO_ROOT))
            except ValueError:
                chk_label = str(chk)
            item["check_edits"][chk_label] = n
            if apply and n:
                chk.write_text(new_text)
        found_in_instruction = False
        for instr_name in ("instruction.md", "instruction_mcp.md"):
            instr = task_dir / instr_name
            if not instr.exists():
                continue
            new_text, n = migrate_instruction_file(instr, old_path, new_path)
            item["instruction_edits"][instr_name] = n
            if n:
                found_in_instruction = True
                if apply:
                    instr.write_text(new_text)
            # A second file mapping to the same (task, new_path) -- e.g. two
            # check scripts under the same task both declaring the same
            # deliverable, discovered across two tool runs -- can find the
            # instruction already migrated (0 occurrences of old_path, because
            # an earlier hit for this task already rewrote it there). That is
            # success, not a gap: only flag when NEITHER the old nor the new
            # spelling appears, which means the agent genuinely was never told.
            elif new_path in new_text:
                found_in_instruction = True
        if not found_in_instruction:
            flags.append(
                f"{item['task']}: no instruction*.md mentions the literal path "
                f"{old_path!r} (old or already-migrated form) -- check-script(s) "
                f"updated but the agent is not told the new location; needs a "
                f"human look"
            )
        plan.append(item)
    return {"plan": plan, "flags": flags}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "task_dir", nargs="?", help="Restrict to one task dir (default: whole corpus)"
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    hits = find_gated_deliverables()
    if args.task_dir:
        target = Path(args.task_dir).resolve()
        hits = [h for h in hits if h["task_dir"].resolve() == target]
        if not hits:
            print(f"no gated deliverables found under {args.task_dir}")
            return 0

    result = migrate(hits, apply=args.apply)
    n_files = sum(len(i["check_edits"]) for i in result["plan"])
    n_tasks = len({i["task"] for i in result["plan"]})
    verb = "Migrated" if args.apply else "Would migrate"
    print(f"{verb} {n_files} check script(s) across {n_tasks} task(s)")
    for item in result["plan"]:
        print(f"  {item['task']}: {item['old_path']} -> {item['new_path']}")
        for f, n in item["check_edits"].items():
            print(f"    check  {f} ({n} occurrence{'s' if n != 1 else ''})")
        for f, n in item["instruction_edits"].items():
            print(f"    instr  {f} ({n} occurrence{'s' if n != 1 else ''})")
    if result["flags"]:
        print(f"\n{len(result['flags'])} FLAG(S) -- not fully migrated:")
        for f in result["flags"]:
            print(f"  ! {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
