#!/usr/bin/env python3
"""Convert a CodeScaleBench task into an EnterpriseBench task directory.

Why this exists
---------------
CSB has 298 tasks pinned to sg-evals mirrors of large real repos (kubernetes,
grafana, kafka, envoy, llvm, firefox) with shipped oracle ground truth and a
working composite scorer. EB has the harness: containerised arms, the
filesystem-permission ablation, cost/latency/score capture. Converting is a
schema translation, not a re-authoring.

What it does NOT do
-------------------
Write into benchmarks/. CSB ships a per-task copy of its verifier machinery,
which is exactly the 549-duplicate pattern EB banned, and its grading assets
are not sealed the way EB's integrity model expects. Converted tasks live under
experiments/csb_import/tasks/ and are run by pointing the runner at that dir.

Verifier translation
--------------------
CSB's tests/test.sh reads /workspace/answer.json, runs
promoted_verifier.py (or oracle_checks.py directly, when a task predates
promotion) against tests/task_spec.json, and writes a 0.0-1.0 composite to
/logs/verifier/reward.txt. EB wants a checks/*.sh emitting
{"score", "passed", "detail"} and exiting 0.

Only checks/*.sh reaches the container, flattened, and only ground_truth.json
reaches /workspace/.task. So the scorer sources and the oracle spec both travel
inside the generated check script as base64 payloads: one sealed, root-owned
artifact per task, byte-identical machinery across tasks because it is
generated from the CSB task's own files rather than hand-copied. Tampering with
it trips EB's integrity gate, same as any other verifier.

Usage:
    python3 convert_csb_task.py <csb_task_dir> [<csb_task_dir> ...] --out tasks/
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path
from typing import Any

CSB_ROOT = Path("/home/ds/projects/CodeScaleBench")
MANIFEST = CSB_ROOT / "configs/runs/mirror_creation_manifest.json"

# CSB's own default when a task carries no promoted_verifier.py.
FALLBACK_SUITE = "csb_sdlc_understand"

# EB's task.suite is a closed enum of seven business suites, so a CSB family has
# to land in one of them. The mapping is by intent, not by name: CSB's own family
# is preserved verbatim under [metadata].csb_family so nothing is lost.
SUITE_BY_CSB_PREFIX = {
    "csb_org_migration": "technical_debt",
    "csb_org_compliance": "security_operations",
    "csb_org_platform": "platform_engineering",
    "csb_org_org": "platform_engineering",
    "csb_sdlc_secure": "security_operations",
    "csb_sdlc_debug": "incident_response",
    "csb_sdlc_refactor": "technical_debt",
}
DEFAULT_EB_SUITE = "platform_engineering"


class ConversionError(RuntimeError):
    """A CSB task cannot be represented as an EB task. Never converted silently."""


def load_mirror_index(manifest: Path = MANIFEST) -> dict[str, dict[str, str]]:
    """mirror name -> {url, rev}.

    derive_mirror_name() drops the upstream org, so 'sg-evals/envoy--v1.31.2'
    cannot be inverted to a URL on its own. This manifest is the only record of
    the mapping.
    """
    data = json.loads(manifest.read_text())
    index: dict[str, dict[str, str]] = {}
    for entry in data.get("mirrors", []):
        mirror = entry.get("mirror")
        upstream = entry.get("upstream")
        commit = entry.get("commit")
        if not (mirror and upstream and commit):
            continue
        # rev is the mirror's own ref suffix, not the commit: EB re-derives the
        # mirror name from url+rev via derive_mirror_name(), and only the suffix
        # round-trips to the name Sourcegraph actually has indexed. The clone
        # itself takes the mirror's default branch, which is already pinned, so
        # the commit is recorded as metadata rather than used as a checkout ref.
        index[mirror] = {"url": f"https://{upstream}",
                         "rev": mirror.split("--", 1)[1],
                         "commit": commit}
    if not index:
        raise ConversionError(f"{manifest} listed no resolvable mirrors")
    return index


def _toml_str(value: str) -> str:
    return json.dumps(value)


def _target_suite(task_dir: Path) -> str:
    """The suite whose composite weights CSB's test.sh applies.

    test.sh hardcodes it per task; there is no other record of it.
    """
    test_sh = task_dir / "tests/test.sh"
    if test_sh.exists():
        match = re.search(r'^TARGET_SUITE="([^"]+)"', test_sh.read_text(), re.M)
        if match:
            return match.group(1)
    return FALLBACK_SUITE


def _parse_csb_toml(path: Path) -> dict[str, Any]:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - py<3.11
        import tomli as tomllib  # type: ignore[no-redef]
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _required_files(task_dir: Path, repo_path: str) -> list[dict[str, Any]]:
    """CSB ground truth files, as EB ground_truth.required_files entries.

    CSB qualifies paths as '<repo>::<path>' where <repo> is its own short name,
    which need not match the directory the repo is cloned into here. The mount
    path is what EB's retrieval metrics key on, so rewrite to it.
    """
    gt_file = task_dir / "tests/ground_truth.json"
    if not gt_file.exists():
        return []
    gt = json.loads(gt_file.read_text())
    entries: list[dict[str, Any]] = []
    for raw in gt.get("files", []):
        path = raw.split("::", 1)[1] if "::" in raw else raw
        entries.append({"path": path, "repo": repo_path, "confidence": 0.9,
                        "source": "deterministic"})
    return entries


CHECK_TEMPLATE = '''#!/usr/bin/env bash
# check_csb_oracle.sh — generated by experiments/csb_import/convert_csb_task.py
#
# Runs CSB's own composite scorer over /workspace/answer.json and translates the
# 0.0-1.0 reward into EB's checkpoint verdict contract. Do not hand-edit: the
# script is sealed root-only and any change trips the integrity gate.
#
# Source task: {source_task}
# Suite weights: {suite}
# Repo mount: /workspace/{repo_path}
set -uo pipefail

WORKSPACE="${{WORKSPACE:-/workspace}}"
ANSWER="$WORKSPACE/answer.json"
SUITE={suite_q}
REPO_PATH={repo_path_q}

verdict() {{ printf '{{"score": %s, "passed": %s, "detail": "%s"}}\\n' "$1" "$2" "$3"; exit 0; }}

if [[ -L "$ANSWER" ]]; then
  verdict 0.0 false "answer.json is a symlink, not a regular file"
fi
if [[ ! -f "$ANSWER" ]]; then
  verdict 0.0 false "answer.json not found at $ANSWER"
fi

SCORER_DIR="$(mktemp -d)"
trap 'rm -rf "$SCORER_DIR"' EXIT

base64 -d >"$SCORER_DIR/oracle_checks.py" <<'ORACLE_B64'
{oracle_b64}
ORACLE_B64
base64 -d >"$SCORER_DIR/task_spec.json" <<'SPEC_B64'
{spec_b64}
SPEC_B64
{promoted_block}
export SCORER_DIR ANSWER SUITE REPO_PATH
python3 - <<'PYEOF'
import json, os, subprocess, sys

scorer = os.environ["SCORER_DIR"]
answer = os.environ["ANSWER"]
suite = os.environ["SUITE"]
repo_path = os.environ["REPO_PATH"]
spec = f"{{scorer}}/task_spec.json"
promoted = f"{{scorer}}/promoted_verifier.py"


def verdict(score, detail):
    print(json.dumps({{"score": round(score, 4),
                      "passed": score >= 0.5,
                      "detail": detail[:400]}}))
    raise SystemExit(0)


def _repo_relative(value):
    """Strip the EB mount prefix from a cited path.

    CSB's oracle predates EB's sandbox and matches repo-relative paths
    ("clients/src/.../Authorizer.java"). EB clones the repo to
    /workspace/{{repo}}/, and agents that reach code through remote search cite
    the absolute mount path while agents reading it locally cite the relative
    one. Left unnormalised, that difference alone scores an identical file set
    at 1.00 in one arm and 0.00 in another. The rewrite is applied to every arm
    and changes which string is compared, never which file was named.
    """
    if not isinstance(value, str):
        return value
    for prefix in (f"/workspace/{{repo_path}}/", f"workspace/{{repo_path}}/",
                   f"/{{repo_path}}/", "./"):
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def _normalise(node):
    if isinstance(node, dict):
        return {{k: (_repo_relative(v) if k in ("path", "file") else _normalise(v))
                for k, v in node.items()}}
    if isinstance(node, list):
        return [_repo_relative(x) if isinstance(x, str) else _normalise(x)
                for x in node]
    return node


try:
    with open(answer) as fh:
        payload = json.load(fh)
except (json.JSONDecodeError, OSError) as exc:
    verdict(0.0, f"answer.json is not readable JSON: {{exc}}".replace('"', "'"))

normalised = f"{{scorer}}/answer.json"
with open(normalised, "w") as fh:
    json.dump(_normalise(payload), fh)
answer = normalised

if os.path.exists(promoted):
    cmd = [sys.executable, promoted, "--answer", answer, "--spec", spec,
           "--suite", suite, "--output", f"{{scorer}}/validation_result.json"]
else:
    cmd = [sys.executable, f"{{scorer}}/oracle_checks.py", "--answer", answer,
           "--spec", spec]

proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
tail = (proc.stdout or "").strip().splitlines()
if not tail:
    # No score at all means the scorer itself failed. Surface it as an infra
    # error rather than banking a fabricated 0.0 — see scorer_guard.py.
    verdict(0.0, "VERIFIER_INFRA_ERROR: csb scorer produced no score: "
                 + (proc.stderr or "")[-200:].replace('"', "'"))
try:
    score = float(tail[-1])
except ValueError:
    verdict(0.0, "VERIFIER_INFRA_ERROR: csb scorer returned non-numeric "
                 + tail[-1][:120].replace('"', "'"))

detail = f"csb composite {{score:.4f}} (suite {{suite}})"
result = f"{{scorer}}/validation_result.json"
if os.path.exists(result):
    try:
        per = json.load(open(result)).get("per_check", {{}})
        parts = ", ".join(f"{{k}}={{v.get('score', 0):.2f}}" for k, v in per.items())
        detail = f"{{detail}}; {{parts}}"
    except (json.JSONDecodeError, OSError):
        pass
verdict(score, detail)
PYEOF
'''

PROMOTED_BLOCK = '''base64 -d >"$SCORER_DIR/promoted_verifier.py" <<'PROMOTED_B64'
{promoted_b64}
PROMOTED_B64
'''


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def convert(task_dir: Path, out_root: Path, mirrors: dict[str, dict[str, str]]) -> Path:
    toml_path = task_dir / "task.toml"
    if not toml_path.exists():
        raise ConversionError(f"{task_dir}: no task.toml")
    csb = _parse_csb_toml(toml_path)
    task = csb.get("task", {})

    task_id = task.get("id")
    if not task_id:
        raise ConversionError(f"{task_dir}: task.toml has no [task].id")

    mirror = task.get("repo")
    if not mirror:
        raise ConversionError(f"{task_id}: task.toml names no repo")
    if mirror not in mirrors:
        raise ConversionError(
            f"{task_id}: mirror {mirror!r} is absent from the creation manifest, "
            "so its upstream URL and pinned commit cannot be recovered"
        )
    repo = mirrors[mirror]
    repo_path = mirror.split("/", 1)[1].split("--", 1)[0]

    spec = task_dir / "tests/task_spec.json"
    oracle = task_dir / "tests/oracle_checks.py"
    if not spec.exists() or not oracle.exists():
        raise ConversionError(
            f"{task_id}: needs tests/task_spec.json and tests/oracle_checks.py; "
            "tasks on other verifier styles are out of scope for this converter"
        )

    instruction = task_dir / "instruction.md"
    if not instruction.exists():
        raise ConversionError(f"{task_id}: no instruction.md")

    suite = _target_suite(task_dir)
    promoted = task_dir / "tests/promoted_verifier.py"
    promoted_block = (
        PROMOTED_BLOCK.format(promoted_b64=_b64(promoted)) if promoted.exists() else ""
    )

    csb_family = task.get("mcp_suite") or task_dir.parent.name
    eb_suite = SUITE_BY_CSB_PREFIX.get(csb_family) or SUITE_BY_CSB_PREFIX.get(
        suite, DEFAULT_EB_SUITE
    )
    difficulty = task.get("difficulty", "hard")
    if difficulty not in ("medium", "hard", "expert"):
        difficulty = "hard"

    slug = task_id.lower()
    dest = out_root / slug
    (dest / "checks").mkdir(parents=True, exist_ok=True)

    (dest / "checks/check_csb_oracle.sh").write_text(
        CHECK_TEMPLATE.format(
            source_task=task_id,
            suite=suite,
            suite_q=_toml_str(suite),
            oracle_b64=_b64(oracle),
            spec_b64=_b64(spec),
            repo_path=repo_path,
            repo_path_q=_toml_str(repo_path),
            promoted_block=promoted_block,
        )
    )
    (dest / "checks/check_csb_oracle.sh").chmod(0o755)

    (dest / "instruction.md").write_text(instruction.read_text())
    mcp_instruction = task_dir / "instruction_mcp.md"
    if mcp_instruction.exists():
        (dest / "instruction_mcp.md").write_text(mcp_instruction.read_text())

    required = _required_files(task_dir, repo_path)
    (dest / "ground_truth.json").write_text(
        json.dumps(
            {
                "source": "codescalebench",
                "source_task_id": task_id,
                "csb_suite": suite,
                "required_files": required,
            },
            indent=2,
        )
        + "\n"
    )

    lines = [
        f"# Imported from CodeScaleBench {task_id} "
        "(experiments/csb_import/convert_csb_task.py)",
        "",
        # Every convertible CSB task names exactly one repo.
        'difficulty_stratum = "large_single"',
        'mcp_suite = "eb_v1"',
        "org_scale = true",
        'verification_modes = ["deterministic"]',
        "",
        "[task]",
        f"id = {_toml_str(slug)}",
        f"suite = {_toml_str(eb_suite)}",
        # task_type is left unset: EB's ten types describe multi-repo retrieval
        # shapes and none of them honestly names a CSB task. It is optional.
        f'difficulty = {_toml_str(difficulty)}',
        'session_type = "single"',
        f"estimated_duration_minutes = {int(task.get('time_limit_sec', 900)) // 60}",
        f"description = {_toml_str(csb.get('metadata', {}).get('description', task_id))}",
        "",
        "[[repos]]",
        f"url = {_toml_str(repo['url'])}",
        f"rev = {_toml_str(repo['rev'])}",
        f"path = {_toml_str(repo_path)}",
        'role = "primary"',
        "",
        "[metadata]",
        f'languages = [{_toml_str(task.get("language", "unknown"))}]',
        f"csb_mirror = {_toml_str(mirror)}",
        f"csb_commit = {_toml_str(repo['commit'])}",
        f"csb_family = {_toml_str(csb_family)}",
        f"csb_category = {_toml_str(str(task.get('category', '')))}",
        "",
        "[[checkpoints]]",
        'name = "csb_oracle_composite"',
        "weight = 1.0",
        'verifier = "checks/check_csb_oracle.sh"',
        f'description = "CSB {suite} composite over answer.json '
        '(file set, symbols, chain, keywords)"',
        "timeout_seconds = 300",
        "",
        "[artifacts]",
        'required = ["answer"]',
        "",
        "[ground_truth]",
        'tiers = ["deterministic"]',
    ]
    for entry in required:
        lines += [
            "",
            "[[ground_truth.required_files]]",
            f"path = {_toml_str(entry['path'])}",
            f"repo = {_toml_str(entry['repo'])}",
            "confidence = 0.9",
            'source = "deterministic"',
        ]
    (dest / "task.toml").write_text("\n".join(lines) + "\n")
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tasks", nargs="+", type=Path)
    parser.add_argument("--out", type=Path,
                        default=Path(__file__).parent / "tasks")
    args = parser.parse_args()

    mirrors = load_mirror_index()
    failed = 0
    for task_dir in args.tasks:
        try:
            dest = convert(task_dir, args.out, mirrors)
        except ConversionError as exc:
            print(f"SKIP {exc}", file=sys.stderr)
            failed += 1
            continue
        print(f"OK   {task_dir.name} -> {dest}")
    if failed:
        print(f"\n{failed} task(s) skipped", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
