"""Copying the prompt is not investigation — on every enterprise-hard task.

The defect this pins (bead jn73.2.7, D1)::

    mkdir -p $WORKSPACE/agent_output
    cp $WORKSPACE/instruction.md $WORKSPACE/agent_output/answer.json

scored >0 on 13 of the 14 authored tasks' root-cause/localization checkpoints
and ``passed: true`` on 9 of them — rustls and netty at a perfect 1.00 — for zero
investigation. Scoping the evidence to ``agent_output/`` (an earlier fix) was
correct but orthogonal: the checks grepped for tokens the PROMPT ITSELF supplies.
rustls' check wanted ``rustls`` + ``complete_io|io.?completion`` + "no further
progress", and its instruction.md says "rustls TLS backend", "IO-completion
routine" and "can make no further progress".

The fix credits only evidence the prompt cannot supply: the oracle paths in
ground_truth.json. :func:`test_prompt_never_names_the_oracle_paths` is what makes
that a real defence rather than a coincidence — it fails if a future prompt edit
leaks a path, which would quietly re-open this hole under a green suite.

Scope is the authored stratum's manifest, not a glob: the same prompt-echo class
on main's SHIPPED tasks is a live finding tracked separately as
EnterpriseBench-vjrbw, and pulling those in here would conflate two decisions.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from eb_verify.runner import checkpoint_env

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST = REPO_ROOT / "benchmarks" / "strata" / "enterprise_hard_authored.json"

# The checkpoints that pay for finding the origin — the ones prompt echo robbed.
_EVIDENCE_CHECKS = ("check_root_cause.sh", "check_localization.sh")


@dataclass(frozen=True)
class EvidenceTask:
    """One authored task and the checkpoint that pays for locating the origin."""

    task_id: str
    task_dir: Path
    check: Path
    gt: dict
    instruction: str

    def score(self, workspace: Path, answer: str | None) -> dict:
        """Run the evidence checkpoint against ``answer`` (raw text, or None)."""
        (workspace / "agent_output").mkdir(parents=True, exist_ok=True)
        # The harness plants the prompt at the workspace root; an echo attack has
        # it to hand, so the fixture must too.
        (workspace / "instruction.md").write_text(self.instruction)
        if answer is not None:
            (workspace / "agent_output" / "answer.json").write_text(answer)
        env = checkpoint_env(workspace, self.task_dir, self.task_id)
        proc = subprocess.run(
            ["bash", str(self.check)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(workspace),
            env=env,
        )
        assert proc.stdout.strip(), (
            f"{self.task_id}: {self.check.name} produced no verdict "
            f"(exit {proc.returncode}): {proc.stderr[:400]}"
        )
        return json.loads(proc.stdout)

    def truthful_answer(self) -> str:
        return json.dumps(
            {
                "source_files": [
                    {"path": f["path"], "repo": f["repo"]}
                    for f in self.gt["required_files"]
                ]
            }
        )


def _evidence_tasks() -> list[EvidenceTask]:
    if not MANIFEST.is_file():
        pytest.skip(f"no authored-stratum manifest at {MANIFEST}")
    tasks: list[EvidenceTask] = []
    for entry in json.loads(MANIFEST.read_text())["tasks"]:
        task_dir = REPO_ROOT / entry["path"]
        check = next(
            (task_dir / "checks" / n for n in _EVIDENCE_CHECKS if (task_dir / "checks" / n).is_file()),
            None,
        )
        # Every authored task pays for locating the origin. One that does not has
        # either been renamed or lost the checkpoint — say so rather than skip.
        assert check is not None, (
            f"{entry['task_id']} declares none of {_EVIDENCE_CHECKS}; the checkpoint "
            f"prompt echo exploited would go unexercised."
        )
        tasks.append(
            EvidenceTask(
                task_id=entry["task_id"],
                task_dir=task_dir,
                check=check,
                gt=json.loads((task_dir / "ground_truth.json").read_text()),
                instruction=(task_dir / "instruction.md").read_text(),
            )
        )
    if not tasks:
        raise RuntimeError("authored-stratum manifest yielded no tasks to exercise")
    return tasks


EVIDENCE_TASKS = _evidence_tasks()


@pytest.fixture(params=EVIDENCE_TASKS, ids=lambda t: t.task_id)
def task(request) -> EvidenceTask:
    return request.param


def test_prompt_never_names_the_oracle_paths(task: EvidenceTask) -> None:
    """The premise of the whole fix: the prompt cannot supply the answer.

    Scoring on oracle paths only defeats prompt echo while the prompt names none
    of them — not the path, and not even its basename. A prompt edit that leaks
    one would re-open D1 silently, so this fails first and loudly.
    """
    leaked = [
        f["path"]
        for f in task.gt["required_files"]
        if f["path"] in task.instruction
        or Path(f["path"]).name in task.instruction
    ]
    assert not leaked, (
        f"{task.task_id}: instruction.md names oracle path(s) {leaked}. The "
        f"root-cause checkpoint scores recall over exactly these paths, so the "
        f"prompt now supplies its own answer — D1 is re-opened."
    )


def test_prompt_echo_scores_zero(task: EvidenceTask, tmp_path: Path) -> None:
    """THE defect: `cp instruction.md agent_output/answer.json` earns nothing."""
    verdict = task.score(tmp_path / "ws", task.instruction)
    assert verdict["score"] == 0.0, (
        f"{task.task_id}: copying the prompt into answer.json scored "
        f"{verdict['score']} on {task.check.name} — {verdict['detail']}"
    )
    assert verdict["passed"] is False


def test_prompt_echo_as_valid_json_scores_zero(task: EvidenceTask, tmp_path: Path) -> None:
    """The stronger echo: prompt prose in a WELL-FORMED answer.json.

    A bare `cp` is rejected for being unparseable, which would leave the real
    hole open. This is the attack that actually tests the scoring rule.
    """
    echoed = json.dumps(
        {"analysis": task.instruction, "root_cause": task.instruction,
         "summary": task.instruction}
    )
    verdict = task.score(tmp_path / "ws", echoed)
    assert verdict["score"] == 0.0, (
        f"{task.task_id}: prompt prose in a valid answer.json scored "
        f"{verdict['score']} on {task.check.name} — {verdict['detail']}"
    )


def test_missing_answer_scores_zero(task: EvidenceTask, tmp_path: Path) -> None:
    """An agent that wrote nothing earns nothing."""
    verdict = task.score(tmp_path / "ws", None)
    assert verdict["score"] == 0.0, (
        f"{task.task_id}: a missing answer.json scored {verdict['score']}"
    )


def test_truthful_answer_scores_full(task: EvidenceTask, tmp_path: Path) -> None:
    """The control: naming the oracle paths must still earn full credit.

    Without this, "prompt echo scores 0" is satisfiable by scoring 0 for
    everything — which is how a fix moves a hole instead of closing it.
    """
    verdict = task.score(tmp_path / "ws", task.truthful_answer())
    assert verdict["score"] == 1.0, (
        f"{task.task_id}: the ground-truth file list scored {verdict['score']} on "
        f"{task.check.name} — {verdict['detail']}"
    )
    assert verdict["passed"] is True


def test_partial_answer_scores_partial(task: EvidenceTask, tmp_path: Path) -> None:
    """Naming only the fix site earns real, partial credit — the checkpoint grades."""
    required = task.gt["required_files"]
    if len(required) < 2:
        pytest.skip("task requires a single file; there is no partial to measure")
    partial = json.dumps(
        {"source_files": [{"path": required[0]["path"], "repo": required[0]["repo"]}]}
    )
    verdict = task.score(tmp_path / "ws", partial)
    assert 0.0 < verdict["score"] < 1.0, (
        f"{task.task_id}: naming 1 of {len(required)} required files scored "
        f"{verdict['score']} — {verdict['detail']}"
    )


def test_bare_basename_dump_scores_below_pass(task: EvidenceTask, tmp_path: Path) -> None:
    """Residual B: a filename dictionary is not localization.

    Listing the oracle files by BARE basename names a file in every repo — the
    directory is the evidence the agent read the tree. So a basename dump must
    fall below pass. A repo-root oracle (follow-redirects' index.js) is the one
    file whose bare name is the truthful spelling, so it is credited — but it is
    one of several required files, so the dump still fails.
    """
    basenames = [Path(f["path"]).name for f in task.gt["required_files"]]
    verdict = task.score(tmp_path / "ws", json.dumps({"source_files": basenames}))
    assert verdict["passed"] is False, (
        f"{task.task_id}: a bare-basename dump {basenames} passed on "
        f"{task.check.name} — {verdict['detail']}"
    )


def test_overlisted_answer_scores_below_pass(task: EvidenceTask, tmp_path: Path) -> None:
    """Residual B: over-listing must dilute precision below pass.

    The oracle paths, correct and dir-qualified, drowned in 200 junk paths — the
    ``find /workspace -type f`` shape. Recall is perfect; precision is not, so F1
    lands below pass.
    """
    real = [f["path"] for f in task.gt["required_files"]]
    junk = [f"pkg/module/generated_{i}.py" for i in range(200)]
    verdict = task.score(tmp_path / "ws", json.dumps({"source_files": real + junk}))
    assert verdict["passed"] is False, (
        f"{task.task_id}: {len(real)} real paths + {len(junk)} junk passed on "
        f"{task.check.name} — {verdict['detail']}"
    )
