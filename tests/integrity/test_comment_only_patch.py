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
    "err-provenance-dual-urllib3-xgzip-001": (
        'if content_encoding in ("gzip", "x-gzip"):',
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

# How each language spells "the rest of this line is prose".
_COMMENT: dict[str, str] = {
    "python": "# ",
    "ruby": "# ",
    "rust": "// ",
    "java": "// ",
    "javascript": "// ",
    "typescript": "// ",
}


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


@pytest.fixture(params=PATCH_TASKS, ids=lambda d: d.name)
def patch_task(request, tmp_path: Path) -> PatchTask:
    """Seed a git repo whose fix site is the one ground truth names."""
    task_dir: Path = request.param
    task_id = task_dir.name
    config = parse_toml(task_dir / "task.toml")
    lang = config["metadata"]["languages"][0]
    gt = json.loads((task_dir / "ground_truth.json").read_text())["required_files"][0]

    assert task_id in FIX_LINES, (
        f"{task_id} declares a code_patch deliverable but this suite has no fix-line "
        f"fixture for it, so its patch checkpoints would go unexercised. Add one."
    )

    workspace = tmp_path / "workspace"
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
        fix_lines=FIX_LINES[task_id],
    )


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
