"""Generate the corpus tasks that close the retrieval slice's coverage gaps.

Every answer key is DERIVED by running the spec's oracle against the pinned
local clone. Nothing is hand-listed: ``Spec.expected`` is a tripwire, not the
source of truth, so a re-clone at a different revision or an ast-grep grammar
change fails the run instead of quietly rewriting what an experiment was
scored against.

    python3 tools/make_navigation_tasks.py --derive   # print measured keys only
    python3 tools/make_navigation_tasks.py --write    # (re)generate task dirs
    python3 tools/make_navigation_tasks.py --check    # fail on any drift

Requires the local clones (experiments/toast_retrieval/clone_repos.py) and
ast-grep. Both are hard requirements: a missing oracle would produce an empty
answer key, which reads as a legitimate task rather than a broken one.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from navigation_specs import SPECS, Spec  # noqa: E402

REPO_ROOT = HERE.parents[2]
CLONES = REPO_ROOT / "experiments" / "toast_retrieval" / "repos"
BENCHMARKS = REPO_ROOT / "benchmarks" / "technical_debt"

#: Paths excluded from every answer key. A retrieval task is scored on the code
#: that implements the behaviour; a test, a vendored copy, a generated file, or
#: a changelog entry that happens to name the symbol is not an edit site, and
#: including them would make the key depend on how thoroughly a repo is tested.
NOISE = re.compile(
    r"(^|/)(tests?|testing|vendor|third_party|node_modules|examples?|docs?|fixtures?)(/|$)"
    r"|_test\.|test_|\.pb\.go$|(^|/)CHANGELOG|generated",
    re.IGNORECASE,
)


def _clone(spec: Spec) -> Path:
    directory = CLONES / spec.store
    if not (directory / ".git").is_dir():
        raise SystemExit(
            f"{spec.task_id}: no clone at {directory}. Run "
            "experiments/toast_retrieval/clone_repos.py first."
        )
    return directory


def _ast_grep() -> str:
    candidate = REPO_ROOT / "experiments" / "toast_retrieval" / ".venv" / "bin" / "ast-grep"
    if candidate.is_file():
        return str(candidate)
    raise SystemExit("ast-grep not found; the shape oracles cannot run.")


def derive(spec: Spec) -> list[str]:
    """The answer key, measured from the pinned clone by the spec's oracle."""
    directory = _clone(spec)
    if spec.kind == "identifier":
        result = subprocess.run(
            ["git", "-C", str(directory), "grep", "-lw", spec.query, "--", spec.scope],
            capture_output=True,
            text=True,
        )
        # git grep exits 1 for "no match", which for an answer key is a broken
        # spec rather than an empty truth, so it is not folded into success.
        if result.returncode not in (0, 1):
            raise SystemExit(f"{spec.task_id}: git grep failed: {result.stderr.strip()}")
        paths = result.stdout.split()
    else:
        scope = directory / spec.scope
        result = subprocess.run(
            [_ast_grep(), "run", "--pattern", spec.query, "--lang", spec.language,
             "--json=compact", str(scope)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise SystemExit(f"{spec.task_id}: ast-grep failed: {result.stderr.strip()}")
        matches = json.loads(result.stdout or "[]")
        paths = [str(Path(m["file"]).resolve().relative_to(directory)) for m in matches]
    return sorted({p for p in paths if not NOISE.search(p)})


def _oracle_command(spec: Spec) -> str:
    if spec.kind == "identifier":
        return f"git grep -lw {spec.query!r} -- {spec.scope}"
    return f"ast-grep run --pattern {spec.query!r} --lang {spec.language} {spec.scope}"


def ground_truth(spec: Spec, files: list[str]) -> dict:
    return {
        "task_id": spec.task_id,
        "repo": spec.url.removeprefix("https://github.com/"),
        "repo_rev": spec.rev,
        "required_files": [
            {
                "path": path,
                "repo": spec.path,
                "confidence": 1.0,
                "source": "deterministic",
                "rationale": (
                    f"Contains a word-boundary occurrence of {spec.query!r}."
                    if spec.kind == "identifier"
                    else "Contains at least one match for the specified code shape."
                ),
            }
            for path in files
        ],
        "sufficient_files": [],
        "provenance": {
            "oracle": "text (word-boundary match)" if spec.kind == "identifier"
                      else "parser (ast-grep pattern match)",
            "command": _oracle_command(spec),
            "clone": spec.store,
            "scope": spec.scope,
            "excluded": NOISE.pattern,
            "bias_note": (
                "The oracle for this axis is the same class of tool as one "
                "experimental condition's primitive. That condition can reproduce "
                "this key more directly than the others, which is a property of the "
                "axis and must be stated with any per-category claim drawn from it."
            ),
        },
    }


def task_toml(spec: Spec, files: list[str]) -> str:
    lines = [
        'difficulty_stratum = "large_single"',
        'mcp_suite = "eb_v1"',
        f'repo_set_id = "{spec.path}-nav"',
        "org_scale = true",
        'verification_modes = ["deterministic"]',
        "",
        "[task]",
        f'id = "{spec.task_id}"',
        'suite = "technical_debt"',
        'difficulty = "hard"',
        "estimated_duration_minutes = 15",
        'session_type = "single"',
        'task_type = "refactor_orchestration"',
        f'description = "{spec.title}"',
        'prompt = "See instruction.md"',
        "",
        "[[repos]]",
        f'url = "{spec.url}"',
        f'rev = "{spec.rev}"',
        f'path = "{spec.path}"',
        'role = "primary"',
        "",
        "[metadata]",
        f"languages = {json.dumps(list(spec.languages))}",
        f"frameworks = {json.dumps(list(spec.frameworks))}",
        'multi_repo_pattern = "investigate"',
        f"retrieval_categories = {json.dumps(list(spec.categories))}",
        "",
        "[[checkpoints]]",
        'name = "source_files"',
        "weight = 1.00",
        'verifier = "checks/check_source_files.sh"',
        'description = "Agent lists exactly the source files that qualify"',
        "timeout_seconds = 60",
        "",
        "[artifacts]",
        'required = ["answer"]',
        "optional = []",
        "",
        "[tool_access]",
        'expected_mcp_benefit = "unknown"',
        'mcp_benefit_rationale = "Authored to measure primitive choice, not to '
        'presuppose it; no arm is expected to win a priori."',
        "",
        "[ground_truth]",
        'tiers = ["deterministic"]',
        "",
    ]
    for path in files:
        lines += [
            "[[ground_truth.required_files]]",
            f'path = "{path}"',
            f'repo = "{spec.path}"',
            "confidence = 1.0",
            'source = "deterministic"',
            "",
        ]
    return "\n".join(lines)


def instruction(spec: Spec) -> str:
    from navigation_specs import _ANSWER

    return f"# {spec.title}\n\n{spec.scenario}\n\n{_ANSWER}\n"


CHECK = """#!/usr/bin/env bash
# check_source_files.sh -- F1 of the agent's file list against the derived key.
#
# Scored as F1, not recall: these tasks ask for an exhaustive set, so listing
# every file in the repository must not score as a perfect answer.
set -euo pipefail

export ANSWER_FILE="${WORKSPACE}/agent_output/answer.json"
export GT_FILE="${TASK_DIR}/ground_truth.json"

if [[ ! -f "$ANSWER_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No answer.json found"}'
    exit 1
fi
if [[ ! -f "$GT_FILE" ]]; then
    echo '{"score": 0.0, "passed": false, "detail": "No ground_truth.json found"}'
    exit 1
fi

python3 -c "
import json, os

gt = json.load(open(os.environ['GT_FILE']))
answer = json.load(open(os.environ['ANSWER_FILE']))

required = [f['path'] for f in gt.get('required_files', [])]
raw = answer.get('source_files', answer.get('files', []))
listed = []
for f in (raw if isinstance(raw, list) else []):
    if isinstance(f, dict):
        listed.append(f.get('path', f.get('file', '')))
    elif isinstance(f, str):
        listed.append(f)
listed = [p.strip().lstrip('/') for p in listed if p]

def matches(want, got):
    # Suffix match in either direction: an agent may report a workspace-prefixed
    # path, and a repo-relative key must still count it.
    return got.endswith(want) or want.endswith(got)

if not required:
    print(json.dumps({'score': 0.0, 'passed': False, 'detail': 'No required files in GT'}))
    raise SystemExit
if not listed:
    print(json.dumps({'score': 0.0, 'passed': False, 'detail': 'Agent listed no files'}))
    raise SystemExit

hit = sum(1 for w in required if any(matches(w, g) for g in listed))
extra = sum(1 for g in listed if not any(matches(w, g) for w in required))
recall = hit / len(required)
precision = hit / (hit + extra) if (hit + extra) else 0.0
f1 = 0.0 if not (recall + precision) else 2 * recall * precision / (recall + precision)
print(json.dumps({
    'score': round(f1, 4),
    'passed': f1 >= 0.5,
    'detail': f'{hit}/{len(required)} required, {extra} spurious (recall {recall:.2f}, precision {precision:.2f})',
}))
"
"""

DOCKERFILE = """# EnterpriseBench task: {task_id}
# Standard Dockerfile: local clone from sg-evals mirrors
FROM python:3.11-bookworm

RUN apt-get update && apt-get install -y git curl ca-certificates jq xz-utils && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://nodejs.org/dist/v20.18.3/node-v20.18.3-linux-x64.tar.xz | \\
    tar -xJ --strip-components=1 -C /usr/local

RUN npm install -g @anthropic-ai/claude-code@latest

RUN useradd -m -s /bin/bash agent && \\
    mkdir -p /workspace && chown agent:agent /workspace

USER agent

WORKDIR /workspace

# primary: {url} @ {rev}
RUN git clone --depth 1 https://github.com/sg-evals/{store}.git /workspace/{path}

WORKDIR /workspace
"""


MIRRORS = REPO_ROOT / "configs" / "sg_mirrors"


def mirror_config(spec: Spec) -> None:
    """Declare the pinned mirror the precise-navigation arm needs.

    Declaring it does not make it indexed. ``probe_index.py`` measures that
    separately, because a task whose mirror is missing must route to invalid
    rather than score the code-intel arm a zero it did not earn.
    """
    (MIRRORS / f"{spec.task_id}.json").write_text(
        json.dumps(
            {
                "task_id": spec.task_id,
                "mirrors": [
                    {
                        "repo": spec.url.removeprefix("https://"),
                        "rev": spec.rev,
                        "mirror_id": f"sg-evals/{spec.store}",
                    }
                ],
            },
            indent=2,
        )
        + "\n"
    )


def manifest_entry(spec: Spec, files: list[str]) -> dict:
    return {
        "task_id": spec.task_id,
        "suite": "technical_debt",
        "path": f"benchmarks/technical_debt/{spec.task_id}",
        "store_identifiers": [f"github.com/sg-evals/{spec.store}"],
        "repo_count": 1,
        "required_file_count": len(files),
        "disclosed_fraction": 0.0,
        "categories": list(spec.categories),
    }


def write(spec: Spec, files: list[str]) -> Path:
    directory = BENCHMARKS / spec.task_id
    (directory / "checks").mkdir(parents=True, exist_ok=True)
    (directory / "environment").mkdir(parents=True, exist_ok=True)
    (directory / "task.toml").write_text(task_toml(spec, files))
    (directory / "instruction.md").write_text(instruction(spec))
    (directory / "ground_truth.json").write_text(
        json.dumps(ground_truth(spec, files), indent=2) + "\n"
    )
    check = directory / "checks" / "check_source_files.sh"
    check.write_text(CHECK)
    check.chmod(0o755)
    (directory / "environment" / "Dockerfile").write_text(
        DOCKERFILE.format(task_id=spec.task_id, url=spec.url, rev=spec.rev,
                          store=spec.store, path=spec.path)
    )
    return directory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--derive", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not (args.derive or args.write or args.check):
        parser.error("pick one of --derive / --write / --check")

    drift = 0
    entries: list[dict] = []
    for spec in SPECS:
        files = derive(spec)
        status = "ok" if tuple(files) == spec.expected else "DRIFT"
        if status == "DRIFT":
            drift += 1
        print(f"{status:5s} {len(files):3d} files  {spec.task_id}")
        if args.derive or status == "DRIFT":
            for path in files:
                marker = " " if path in spec.expected else "+"
                print(f"      {marker} {path}")
            for path in spec.expected:
                if path not in files:
                    print(f"      - {path}")
        if args.write and status == "ok":
            write(spec, files)
            mirror_config(spec)
            entries.append(manifest_entry(spec, files))
    if args.write and entries:
        (HERE.parent / "nav_tasks.json").write_text(json.dumps(entries, indent=2) + "\n")
        print(f"\nwrote nav_tasks.json ({len(entries)} tasks)")
    if drift:
        print(f"\n{drift} spec(s) drifted from the pinned key; not written.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
