"""A diff driver the agent configures must not be able to forge its own score.

A git repo configures its own code execution: ``.gitattributes`` picks a diff
driver per path, and that driver's ``textconv``/``command`` names a program git
runs while producing the diff. Every repo ``patch_evidence`` diffs is the
agent's, so the agent picks the driver — and a driver that prints a fix the
agent never wrote makes ``git diff`` say whatever the agent wants.

That reopens D2 through a different door: the comment-stripping in
:mod:`eb_verify.patch_diff` is honest about the text it is handed, but it is
handed forged text, so ``check_patch_site`` and ``check_patch_semantics`` both
paid 1.0 with the vulnerable code untouched on disk (bead jn73.2.7).

``code_patch`` already refuses this at the call site and says why. These tests
exist because ``patch_evidence`` was a *new* diff call site that did not, and
the regression test guarding the rule could not see it: that test greps
``benchmarks/*/*/checks/*.sh`` for the string ``git diff``, and the new checks
call python instead — so the rule kept passing while its coverage lapsed. The
first test below pins the behaviour; ``test_grading_asset_seal`` pins the rule
for python call sites so the next module cannot forget it either.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

BASELINE = """\
module Rack
  module Utils
    def get_byte_ranges(http_range, size)
      $1.split(/,\\s*/).each do |range_spec|
        return nil unless range_spec =~ /(\\d*)-(\\d*)/
        r0, r1 = $1, $2
      end
    end
  end
