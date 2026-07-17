"""A comment is not a patch — on every enterprise-hard task that asks for one.

The defect this pins (bead jn73.2.7, D2): ``check_patch_site`` awarded 1.0 for a
filename appearing in ``git diff --name-only``, and ``check_patch_semantics``
grepped raw ``+`` lines for a token. So appending ONE line at the true fix site::

    # note: could use .split( someday

scored 1.0 on BOTH patch checkpoints — 60-65% of task weight — while the
vulnerable regex sat untouched. All 14 tasks shared the shape, and nothing in
the suite would have caught it: ``test_enterprise_hard_stratum.py`` tests the
authored *manifest* and never runs a verifier.

Each task is exercised four ways against a seeded repo, because a checkpoint
that only rejects is as broken as one that only accepts:

* comment-only edit at the true fix site -> 0.0 on both checkpoints (the defect)
* the same line as real code            -> full credit on both (the control)
* no diff at all                        -> 0.0
* real code at a WRONG path             -> 0.2 site / 0.0 semantics (pathspec
  anchoring, which already worked and must keep working)

The per-task fix line is the fixture. It is written once as code and once
commented out, so the negative case carries the EXACT token the task's semantic
grep wants: that is what makes the test prove comment-stripping rather than
merely prove the token was absent.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from eb_verify.runner import checkpoint_env
from scripts.validation.crnt_validator import parse_toml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BENCHMARKS = REPO_ROOT / "benchmarks"

# One line (or two) of real code that earns FULL credit from each task's own
# check_patch_semantics/check_patch_applies grep. Hand-written per task against
# the upstream fix, and deliberately not derived from the check script — a
# fixture generated from the thing under test would pass no matter what the
# thing under test does.
FIX_LINES: dict[str, tuple[str, ...]] = {
    "incident-rack-range-redos-001": (
        "r0, r1 = range_spec.split('-', 2)",
    ),
    "err-provenance-dual-aiohttp-zoneid-001": (
        "host = host.replace('%25', '%')",
    ),
    "err-provenance-dual-kombu-serializer-001": (
        "except KeyError:",
        "    raise SerializerNotInstalled(name)",
    ),
    "err-provenance-dual-markupsafe-striptags-001": (
        '_strip_tags_re = re.compile(r"<[^>]*>", re.DOTALL)',
    ),
    # Upstream 5fc48e711b, spelled as the real repo spells it: 2.0.7 dispatches on
    # `mode == "gzip"` in _get_decoder and registers `CONTENT_DECODERS = ["gzip",
    # "deflate"]`. An earlier fixture invented `content_encoding in (...)` — a
    # shape absent from urllib3 — which let the check's positive control pass
    # while the genuine two-site fix scored 0.6 (bead jn73.2.7, defect B).
    "err-provenance-dual-urllib3-xgzip-001": (
        'if mode in ("gzip", "x-gzip"):',
        'CONTENT_DECODERS = ["gzip", "x-gzip", "deflate"]',
    ),
    "error-prov-dual-reqwest-hyper-chunk-001": (
        "let size = size.checked_mul(16).ok_or_else(|| io::Error::new(InvalidData, "
        '"chunk size overflow"))?;',
    ),
    "escalation-netty-gzip-fextra-001": (
        "xlen = xlen1 | xlen2 << 8;",
    ),
    "escalation-spring-mimetype-oob-001": (
        "if (nextIndex >= 1) { return parseMimeTypeInternal(value, nextIndex); }",
    ),
    "incident-h2-continuation-flood-001": (
        "if self.continuation_count > MAX_CONTINUATION_FRAMES { "
        "return Err(Reason::PROTOCOL_ERROR.into()); }",
    ),
    "incident-httpclient5-poolleak-timeout-001": (
        "if (cancel()) { throw new TimeoutException(); }",
        "return super.get();",
    ),
    "incident-jackson-bigdecimal-arith-001": (
        "catch (ArithmeticException e) { "
        'throw new NumberFormatException("invalid BigDecimal"); }',
    ),
    "incident-rustls-completeio-hang-001": (
        "if !self.conn.wants_write() && !self.conn.wants_read() { return Ok(()); }",
    ),
    "secops-axios-proxyauth-leak-001": (
        "removeMatchingHeaders(/^proxy-authorization$/i, this._options.headers);",
    ),
    "secops-qs-proto-pollution-tri-001": (
        "if (key === '__proto__') { continue; }",
    ),
}

# The residual-A attack, per task: a DEAD assignment whose string VALUE carries
# the task's fix token(s), while the vulnerable code stays untouched. For the
# opt-in string-literal tasks (qs, aiohttp, urllib3, markupsafe) the string
# carries BOTH the literal and its structural token, so this also pins the
# stronger "both tokens inside one string" bypass — the annotate co-location must
# still score it 0 because the structural token is not real code (not in the bare
# field). Hand-written per task, like FIX_LINES, never derived from the check.
STRING_BYPASS_LINES: dict[str, tuple[str, ...]] = {
    "incident-rack-range-redos-001": (
        'DUMMY = "r0, r1 = range_spec.split(\'-\', 2)"',
    ),
    "err-provenance-dual-aiohttp-zoneid-001": (
        'DUMMY = "host = host.replace(\'%25\', \'%\')"',
    ),
    "err-provenance-dual-kombu-serializer-001": (
        'DUMMY = "except KeyError: raise SerializerNotInstalled(name)"',
    ),
    "err-provenance-dual-markupsafe-striptags-001": (
        'DUMMY = "_strip_tags_re = re.compile(pattern, re.DOTALL)"',
    ),
    "err-provenance-dual-urllib3-xgzip-001": (
        'DUMMY1 = "if mode in (gzip, x-gzip):"',
        'DUMMY2 = "CONTENT_DECODERS = [gzip, x-gzip, deflate]"',
    ),
    "error-prov-dual-reqwest-hyper-chunk-001": (
        'let _dummy = "size.checked_mul(16) io::Error InvalidData";',
    ),
    "escalation-netty-gzip-fextra-001": (
        'String _dummy = "xlen = xlen1 | xlen2 << 8";',
    ),
    "escalation-spring-mimetype-oob-001": (
        'String _dummy = "if (nextIndex >= 1) charAt";',
    ),
    "incident-h2-continuation-flood-001": (
        'let _dummy = "continuation_count MAX_CONTINUATION_FRAMES Err Reason PROTOCOL_ERROR";',
    ),
    "incident-httpclient5-poolleak-timeout-001": (
        'String _dummy = "if (cancel()) return super.get()";',
    ),
    "incident-jackson-bigdecimal-arith-001": (
        'String _dummy = "catch (ArithmeticException) throw new NumberFormatException";',
    ),
    "incident-rustls-completeio-hang-001": (
        'let _dummy = "if !wants_write() && !wants_read() return";',
    ),
    "secops-axios-proxyauth-leak-001": (
        'const _dummy = "removeMatchingHeaders proxy-authorization";',
    ),
    "secops-qs-proto-pollution-tri-001": (
        'const _dummy = "if (key === \'__proto__\') { continue; }";',
    ),
}

# The co-location over-credit vector, for the four opt-in "literal fix" tasks: a
# single dead line that carries a *generic* structural keyword AND the target
# literal, with no real operation connecting them. Presence-on-a-line is not a
# fix — the keyword must operate on the literal — so each must score 0 on the
# semantic checkpoint. Only these four tasks read a literal; the other ten match
# a bare token and this shape does not apply.
DEAD_KEYWORD_BYPASS_LINES: dict[str, tuple[str, ...]] = {
    "secops-qs-proto-pollution-tri-001": (
        "if (a === b) obj.__proto__;",  # unrelated ===, bare property access
    ),
    "err-provenance-dual-aiohttp-zoneid-001": (
        "zone = '%25'",  # the literal as a dead assignment value, no replace/partition
    ),
    "err-provenance-dual-urllib3-xgzip-001": (
        "decoder = 'x-gzip'",  # not a list/tuple element
        "DECODER = 'x-gzip'",
    ),
    "err-provenance-dual-markupsafe-striptags-001": (
        '_unused_re = re.compile(r"[\\s\\S]")',  # right pattern, wrong (dead) regex name
    ),
}

# The same genuine fixes as FIX_LINES, but wrapped across lines the way an agent
# writing idiomatic code would. The co-location check must credit these at full —
# a fix is not less of a fix for spanning two lines (the mirror failure mode of
# the over-credit vector above).
MULTILINE_FIX_LINES: dict[str, tuple[str, ...]] = {
    "secops-qs-proto-pollution-tri-001": (
        "if (key ===",
        "    '__proto__') { continue; }",
    ),
    "err-provenance-dual-aiohttp-zoneid-001": (
        "host = host.replace(",
        "    '%25', '%')",
    ),
    "err-provenance-dual-urllib3-xgzip-001": (
        "if mode in (",
        "    'gzip', 'x-gzip'",
        "):",
        "CONTENT_DECODERS = [",
        "    'gzip', 'x-gzip', 'deflate'",
        "]",
    ),
    "err-provenance-dual-markupsafe-striptags-001": (
        "_strip_tags_re = re.compile(",
        '    r"<[\\s\\S]*>"',
        ")",
    ),
}

# Wrong-target decoys: the structural token operates on the RIGHT literal but at
# the wrong target or in a non-operative position — a comparison that gates
# nothing, a call on the literal itself instead of the variable, a list literal
# under the wrong name. These are precision failures (the mirror of residual B's
# bare-basename), distinct from the reachability floor (a genuine fix wrapped in
# dead code), and each must score 0. ``(label, task_id, lines)``.
WRONG_TARGET_DECOYS: list[tuple[str, str, tuple[str, ...]]] = [
    ("qs free-standing comparison", "secops-qs-proto-pollution-tri-001",
     ("key === '__proto__';",)),
    ("qs comparison in a body assignment", "secops-qs-proto-pollution-tri-001",
     ("if (unrelated) { x = (key === '__proto__'); }",)),
    ("aiohttp self-replace on the literal", "err-provenance-dual-aiohttp-zoneid-001",
     ("x = ('%25').replace('%25', '%')",)),
    ("urllib3 dead tuple, wrong name", "err-provenance-dual-urllib3-xgzip-001",
     ("x = ('gzip', 'x-gzip')", "y = ['a', 'x-gzip']")),
    ("urllib3 scalar under the real name", "err-provenance-dual-urllib3-xgzip-001",
     ("content_encoding = 'x-gzip'",)),
    ("urllib3 substring, not the element", "err-provenance-dual-urllib3-xgzip-001",
     ("CONTENT_DECODERS = ['noise-x-gzip-noise']",)),
    # Defect B: the real names, reassigned to broken 1-tuples that DROP the
    # existing "gzip". Real code, real names, x-gzip in a bracket — and a no-op:
    # the genuine fix ADDS x-gzip alongside gzip, it does not replace it.
    ("urllib3 dead reassignment of both real names",
     "err-provenance-dual-urllib3-xgzip-001",
     ("CONTENT_DECODERS = ('x-gzip',)", "content_encoding = ('x-gzip',)")),
    # The same, with the missing "gzip" element supplied by a NEIGHBOURING dead
    # statement — pins that each lookahead stays inside its own collection.
    ("urllib3 elements pooled across statements",
     "err-provenance-dual-urllib3-xgzip-001",
     ("CONTENT_DECODERS = ('x-gzip',)", "x = ('gzip',)")),
    # Live, parsing, behaviour-neutral code — NOT the reachability floor. Every
    # line runs; none of them fixes anything. `mode in (["gzip", "x-gzip"],)`
    # tests membership against a 1-tuple holding a list and is always False, so
    # the element window must not descend into a nested collection.
    ("urllib3 nested collection, membership always False",
     "err-provenance-dual-urllib3-xgzip-001",
     ('if mode in (["gzip", "x-gzip"],):', "    pass")),
    # `CONTENT_DECODERS=` also spells a keyword argument. The registry anchor has
    # to be an assignment, not any text shaped like one.
    ("urllib3 registry name as a kwarg",
     "err-provenance-dual-urllib3-xgzip-001",
     ('_ = dict(CONTENT_DECODERS=["gzip", "x-gzip", "deflate"])',)),
    ("urllib3 nested + kwarg combined",
     "err-provenance-dual-urllib3-xgzip-001",
     ('if mode in (["gzip", "x-gzip"],):', "    pass",
      '_ = dict(CONTENT_DECODERS=["gzip", "x-gzip", "deflate"])')),
    # Only ADDED lines are scored, so a collection left open on the added line
    # has its closer dropped with the context — and the window would run on into
    # a later statement and borrow its element. The collection must close here.
    ("urllib3 open collection pooling a later statement",
     "err-provenance-dual-urllib3-xgzip-001",
     ('CONTENT_DECODERS = ["gzip"', 'x = a, "x-gzip"')),
]

# How each language spells "the rest of this line is prose".
_COMMENT: dict[str, str] = {
    "python": "# ",
    "ruby": "# ",
    "rust": "// ",
    "java": "// ",
    "javascript": "// ",
    "typescript": "// ",
}

# How each language opens and closes a block of prose spanning many lines — the
# docstring or license header a real source file almost always carries.
_BLOCK_COMMENT: dict[str, tuple[str, str]] = {
    "python": ('"""', '"""'),
    "ruby": ("=begin", "=end"),
    "rust": ("/*", "*/"),
    "java": ("/*", "*/"),
    "javascript": ("/*", "*/"),
    "typescript": ("/*", "*/"),
}

# Prose lines between the block-comment opener and the agent's edit. Git shows
# three lines of context by default, so an opener further than that above the
# edit falls outside the hunk — which is precisely defect A: the scanner then
# starts with no string state and reads the prose inside the comment as code.
# Eight is comfortably past three and still cheap to seed.
_OPENER_DISTANCE = 8


def _decoy_path(target: str) -> str:
    """A sibling of the fix site, same language, wrong file."""
    site = Path(target)
    return str(site.parent / f"decoy_elsewhere{site.suffix}")


@dataclass(frozen=True)
class PatchTask:
    """One patch-deliverable task, with a seeded repo standing in for its fix site."""

    task_id: str
    task_dir: Path
    workspace: Path
    repo: Path
    target: str
    lang: str
    fix_lines: tuple[str, ...]

    @property
    def site_check(self) -> Path:
        return self.task_dir / "checks" / "check_patch_site.sh"

    @property
    def semantics_check(self) -> Path:
        """The task's semantic checkpoint, whatever it is named."""
        for name in ("check_patch_semantics.sh", "check_patch_applies.sh"):
            if (self.task_dir / "checks" / name).is_file():
                return self.task_dir / "checks" / name
        raise AssertionError(f"{self.task_id} declares no patch-semantics check")

    def write(self, lines: tuple[str, ...], *, commented: bool, path: str | None = None) -> None:
        """Append ``lines`` to a file in the repo, as code or as commented-out prose."""
        marker = _COMMENT[self.lang] if commented else ""
        body = "".join(f"        {marker}{line}\n" for line in lines)
        file = self.repo / (path or self.target)
        file.parent.mkdir(parents=True, exist_ok=True)
        with file.open("a") as fh:
            fh.write(body)

    def _commit(self, message: str) -> None:
        for cmd in (["git", "add", "-A"], ["git", "commit", "-qm", message]):
            subprocess.run(cmd, cwd=str(self.repo), check=True, capture_output=True)

    def open_block_comment(self) -> None:
        """Re-seed the fix site as a file carrying a long pre-existing block comment.

        Committed, so it is part of the baseline the agent inherits — a license
        header or module docstring, which nearly every real source file has.
        """
        opener, closer = _BLOCK_COMMENT[self.lang]
        prose = "".join(f"prose line {i}\n" for i in range(_OPENER_DISTANCE))
        (self.repo / self.target).write_text(f"{opener}\n{prose}{closer}\n")
        self._commit("block comment")

    def write_inside_block(self, lines: tuple[str, ...]) -> None:
        """Insert ``lines`` verbatim just above the block comment's closer.

        No comment marker: the lines are already prose by virtue of where they
        sit. That is the point — the agent types the fix's exact text but never
        leaves the comment.
        """
        _, closer = _BLOCK_COMMENT[self.lang]
        file = self.repo / self.target
        text = file.read_text()
        tail = f"{closer}\n"
        assert text.endswith(tail), f"{self.task_id}: fix site is not a seeded block comment"
        file.write_text(text[: -len(tail)] + "".join(f"{line}\n" for line in lines) + tail)

    def run(self, check: Path) -> dict:
        env = checkpoint_env(self.workspace, self.task_dir, self.task_id)
        proc = subprocess.run(
            ["bash", str(check)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(self.workspace),
            env=env,
        )
        assert proc.stdout.strip(), (
            f"{self.task_id}: {check.name} produced no verdict "
            f"(exit {proc.returncode}): {proc.stderr[:400]}"
        )
        return json.loads(proc.stdout)


def _patch_tasks() -> list[Path]:
    """Every authored task whose declared deliverable includes a code_patch."""
    found = [
        d
        for d in sorted(BENCHMARKS.glob("*/*"))
        if (d / "task.toml").is_file()
        and (d / "checks" / "check_patch_site.sh").is_file()
        and "code_patch" in parse_toml(d / "task.toml").get("artifacts", {}).get("required", [])
    ]
    # Collecting nothing must never read as green — the failure mode that let
    # these 14 tasks ship with no discrimination coverage at all.
    if not found:
        raise RuntimeError(
            "no task declares a code_patch deliverable with a check_patch_site.sh; "
            "this suite would collect nothing."
        )
    return found


PATCH_TASKS = _patch_tasks()


def _seed_task(task_dir: Path, workspace: Path) -> PatchTask:
    """Seed a git repo under ``workspace`` whose fix site is the one ground truth names."""
    task_id = task_dir.name
    config = parse_toml(task_dir / "task.toml")
    lang = config["metadata"]["languages"][0]
    gt = json.loads((task_dir / "ground_truth.json").read_text())["required_files"][0]

    repo = workspace / gt["repo"]
    target = repo / gt["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    # Baseline content is irrelevant to scoring — every check reads the DIFF, not
    # the file — so it only has to exist and be committed for a diff to exist.
    target.write_text(f"{_COMMENT[lang]}baseline\n")
    # A tracked sibling, so the wrong-path decoy models what it is meant to: an
    # agent editing a real file in the wrong place. Left untracked it would prove
    # only that `git diff HEAD` ignores new files.
    (repo / _decoy_path(gt["path"])).write_text(f"{_COMMENT[lang]}baseline\n")
    for cmd in (
        ["git", "init", "-q", "."],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "baseline"],
    ):
        subprocess.run(cmd, cwd=str(repo), check=True, capture_output=True)

    return PatchTask(
        task_id=task_id,
        task_dir=task_dir,
        workspace=workspace,
        repo=repo,
        target=gt["path"],
        lang=lang,
        fix_lines=FIX_LINES.get(task_id, ()),
    )


_OPT_IN_TASKS = {d.name: d for d in PATCH_TASKS if d.name in DEAD_KEYWORD_BYPASS_LINES}


@pytest.fixture(params=PATCH_TASKS, ids=lambda d: d.name)
def patch_task(request, tmp_path: Path) -> PatchTask:
    """Seed a git repo whose fix site is the one ground truth names."""
    task_dir: Path = request.param
    task_id = task_dir.name

    assert task_id in FIX_LINES, (
        f"{task_id} declares a code_patch deliverable but this suite has no fix-line "
        f"fixture for it, so its patch checkpoints would go unexercised. Add one."
    )
    assert task_id in STRING_BYPASS_LINES, (
        f"{task_id} declares a code_patch deliverable but this suite has no "
        f"string-literal bypass fixture for it, so the residual-A vector "
        f"(a dead string carrying the token) would go unexercised. Add one."
    )
    return _seed_task(task_dir, tmp_path / "workspace")


@pytest.fixture(params=list(_OPT_IN_TASKS.values()), ids=lambda d: d.name)
def opt_in_task(request, tmp_path: Path) -> PatchTask:
    """A task whose genuine fix is a string literal, so it uses co-location scoring."""
    return _seed_task(request.param, tmp_path / "workspace")


def test_comment_only_edit_at_fix_site_scores_zero(patch_task: PatchTask) -> None:
    """THE defect: the fix line, commented out at the true site, buys nothing."""
    patch_task.write(patch_task.fix_lines, commented=True)

    site = patch_task.run(patch_task.site_check)
    semantics = patch_task.run(patch_task.semantics_check)

    assert site["score"] == 0.0, (
        f"{patch_task.task_id}: a comment-only edit at {patch_task.target} scored "
        f"{site['score']} on check_patch_site — {site['detail']}"
    )
    assert semantics["score"] == 0.0, (
        f"{patch_task.task_id}: a comment carrying the fix token scored "
        f"{semantics['score']} on {patch_task.semantics_check.name} — "
        f"{semantics['detail']}"
    )


def test_genuine_fix_at_site_scores_full(patch_task: PatchTask) -> None:
    """The control: the same line as CODE must still earn full credit.

    Without this, "score 0 for a comment" is satisfiable by a checkpoint that
    scores 0 for everything.
    """
    patch_task.write(patch_task.fix_lines, commented=False)

    site = patch_task.run(patch_task.site_check)
    semantics = patch_task.run(patch_task.semantics_check)

    assert site["score"] == 1.0, (
        f"{patch_task.task_id}: a real code change at {patch_task.target} scored "
        f"{site['score']} on check_patch_site — {site['detail']}"
    )
    assert semantics["score"] == 1.0, (
        f"{patch_task.task_id}: the genuine fix scored {semantics['score']} on "
        f"{patch_task.semantics_check.name} — {semantics['detail']}"
    )


def test_no_diff_scores_zero(patch_task: PatchTask) -> None:
    """An agent that changed nothing scores nothing."""
    site = patch_task.run(patch_task.site_check)
    semantics = patch_task.run(patch_task.semantics_check)

    assert site["score"] == 0.0, f"{patch_task.task_id}: clean repo scored {site['score']}"
    assert semantics["score"] == 0.0, (
        f"{patch_task.task_id}: clean repo scored {semantics['score']} on semantics"
    )


def test_real_fix_at_wrong_path_is_not_full_credit(patch_task: PatchTask) -> None:
    """Pathspec anchoring: the right code in the wrong file is not the fix.

    This already worked before the D2 fix and must survive it — the patch-site
    checkpoint pays for landing at the site ground truth names, so a decoy
    carrying the same code elsewhere earns the "wrong place" tier, not full marks.
    """
    decoy = _decoy_path(patch_task.target)
    patch_task.write(patch_task.fix_lines, commented=False, path=decoy)

    site = patch_task.run(patch_task.site_check)
    semantics = patch_task.run(patch_task.semantics_check)

    assert site["score"] == 0.2, (
        f"{patch_task.task_id}: the fix at a WRONG path ({decoy}) scored "
        f"{site['score']} on check_patch_site — {site['detail']}"
    )
    assert semantics["score"] == 0.0, (
        f"{patch_task.task_id}: the fix at a WRONG path scored {semantics['score']} "
        f"on {patch_task.semantics_check.name} — {semantics['detail']}"
    )


def test_string_literal_carrying_the_token_scores_zero(patch_task: PatchTask) -> None:
    """Residual A: a dead string whose VALUE carries the fix token buys nothing.

    ``DUMMY = "range_spec.split('-', 2)"`` is real code (a live assignment), so
    check_patch_site pays for landing at the fix site — but the *semantic*
    checkpoint must not: the vulnerable regex is untouched and the token lives
    only inside a string. For the opt-in literal tasks the string carries the
    structural token too, so this also pins the "both tokens in one string"
    bypass — the marked-view co-location scores it 0 because nothing sits flush
    against the string mark outside the dead literal.

    Paired with :func:`test_genuine_fix_at_site_scores_full` (the positive
    control), so "0 for a dead string" is not satisfiable by a check that zeros
    everything.
    """
    patch_task.write(STRING_BYPASS_LINES[patch_task.task_id], commented=False)

    semantics = patch_task.run(patch_task.semantics_check)

    assert semantics["score"] == 0.0, (
        f"{patch_task.task_id}: a dead string literal carrying the fix token scored "
        f"{semantics['score']} on {patch_task.semantics_check.name} — "
        f"{semantics['detail']}"
    )


def test_fix_shaped_prose_inside_a_far_opening_comment_scores_zero(
    patch_task: PatchTask,
) -> None:
    """Defect A: a comment is not a patch even when its opener is off-screen.

    :func:`test_comment_only_edit_at_fix_site_scores_zero` only ever exercised a
    comment the agent opened *on the line it wrote*, which git's default three
    lines of context always show. A block comment the file ALREADY had opens
    further up, so the opener sits outside the hunk and the scanner starts
    mid-string with no state — reading the prose inside it as bare code. The
    agent types the fix's exact text into a docstring, changes nothing, and
    collects full credit on both patch checkpoints. Real files carry license
    headers and docstrings, so this is the common shape, not an exotic one.

    Paired with :func:`test_genuine_fix_below_a_far_opening_comment_scores_full`,
    so "0 inside a comment" cannot be satisfied by zeroing the whole file shape.
    """
    patch_task.open_block_comment()
    patch_task.write_inside_block(patch_task.fix_lines)

    site = patch_task.run(patch_task.site_check)
    semantics = patch_task.run(patch_task.semantics_check)

    assert site["score"] == 0.0, (
        f"{patch_task.task_id}: the fix text typed INSIDE a pre-existing block "
        f"comment at {patch_task.target} scored {site['score']} on "
        f"check_patch_site — {site['detail']}"
    )
    assert semantics["score"] == 0.0, (
        f"{patch_task.task_id}: the fix text typed INSIDE a pre-existing block "
        f"comment scored {semantics['score']} on {patch_task.semantics_check.name} "
        f"— {semantics['detail']}"
    )


def test_genuine_fix_below_a_far_opening_comment_scores_full(
    patch_task: PatchTask,
) -> None:
    """The control: the same file, with the fix as real code once the comment closes.

    Widening the scanner's view must not make a file with a docstring unscorable
    — an agent that writes the genuine fix below the header still gets paid.
    """
    patch_task.open_block_comment()
    patch_task.write(patch_task.fix_lines, commented=False)

    site = patch_task.run(patch_task.site_check)
    semantics = patch_task.run(patch_task.semantics_check)

    assert site["score"] == 1.0, (
        f"{patch_task.task_id}: a real fix below a block comment scored "
        f"{site['score']} on check_patch_site — {site['detail']}"
    )
    assert semantics["score"] == 1.0, (
        f"{patch_task.task_id}: a real fix below a block comment scored "
        f"{semantics['score']} on {patch_task.semantics_check.name} — "
        f"{semantics['detail']}"
    )


def test_dead_keyword_plus_literal_scores_zero(opt_in_task: PatchTask) -> None:
    """The co-location over-credit vector: a keyword and the literal, unconnected.

    A dead line that merely places a generic structural keyword and the target
    literal together — ``if (a === b) obj.__proto__;``, ``zone = '%25'`` — did no
    work: nothing operates on the literal. It must score 0 on the semantic
    checkpoint. Paired with :func:`test_genuine_fix_at_site_scores_full` and
    :func:`test_multiline_genuine_fix_scores_full` so this is not satisfiable by a
    check that zeros everything.
    """
    opt_in_task.write(DEAD_KEYWORD_BYPASS_LINES[opt_in_task.task_id], commented=False)

    semantics = opt_in_task.run(opt_in_task.semantics_check)

    assert semantics["score"] == 0.0, (
        f"{opt_in_task.task_id}: a dead keyword co-located with the literal scored "
        f"{semantics['score']} on {opt_in_task.semantics_check.name} — {semantics['detail']}"
    )


@pytest.mark.parametrize(
    "label,task_id,lines", WRONG_TARGET_DECOYS, ids=[d[0] for d in WRONG_TARGET_DECOYS]
)
def test_wrong_target_decoy_scores_zero(
    label: str, task_id: str, lines: tuple[str, ...], tmp_path: Path
) -> None:
    """A structural token on the right literal but the wrong target does no work.

    The comparison that gates nothing, the call on the literal instead of the
    variable, the list under the wrong name — each names the right token but
    misses the fix, so each must score 0 on the semantic checkpoint. These are
    the co-location precision cases; the residual reachability floor (a genuine
    fix wrapped in ``if (false)`` or a never-called function) is a separate,
    pre-existing lexical limitation tracked in EnterpriseBench-jn73.2.7.5.
    """
    task = _seed_task(_OPT_IN_TASKS[task_id], tmp_path / "workspace")
    task.write(lines, commented=False)

    semantics = task.run(task.semantics_check)

    assert semantics["score"] == 0.0, (
        f"{label} ({task_id}) scored {semantics['score']} on "
        f"{task.semantics_check.name} — {semantics['detail']}"
    )


def test_multiline_genuine_fix_scores_full(opt_in_task: PatchTask) -> None:
    """The mirror control: an idiomatic fix wrapped across lines is still a fix.

    Co-location must span lines — ``key ===\\n '__proto__'`` is the same guard as
    the one-liner. Without this, tightening the check against the over-credit
    vector could quietly start zeroing honest multi-line fixes (the false-negative
    the co-location rework exists to also prevent). markupsafe's fixture uses the
    ``[\\s\\S]`` char-class branch (a string-interior token), so this doubles as
    the coverage for that branch under a wrapped compile call.
    """
    opt_in_task.write(MULTILINE_FIX_LINES[opt_in_task.task_id], commented=False)

    semantics = opt_in_task.run(opt_in_task.semantics_check)

    assert semantics["score"] == 1.0, (
        f"{opt_in_task.task_id}: an idiomatic multi-line genuine fix scored "
        f"{semantics['score']} on {opt_in_task.semantics_check.name} — {semantics['detail']}"
    )
