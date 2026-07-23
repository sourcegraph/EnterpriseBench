#!/usr/bin/env bash
# check_resolution.sh — checkpoint "resolution_correct"
#
# Grades the DECISION, not a diff. This was check_fix.sh, which awarded its full
# weight for (a) a FIX_SUMMARY.md over 50 bytes and (b) any *.py file showing up in
# the workspace repo's diff against HEAD~1 — bare changed-file membership. It could
# not tell a fix from a comment because it never read the code's meaning, so one
# comment appended to tests/conftest.py paid 0.40 (EnterpriseBench-uc12m / -e4w15).
#
# The correct resolution is that NO code change is warranted: the reported cycle
# does not exist, so there is nothing to break, and patching working code to defer
# an import that is already absent would add risk and fix nothing. Because the
# decision is the deliverable, no diff is graded at all — that is what removes the
# comment vector, rather than tightening a match that was never measuring the fix.
#
# The decision GATES the evidence rather than scoring beside it: a wrong decision is
# 0 outright, and a right one earns only what its reason can evidence. So guessing
# "no change" — which the escalation pressure in session 2 invites — pays nothing on
# its own.
#
# The verdict gates it too, for the same reason and against a sharper vector. `reason`
# is free text, so ANY token rule over it is dump-payable: the sole token for this
# checkpoint (type_checking) sits in the first eight lines of src/flask/globals.py, so
# pasting that file into `reason` with code_change_required=false scored 1.0 — 0.30 of
# the task for a file copy plus a coin flip. Retokenizing cannot fix that (a multi-file
# cat pays any token drawn from source; measured), and the decision is only one bit, so
# it cannot carry the gate alone. Requiring the verdict to be right as well means the
# refusal to patch has to rest on an investigation that actually landed
# (EnterpriseBench-e4w15). See check_investigation.sh for the full reasoning and the
# enumerated cost.
#
# ground_truth.json is sealed root-only; the agent cannot read the key.
set -euo pipefail

CHECKPOINT="resolution_correct"
# See check_cycle.sh for why the workspace and TASK_DIR are resolved this way (three
# runners, three conventions) and why the literal "$WORKSPACE/..." spelling matters.
WORKSPACE="${1:-${WORKSPACE:-/workspace}}"
RESOLUTION="$WORKSPACE/agent_output/RESOLUTION.json"
# The gate's input. Session 1 writes it and the chain commits the repo between
# sessions, so it is on the branch by the time this check runs. (task.toml declares
# this script as a session-2 milestone too, but chain_runner.py:392 skips the FINAL
# session's milestones — `session_num < session_count` — so that milestone never fires.
# Not this bead's to fix; see EnterpriseBench-2eqr6.)
CYCLE="$WORKSPACE/agent_output/CYCLE_VERDICT.json"
TASK_DIR="${TASK_DIR:-$(dirname "$(dirname "$(realpath "$0")")")}"
GT="$TASK_DIR/ground_truth.json"
MAX_BYTES=1048576

verdict() { printf '{"score": %s, "passed": %s, "detail": "%s"}\n' "$1" "$2" "$3"; exit 0; }

if [ ! -f "$GT" ]; then
    verdict 0.0 false "VERIFIER_INFRA_ERROR: ground_truth.json not found at $GT"
fi
if [ -L "$RESOLUTION" ]; then
    verdict 0.0 false "RESOLUTION.json is a symlink, not a regular file"
fi
if [ ! -f "$RESOLUTION" ]; then
    verdict 0.0 false "RESOLUTION.json not found"
fi
if [ "$(wc -c <"$RESOLUTION")" -gt "$MAX_BYTES" ]; then
    verdict 0.0 false "RESOLUTION.json exceeds $MAX_BYTES bytes"
fi
export RESOLUTION CYCLE GT CHECKPOINT MAX_BYTES
# See check_cycle.sh for why this is `python3 -I` and not `python3` (an agent's
# json.py beside its deliverables would otherwise shadow the stdlib and mint this
# script's verdict). -I implies -E, which drops PYTHON* vars only: the exports
# above still arrive via os.environ.
python3 -I -c '
import json, os

def out(score, detail):
    print(json.dumps({"score": round(score, 4), "passed": score >= 0.5, "detail": detail}))
    raise SystemExit(0)


# norm() and the gate below are copied from check_cycle.sh by CONTRACT, not by
# oversight. run_task.py stages only checks/*.sh, copying each one individually into a
# flat /workspace/.verifiers/. A shared library outside checks/ is never copied (every
# check then breaks in-sandbox); one inside checks/ is copied, lands renamed, and
# test_runner.sh runs it AS a check at default weight 1.0 — pushing task_score above
# 1.0 and tripping test_every_check_is_claimed_by_a_checkpoint. Self-containment is
# the staging contract. Keep the copies identical.
def open_agent_file(path, errors=None):
    """Open an agent-owned deliverable, refusing anything but a regular file.

    The agent owns the directory, so a [ -L ]/[ -f ] test then a separate open() has a
    window it can swap inside. Measured, not theorised — a review raced the guarded
    check with concurrent swappers and forged a perfect 1.0 on ~22% of 1500 invocations,
    because CYCLE_VERDICT.json carries ground_truth.json exact schema, so a symlinked
    read of the sealed key IS a perfect verdict. Three flags close the window at open()
    time, where there is nothing to race:
      O_NOFOLLOW  a symlink swapped in raises instead of reading the target.
      O_NONBLOCK  a FIFO swapped in returns instead of blocking the read until a writer
                  appears — a hang becomes a verifier_timeout, i.e. a no-verdict route
                  the agent can trigger for a free re-run.
      fstat S_ISREG  a FIFO, device or directory (which O_NOFOLLOW alone lets through)
                  is rejected after the descriptor is open, so it is the SAME object the
                  read will use, not a re-lstat of the path. Regular files ignore
                  O_NONBLOCK for reads, so this is a no-op on the honest case.
    The bash [ -L ]/[ -f ] guards stay for the clear message on the honest case; this is
    what makes them sound (EnterpriseBench-e4w15).
    """
    import stat
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError("agent deliverable is not a regular file: %s" % path)
    return os.fdopen(fd, "r", encoding="utf-8", errors=errors)


