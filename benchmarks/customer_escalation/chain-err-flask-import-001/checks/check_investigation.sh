#!/usr/bin/env bash
# check_investigation.sh — checkpoint "investigation_evidence"
#
# Grades the evidence behind the verdict, and only for a verdict that is right.
#
# Two defects, fixed in this order (EnterpriseBench-e4w15):
#
# 1. It used to grep "circular import" and "module" — concept vocabulary the session
#    prompt states verbatim — so a copy of the prompt scored 1.0. The tokens now come
#    from ground_truth.json:scoring_evidence and name only what a reader of
#    src/flask/globals.py has and no prompt supplies: the TYPE_CHECKING guard that
#    makes the claimed flask.globals -> flask.app edge type-checker-only, and the
#    werkzeug LocalProxy / ContextVar machinery globals.py imports at runtime instead.
#
# 2. Non-prompt turned out to be necessary and NOT sufficient. All three tokens live
#    in the first EIGHT lines of that one file, and the prompt names the file, so
#    `cat src/flask/globals.py > INVESTIGATION.md` scored 1.0 — 0.30 of the task for a
#    file copy, with `head -8` paying the same (which is why a length threshold fixes
#    nothing). The token set is not the defect. Free text scored by substring is
#    dump-payable BY CONSTRUCTION, and a multi-file cat pays any token drawn from
#    source — measured: a concatenation of globals.py, app.py, json/__init__.py and
#    __init__.py pays every candidate token — so spreading the tokens across the files
#    a real investigation must read does not close it either. What closes it is the
#    GATE below: evidence is credited only if CYCLE_VERDICT.json classified all four
#    claimed edges correctly. No dump produces that verdict, and a naive reader who
#    takes `from .app import Flask` under the guard at face value gets it wrong. This
#    is the shape check_resolution.sh already uses, where the decision gates evidence.
#
# The gate gives up nothing the ungated design held: scoring 1.0 always required all
# four edges right anyway, so the lucky-guesser tail is unchanged at 1/16 (enumerated
# over all 16 guesses, not argued). What it removes is the GUARANTEED credit — for the
# best no-comprehension strategy E[score] falls 0.675 -> 0.1125.
#
# The tokens are flat strings scored one-for-one, which is what
# tests/integrity/test_scoring_evidence_is_nonprompt.py can read and assert are
# absent from the prompt. A nested any-of shape would be silently skipped by that
# invariant (it only walks `isinstance(t, str)`), trading brittleness for a blind
# spot. Each token is instead chosen to survive lowercased substring matching:
# "type_checking" matches TYPE_CHECKING, "contextvar" matches both ContextVar and
# contextvars, "werkzeug" matches werkzeug.local.
#
# ground_truth.json is sealed root-only; the agent cannot read the key.
set -euo pipefail

CHECKPOINT="investigation_evidence"
# See check_cycle.sh for why the workspace and TASK_DIR are resolved this way (three
# runners, three conventions) and why the literal "$WORKSPACE/..." spelling matters.
WORKSPACE="${1:-${WORKSPACE:-/workspace}}"
REPORT="$WORKSPACE/agent_output/INVESTIGATION.md"
# The gate's input. Session 1 writes both files, and session 2 inherits them on the
# branch, so CYCLE_VERDICT.json is present wherever this check runs — as a session-1
# milestone and as a final weighted checkpoint.
CYCLE="$WORKSPACE/agent_output/CYCLE_VERDICT.json"
TASK_DIR="${TASK_DIR:-$(dirname "$(dirname "$(realpath "$0")")")}"
GT="$TASK_DIR/ground_truth.json"
MAX_BYTES=1048576

verdict() { printf '{"score": %s, "passed": %s, "detail": "%s"}\n' "$1" "$2" "$3"; exit 0; }

if [ ! -f "$GT" ]; then
    verdict 0.0 false "VERIFIER_INFRA_ERROR: ground_truth.json not found at $GT"
fi
if [ -L "$REPORT" ]; then
    verdict 0.0 false "INVESTIGATION.md is a symlink, not a regular file"
fi
if [ ! -f "$REPORT" ]; then
    verdict 0.0 false "INVESTIGATION.md not found"
fi
if [ "$(wc -c <"$REPORT")" -gt "$MAX_BYTES" ]; then
    verdict 0.0 false "INVESTIGATION.md exceeds $MAX_BYTES bytes"
fi
export REPORT CYCLE GT CHECKPOINT MAX_BYTES
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
# the staging contract. Keep the two copies identical.
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

evidence = (gt.get("scoring_evidence") or {}).get(os.environ["CHECKPOINT"]) or []
if not evidence:
    out(0.0, "VERIFIER_INFRA_ERROR: no scoring_evidence for " + os.environ["CHECKPOINT"])
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

if not verdict_is_fully_correct(key):
    out(0.0, "CYCLE_VERDICT.json is missing or does not classify every claimed edge "
             "correctly, so the evidence for it is not credited")

try:
    with open_agent_file(os.environ["REPORT"], errors="replace") as fh:
        text = fh.read().lower()
except OSError as exc:
    # The agent owns this file and can chmod 000 it; the bash wc -c guard fails open on
    # a permission error, so this open() was reached unguarded and killed the check with
    # a bare traceback and no verdict (EnterpriseBench-e4w15). errors="replace" already
    # rules out UnicodeDecodeError.
    out(0.0, "INVESTIGATION.md could not be read: %s" % type(exc).__name__)

found = sum(1 for token in evidence if token.lower() in text)
out(found / len(evidence),
    "Cited %d/%d non-prompt evidence tokens for %s"
    % (found, len(evidence), os.environ["CHECKPOINT"]))
'
