"""Grounded-citations gate over the answer artifact.

This is what gives ``ground_truth.require_grounded_citations`` teeth in the
production scoring path. The flag already shapes the agent's prompt; without
this gate nothing checks the result, so an answer with fabricated citations
scores exactly as well as a sourced one.

Two contracts here are load-bearing:

``gate_answer_citations`` reads ONE caller-pinned path. It deliberately does
not glob: AnswerValidator takes ``workspace.glob("**/answer.json")[0]``, and
pathlib yields the anchor directory first, so on the world-writable workspace
of a live session a decoy at the root wins over the ``agent_output/answer.json``
that every check script actually grades. A gate that globbed would grade a file
nothing else grades.

``main`` reports the verdict as JSON on stdout and exits 0 whenever the gate
ran, pass or fail. The exit code answers "did the gate run", never "did it
pass" — a nonzero exit on a failed gate is indistinguishable from a crashed
one, and the caller routes an unreadable gate to the re-run channel rather
than to a zero.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from eb_verify.groundedness import CitationParseError, ground_citations

# The answer artifact every check script pins and the instruction tells the
# agent to write. Relative to the workspace root; the caller passes the
# absolute path, this is the shared definition of where it lives.
ANSWER_RELPATH = "agent_output/answer.json"


@dataclass(frozen=True)
class GateVerdict:
    """Whether the answer's citations are grounded, and why."""

    passed: bool
    detail: str

    def as_dict(self) -> dict:
        return {"passed": self.passed, "detail": self.detail}


def gate_answer_citations(answer_path: Path, workspace: Path) -> GateVerdict:
    """Check every citation in *answer_path* against files in *workspace*.

    *answer_path* is read as given and never globbed for — the caller pins the
    artifact the checks grade. A citation is grounded when its evidence_span
    appears verbatim in the cited workspace file; every citation must be
    grounded for the gate to pass.
    """
    try:
        raw = answer_path.read_text()
    except (OSError, UnicodeDecodeError) as e:
        return GateVerdict(False, f"no answer artifact to gate at {answer_path}: {e}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return GateVerdict(False, f"answer.json invalid: {e}")
    if not isinstance(data, dict):
        return GateVerdict(False, "answer.json should be a JSON object")

    try:
        grounded = ground_citations(data, workspace)
    except CitationParseError as e:
        return GateVerdict(False, "; ".join(e.issues))

    # An empty citation list scores 1.0 in check_groundedness ("no claims,
    # nothing ungrounded") — correct for a metric, wrong for a gate. A task
    # that demands grounded citations is not satisfied by citing nothing.
    if not grounded.results:
        return GateVerdict(
            False,
            "citations list is empty; require_grounded_citations demands at "
            "least one grounded citation",
        )

    if not grounded.all_grounded:
        return GateVerdict(False, f"ungrounded citations: {grounded.summary()}")
    return GateVerdict(True, grounded.summary())


def main(argv: list[str] | None = None) -> int:
    """Emit the verdict as JSON on stdout. Returns 0 whenever the gate ran.

    Exit status is the "did the gate run" channel only: a failed gate exits 0
    with ``passed: false``. See the module docstring for why.
    """
    parser = argparse.ArgumentParser(
        prog="python -m eb_verify.artifact_gate",
        description="Gate an answer artifact on grounded citations.",
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument(
        "--answer",
        type=Path,
        help=f"Answer artifact path (default: <workspace>/{ANSWER_RELPATH}).",
    )
    args = parser.parse_args(argv)

    answer = args.answer or args.workspace / ANSWER_RELPATH
    verdict = gate_answer_citations(answer, args.workspace)
    # stdout carries the verdict and nothing else — the caller json.loads it.
    print(json.dumps(verdict.as_dict()))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