def norm(value):
    """Collapse every spelling of one module onto its dotted name.

    See check_cycle.sh for why this anchors on the LAST "src" segment: the repo mounts
    at /workspace/flask/ with the package under src/, so the spellings this workspace
    produces are flask/src/flask/globals.py and /workspace/flask/src/flask/globals.py.
    """
    s = str(value).strip().lower().replace("\\", "/")
    if s.endswith("/__init__.py"):
        s = s[: -len("/__init__.py")]
    elif s.endswith(".py"):
        s = s[:-3]
    parts = [p for p in s.replace("/", ".").split(".") if p]
    for i in range(len(parts) - 1, -1, -1):
        # A trailing "src" names a module, not the package root: cutting there would
        # collapse "flask/src.py" to "" and, with both endpoints empty, silently drop
        # the edge — which negative marking then charges as unanswered.
        if parts[i] == "src" and i + 1 < len(parts):
            parts = parts[i + 1 :]
            break
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def verdict_is_fully_correct(key):
    """Did CYCLE_VERDICT.json classify EVERY claimed edge correctly?

    Every failure here is False, never an infra error: the agent owns that file, so a
    missing, symlinked, oversized, unreadable or malformed one is a wrong answer, not a
    broken grader.

    These guards live in python, not in the bash preamble beside the others, so the
    ANSWER KEY is parsed FIRST. A bash pre-check fires before ground_truth.json has been
    validated, so a corrupt key plus a missing verdict returned a plain
    {"score": 0.0} instead of VERIFIER_INFRA_ERROR — a broken grader recorded as a real
    agent failure, which is the one thing the scoring trust boundary forbids. Measured
    both ways; see lib/eb_verify/scorer_guard.py (EnterpriseBench-e4w15).

    islink before isfile: isfile follows symlinks, and the sealed ground_truth.json IS a
    perfect verdict for this task, so a symlink to it would forge the gate.
    """
    path = os.environ["CYCLE"]
    if os.path.islink(path) or not os.path.isfile(path):
        return False
    try:
        if os.path.getsize(path) > int(os.environ["MAX_BYTES"]):
            return False
        with open_agent_file(path) as fh:
            data = json.load(fh)
    except (ValueError, OSError, RecursionError):
        return False
    if not isinstance(data, dict) or not isinstance(data.get("claimed_edges"), list):
        return False
    answered = {}
    for entry in data["claimed_edges"]:
        if not isinstance(entry, dict):
            continue
        try:
            edge = (norm(entry["from"]), norm(entry["to"]))
        except KeyError:
            continue
        flag = entry.get("imported_at_runtime")
        if edge in key and isinstance(flag, bool):
            answered[edge] = flag
    return all(answered.get(e) is want for e, want in key.items())


try:
    with open(os.environ["GT"]) as fh:
        gt = json.load(fh)
except (ValueError, OSError) as exc:
    out(0.0, "VERIFIER_INFRA_ERROR: unreadable ground_truth.json: %s" % type(exc).__name__)
if not isinstance(gt, dict):
    out(0.0, "VERIFIER_INFRA_ERROR: ground_truth.json is not an object")

expected = gt.get("code_change_required")
evidence = (gt.get("scoring_evidence") or {}).get(os.environ["CHECKPOINT"]) or []
if not isinstance(expected, bool) or not evidence:
    out(0.0, "VERIFIER_INFRA_ERROR: ground_truth lacks code_change_required/scoring_evidence")
try:
    key = {
        (norm(e["from"]), norm(e["to"])): bool(e["imported_at_runtime"])
        for e in gt["claimed_edges"]
    }
except (KeyError, TypeError) as exc:
    out(0.0, "VERIFIER_INFRA_ERROR: unreadable claimed_edges in ground_truth.json: %s"
        % type(exc).__name__)
if not key:
    out(0.0, "VERIFIER_INFRA_ERROR: ground_truth.json has no claimed_edges")

try:
    with open_agent_file(os.environ["RESOLUTION"]) as fh:
        data = json.load(fh)
except (ValueError, OSError, RecursionError) as exc:
    # OSError too: see check_cycle.sh — the agent can chmod 000 its own deliverable and
    # the bash wc -c guard fails open on it.
    out(0.0, "RESOLUTION.json could not be read as JSON: %s" % type(exc).__name__)

if not isinstance(data, dict):
    out(0.0, "RESOLUTION.json is not a JSON object")

decision = data.get("code_change_required")
if not isinstance(decision, bool):
    out(0.0, "RESOLUTION.json has no boolean code_change_required")
if decision is not expected:
    out(0.0, "Wrong resolution: code_change_required=%s" % decision)
if not verdict_is_fully_correct(key):
    out(0.0, "Right decision, but CYCLE_VERDICT.json is missing or does not classify "
             "every claimed edge correctly, so it does not rest on the investigation")

reason = data.get("reason")
if not isinstance(reason, str):
    out(0.0, "Right decision but no reason string to evidence it")
text = reason.lower()

found = sum(1 for token in evidence if token.lower() in text)
out(found / len(evidence),
    "Correct decision; reason cited %d/%d non-prompt evidence tokens"
    % (found, len(evidence)))
'