end
"""

# What an honest fix would add, and therefore what a forging driver claims.
FORGED = "r0, r1 = range_spec.split('-', 2)\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin"},
    )


@pytest.fixture()
def rigged_repo(tmp_path: Path) -> Path:
    """A repo whose fix site is untouched, rigged to forge a fix in `git diff`.

    Only files the agent legitimately owns are written: its own worktree, its
    own ``.gitattributes``, its own ``.git/config``. Nothing here needs a
    privilege the sandboxed agent lacks.
    """
    repo = tmp_path / "rack"
    (repo / "lib" / "rack").mkdir(parents=True)
    site = repo / "lib" / "rack" / "utils.rb"
    site.write_text(BASELINE)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "baseline")

    # git skips textconv entirely when the two sides are byte-identical, so the
    # agent makes one no-op edit to get the driver invoked. A blank line is
    # enough; the vulnerable regex stays exactly where it was.
    site.write_text(BASELINE + "\n")

    driver = repo / "pwn.sh"
    driver.write_text(
        "#!/bin/sh\n"
        # git hands the committed side to the driver as a temp blob and the
        # working-tree side as the real path: forge only the latter and the
        # diff reads as an addition.
        'case "$1" in\n'
        "  *git-blob-*) cat \"$1\" ;;\n"
        f"  *) printf '%s' \"{FORGED.strip()}\" ; printf '\\n' ;;\n"
        "esac\n"
    )
    driver.chmod(0o755)
    (repo / ".gitattributes").write_text("lib/rack/utils.rb diff=pwn\n")
    (repo / ".git" / "config").open("a").write(
        f'[diff "pwn"]\n\ttextconv = {driver}\n'
    )
    return repo


def _score(repo: Path, *args: str) -> dict:
    proc = subprocess.run(
        ["python3", "-m", "eb_verify.plugins.patch_evidence", *args],
        capture_output=True,
        text=True,
        cwd=repo.parent,
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[2] / "lib"),
             "PATH": "/usr/bin:/bin"},
    )
    assert proc.stdout.strip(), f"scorer emitted nothing; stderr={proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_textconv_driver_cannot_forge_a_code_change(rigged_repo: Path) -> None:
    """The fix site is untouched, so the site checkpoint must not pay for it."""
    verdict = _score(
        rigged_repo, "site", "--repo", str(rigged_repo),
        "--path", "lib/rack/utils.rb", "--lang", "ruby",
    )
    assert verdict["score"] == 0.0, (
        "a textconv driver the agent configured forged a code change at the fix "
        f"site and scored {verdict['score']}: {verdict.get('detail')}"
    )
    assert verdict["passed"] is False


def test_textconv_driver_cannot_forge_semantic_tokens(rigged_repo: Path) -> None:
    """The `code` subcommand feeds a per-task regex; it must not carry forgery."""
    proc = subprocess.run(
        ["python3", "-m", "eb_verify.plugins.patch_evidence", "code",
         "--repo", str(rigged_repo), "--path", "lib/rack/utils.rb",
         "--lang", "ruby", "--added"],
        capture_output=True, text=True, cwd=rigged_repo.parent,
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[2] / "lib"),
             "PATH": "/usr/bin:/bin"},
    )
    assert ".split(" not in proc.stdout, (
        "forged textconv output reached the semantic matcher; a per-task regex "
        "would now credit a fix the agent never wrote"
    )


def test_textconv_driver_cannot_forge_a_colocated_match(rigged_repo: Path) -> None:
    """`colocated` is a NEW diff-reading path — the same place a forgotten flag hides.

    It reuses ``_diff``/``git_diff_argv``, so the forged textconv output must not
    reach the marked view the co-location pattern searches. Pinned so the new mode
    cannot silently reopen the door the ``site``/``code`` tests guard.
    """
    proc = subprocess.run(
        ["python3", "-m", "eb_verify.plugins.patch_evidence", "colocated",
         "--repo", str(rigged_repo), "--path", "lib/rack/utils.rb",
         "--lang", "ruby", "--added", "--pattern", r"\.split\("],
        capture_output=True, text=True, cwd=rigged_repo.parent,
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[2] / "lib"),
             "PATH": "/usr/bin:/bin"},
    )
    assert proc.stdout.strip() == "0", (
        "forged textconv output reached the co-location matcher; a per-task "
        f"pattern would now credit a fix the agent never wrote: {proc.stdout!r}"
    )


def test_external_diff_driver_cannot_forge_a_code_change(rigged_repo: Path) -> None:
    """`diff.external` is the same trick via a different config key.

    Pinned separately because --no-textconv does not cover it: a fix that only
    remembered textconv would leave this door open.
    """
    (rigged_repo / ".gitattributes").unlink()
    driver = rigged_repo / "ext.sh"
    # An external driver replaces diff output wholesale (7 positional args).
    driver.write_text("#!/bin/sh\nprintf '+%s\\n' \"r0, r1 = range_spec.split('-', 2)\"\n")
    driver.chmod(0o755)
    (rigged_repo / ".git" / "config").open("a").write(
        f"[diff]\n\texternal = {driver}\n"
    )
    verdict = _score(
        rigged_repo, "site", "--repo", str(rigged_repo),
        "--path", "lib/rack/utils.rb", "--lang", "ruby",
    )
    assert verdict["score"] == 0.0, (
        f"diff.external forged a code change and scored {verdict['score']}"
    )


def test_code_mode_reports_a_bad_invocation_on_its_own_channel() -> None:
    """`code` prints diff text, so its errors must not print a verdict.

    The caller assigns this subcommand's stdout to a shell variable and greps
    it. A JSON verdict there is not an error report, it is text the per-task
    regex then searches — so a broken invocation must answer on stderr with the
    sentinel and leave stdout empty, the same as any other `code` failure. The
    channel is chosen by the subcommand, which argparse has not parsed yet when
    the invocation is what is broken.
    """
    proc = subprocess.run(
        ["python3", "-m", "eb_verify.plugins.patch_evidence", "code",
         "--lang", "python", "--added"],  # missing --repo/--path
        capture_output=True, text=True,
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[2] / "lib"),
             "PATH": "/usr/bin:/bin"},
    )
    assert proc.stdout == "", (
        f"a bad `code` invocation printed to stdout: {proc.stdout!r} — the "
        "caller would grep this as if it were the agent's diff"
    )
    assert "VERIFIER_INFRA_ERROR" in proc.stderr
    assert proc.returncode != 0


def test_honest_fix_still_scores_full(rigged_repo: Path) -> None:
    """The negative control's mirror: refusing the driver must not refuse real work.

    Same rigged repo — the driver stays configured — but this time the agent
    actually writes the fix. Refusing forged text must not cost an honest agent
    its credit, which is the failure mode a blunt "ignore this repo" fix would
    have.
    """
    site = rigged_repo / "lib" / "rack" / "utils.rb"
    site.write_text(BASELINE.replace(
        "        return nil unless range_spec =~ /(\\d*)-(\\d*)/\n        r0, r1 = $1, $2\n",
        "        r0, r1 = range_spec.split('-', 2)\n",
    ))
    verdict = _score(
        rigged_repo, "site", "--repo", str(rigged_repo),
        "--path", "lib/rack/utils.rb", "--lang", "ruby",
    )
    assert verdict["score"] == 1.0, (
        f"an honest fix scored {verdict['score']}: {verdict.get('detail')}"
    )
