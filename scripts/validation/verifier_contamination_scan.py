#!/usr/bin/env python3
"""
Scan multi-repo task verifiers for cross-task copy-paste contamination.

A verifier copy-pasted from another task and not re-targeted scores the wrong
thing — e.g. a Zulip schema verifier left on a Django task, or a Consul drift
verifier left on an ArgoCD task. y50 found these only via the gold_zero lens,
which misses contaminated checkpoints that score >0 by luck. This scans the
verifier source directly.

Three detectors:
  1. foreign-path   — verifier reads ``$WORKSPACE/<dir>/...`` where <dir> is not
                      a declared repo of the task and not a generic output dir.
                      HIGH PRECISION (a verifier should only read its own repos).
  2. shared-body    — byte-identical (comment-stripped) verifier body shared
                      across tasks with DIFFERENT declared repo sets. Surfaces
                      both deliberate generic verifiers and copy-paste; the
                      caller must classify (does the *active* code bind to one
                      task, or read ground_truth generically?).
  3. foreign-token  — a distinctive repo name appears as a whole word in a
                      verifier that does not own it and whose own prompt/GT does
                      not mention it. INFORMATIONAL (benign comment/domain hits).

Exit non-zero under --strict if any foreign-path hit is found (the high-precision
signal). Usage:
    python3 scripts/validation/verifier_contamination_scan.py benchmarks/ [--json] [--strict]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# Workspace-relative path references inside verifier scripts.
_WS_PATH_RES = (
    re.compile(r"\$\{WORKSPACE(?::-[^}]*)?\}(/[\w@%+=:,.\-/]+)"),
    re.compile(r"\$WORKSPACE(/[\w@%+=:,.\-/]+)"),
)
# First-path-segments that are never repos.
_GENERIC_DIRS = {
    "agent_output", "output", "outputs", "artifacts", "analysis", "results",
    ".task", ".verifiers", ".eb_verify", ".git", "workspace", "tmp",
}
# Short/ambiguous repo names excluded from the token detector to cut false
# positives (they collide with English words or appear in many tasks).
_TOKEN_STOP = {
    "go", "api", "web", "core", "cli", "app", "test", "tests", "docs", "src",
    "main", "spring", "data", "auth", "redis", "kafka", "docker", "node",
    "next", "relay", "sentry",
}


def _declared_repos(toml_text: str) -> list[str]:
    """First-segment dir names from the task's ``[[repos]]`` path entries."""
    repos: list[str] = []
    in_repos = False
    for line in toml_text.splitlines():
        s = line.strip()
        if s.startswith("[[repos]]"):
            in_repos = True
            continue
        if s.startswith("[") and not s.startswith("[[repos]]"):
            in_repos = False
        if in_repos:
            m = re.match(r'path\s*=\s*"([^"]+)"', s)
            if m:
                repos.append(m.group(1).strip("/").split("/")[0])
    return repos


def _is_multirepo(toml_text: str, repos: list[str]) -> bool:
    strat = re.search(r'difficulty_stratum\s*=\s*"([^"]+)"', toml_text)
    if strat and strat.group(1) in ("dual_repo", "tri_repo", "multi_repo", "monorepo"):
        return True
    return len(set(repos)) > 1


def _task_id(toml_text: str, fallback: str) -> str:
    m = re.search(r'^\s*id\s*=\s*"([^"]+)"', toml_text, re.M)
    return m.group(1) if m else fallback


def _norm_body(text: str) -> str:
    """Comment- and whitespace-stripped body, for byte-identical comparison."""
    return "\n".join(
        line for line in (ln.strip() for ln in text.splitlines())
        if line and not line.startswith("#")
    )


def scan(root: Path) -> dict:
    tasks = []  # (tid, dir, set(repos), toml_text)
    all_repos: set[str] = set()
    for toml in root.rglob("task.toml"):
        if "_archived" in str(toml):
            continue
        t = toml.read_text(errors="replace")
        repos = _declared_repos(t)
        all_repos.update(repos)
        if not _is_multirepo(t, repos):
            continue
        tasks.append((_task_id(t, toml.parent.name), toml.parent, set(repos), t))

    distinctive = {
        r for r in all_repos
        if len(r) > 4 and r.lower() not in _TOKEN_STOP
        and re.match(r"^[a-z0-9_-]+$", r.lower())
    }

    foreign_path = []      # (tid, check, foreign_dir, sorted_repos)
    foreign_token = []     # (tid, check, foreign_repo, sorted_repos)
    bodies = defaultdict(list)  # hash -> [(tid, check, frozenset(repos))]

    for tid, d, repos, _ in tasks:
        cdir = d / "checks"
        if not cdir.is_dir():
            continue
        own = {r.lower() for r in repos}
        own_ctx = ""
        for extra in ("ground_truth.json", "expected_solution.json", "task.toml", "instruction.md"):
            p = d / extra
            if p.exists():
                own_ctx += p.read_text(errors="replace").lower()

        for c in sorted(cdir.glob("*.sh")):
            txt = c.read_text(errors="replace")
            bodies[hashlib.sha1(_norm_body(txt).encode()).hexdigest()].append(
                (tid, c.name, frozenset(repos))
            )
            # detector 1: foreign path
            segs = set()
            for pat in _WS_PATH_RES:
                for m in pat.finditer(txt):
                    parts = [p for p in m.group(1).lstrip("/").split("/") if p]
                    if len(parts) >= 2:
                        segs.add(parts[0])
            for seg in segs:
                if seg not in _GENERIC_DIRS and seg not in repos:
                    foreign_path.append((tid, c.name, seg, sorted(repos)))
            # detector 3: foreign token
            body_lc = txt.lower()
            for repo in distinctive:
                rl = repo.lower()
                if rl in own or rl in own_ctx:
                    continue
                if re.search(r"(?<![a-z0-9_-])" + re.escape(rl) + r"(?![a-z0-9_-])", body_lc):
                    foreign_token.append((tid, c.name, repo, sorted(repos)))

    shared = []
    for h, items in bodies.items():
        if len({i[0] for i in items}) > 1 and len({i[2] for i in items}) > 1:
            shared.append({
                "hash": h[:8],
                "members": [{"task": t, "check": c, "repos": sorted(r)} for t, c, r in sorted(items)],
            })

    return {
        "tasks_scanned": len(tasks),
        "foreign_path": [
            {"task": t, "check": c, "foreign_dir": s, "declared_repos": r}
            for t, c, s, r in sorted(foreign_path)
        ],
        "foreign_token": [
            {"task": t, "check": c, "foreign_repo": s, "declared_repos": r}
            for t, c, s, r in sorted(foreign_token)
        ],
        "shared_body_across_repo_sets": shared,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="benchmarks", type=Path)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if any foreign-path hit is found")
    args = ap.parse_args(argv)

    result = scan(args.root)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"multi-repo tasks scanned: {result['tasks_scanned']}")
        print(f"\nFOREIGN-PATH hits (high precision): {len(result['foreign_path'])}")
        for h in result["foreign_path"]:
            print(f"  {h['task']:48s} {h['check']:26s} refs '{h['foreign_dir']}/' "
                  f"(declared: {h['declared_repos']})")
        print(f"\nFOREIGN-TOKEN hits (informational): {len(result['foreign_token'])}")
        for h in result["foreign_token"]:
            print(f"  {h['task']:48s} {h['check']:26s} token '{h['foreign_repo']}'")
        print(f"\nSHARED bodies across different repo sets: {len(result['shared_body_across_repo_sets'])} groups")

    if args.strict and result["foreign_path"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
