"""Tests for the cross-revision mirror contamination auditor.

The auditor answers one question per run: did the agent see, or actively read,
source from a repo outside the run's authorized mirror set?

Two contamination modes are distinguished because they have different severity:

- PIN_VIOLATING — a different revision of the SAME project (a sibling pinned
  mirror, or upstream HEAD). This breaks the revision-pinning guarantee the
  mirrors exist to provide, so it is the acute mode.
- FOREIGN — an unrelated third-party project (global-index bleed). Noisy, but
  it does not violate the revision pin.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "analysis"))

import audit_mirror_contamination as amc  # noqa: E402
from agents.harnesses.claude.mcp.sourcegraph import _build_repo_scope  # noqa: E402

PREAMBLE = """
**IMPORTANT: Local source files are not present in /workspace.**

## Sourcegraph Repository Scoping

These repos are indexed on Sourcegraph under `sg-evals/` mirrors.

- **react** (local: `/workspace/react/`)
  - MCP filter: `repo:^github.com/sg-evals/react--ab18f33d$`
  - Upstream: `facebook/react@ab18f33d46171ed1963ae1ac955c5110bb1eb199`
"""


class TestParseAuthorizedMirrors:
    def test_extracts_mirror_upstream_and_project(self):
        mirrors = amc.parse_preamble_mirrors(PREAMBLE)
        assert len(mirrors) == 1
        m = mirrors[0]
        assert m.mirror == "sg-evals/react--ab18f33d"
        assert m.upstream == "facebook/react"
        assert m.project == "react"

    def test_extracts_multiple_mirrors(self):
        text = PREAMBLE + """
- **grpc-go** (local: `/workspace/grpc-go/`)
  - MCP filter: `repo:^github.com/sg-evals/grpc-go--deadbeef$`
  - Upstream: `grpc/grpc-go@deadbeefcafe`
"""
        mirrors = amc.parse_preamble_mirrors(text)
        assert {m.mirror for m in mirrors} == {
            "sg-evals/react--ab18f33d",
            "sg-evals/grpc-go--deadbeef",
        }

    def test_no_scoping_block_yields_empty(self):
        assert amc.parse_preamble_mirrors("no repos here") == ()

    def test_round_trips_the_preamble_the_harness_actually_builds(self):
        """The parser reads a format `mcp/sourcegraph.py` writes, and nothing but
        this test binds the two. Reformat `_build_repo_scope`'s `MCP filter:` or
        `Upstream:` line and every MCP run parses as having no authorized set —
        i.e. as unscored, so the audit reports a corpus-wide clean bill of health.
        Round-trip the real builder so a format change breaks a test instead.
        """
        preamble = _build_repo_scope(
            [
                {
                    "url": "https://github.com/facebook/react",
                    "rev": "ab18f33d46171ed1963ae1ac955c5110bb1eb199",
                    "path": "react",
                }
            ]
        )

        mirrors = amc.parse_preamble_mirrors(preamble)

        assert [m.mirror for m in mirrors] == ["sg-evals/react--ab18f33d"]
        assert mirrors[0].upstream == "facebook/react"


class TestClassifyRepo:
    @pytest.fixture
    def authorized(self):
        return amc.parse_preamble_mirrors(PREAMBLE)

    def test_authorized_mirror(self, authorized):
        assert (
            amc.classify_repo("github.com/sg-evals/react--ab18f33d", authorized)
            == amc.AUTHORIZED
        )

    def test_sibling_mirror_different_revision_is_pin_violating(self, authorized):
        # Same project, a DIFFERENT pinned revision — this is the acute mode.
        assert (
            amc.classify_repo("github.com/sg-evals/react--56408a5b", authorized)
            == amc.PIN_VIOLATING
        )

    def test_upstream_head_of_authorized_project_is_pin_violating(self, authorized):
        # Upstream HEAD is not the pinned revision either.
        assert (
            amc.classify_repo("github.com/facebook/react", authorized)
            == amc.PIN_VIOLATING
        )

    def test_unrelated_repo_is_foreign(self, authorized):
        assert (
            amc.classify_repo("github.com/torvalds/linux", authorized) == amc.FOREIGN
        )

    def test_basename_collision_under_different_org_is_foreign(self, authorized):
        # `preactjs/react` shares no upstream with `facebook/react`. Matching on
        # bare project basename alone would misfile this as PIN_VIOLATING and
        # inflate the headline; it must be FOREIGN.
        assert (
            amc.classify_repo("github.com/preactjs/react", authorized) == amc.FOREIGN
        )


def _msg(role: str, content) -> dict:
    return {"type": role, "message": {"role": role, "content": content}}


def _tool_use(use_id: str, tool: str, **args) -> dict:
    """An assistant turn issuing one Sourcegraph MCP call."""
    return _msg(
        "assistant",
        [
            {
                "type": "tool_use",
                "id": use_id,
                "name": f"mcp__sourcegraph__{tool}",
                "input": args,
            }
        ],
    )


def _tool_result(use_id: str, text: str) -> dict:
    """The user turn carrying that call's result."""
    return _msg(
        "user",
        [
            {
                "type": "tool_result",
                "tool_use_id": use_id,
                "content": [{"type": "text", "text": text}],
            }
        ],
    )


def _trace(tmp_path: Path, lines: list, rel: str = "agent_trace.jsonl") -> Path:
    """Write a trace at `rel` under tmp_path.

    A `str` line is written verbatim rather than JSON-encoded — that is how the
    truncated-line tests inject a line `json.loads` cannot parse.
    """
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines)
    p.write_text(body + "\n")
    return p


TASK = "dead-code-003"
# derive_mirror_name() turns this into `sg-evals/react--ab18f33d` — the mirror
# PREAMBLE above also names, so a well-pinned task and its preamble agree.
REACT_PIN = {
    "url": "https://github.com/facebook/react",
    "rev": "ab18f33d46171ed1963ae1ac955c5110bb1eb199",
}


def _pin(tmp_path: Path, repos: list[dict] | None = None, task: str = TASK) -> Path:
    """Write the task's config — the pin of record — and return the benchmarks dir.

    The authorized set is resolved from HERE, never from the preamble. The
    preamble is a rendered artifact that is itself mis-pinned for some real tasks
    (ccx-dep-trace-106), so deriving the authorized set from it would let the
    auditor certify the very contamination it exists to catch.
    """
    task_dir = tmp_path / "benchmarks" / "suite" / task
    task_dir.mkdir(parents=True, exist_ok=True)
    blocks = [f'[task]\nid = "{task}"']
    for r in repos if repos is not None else [REACT_PIN]:
        blocks.append(f'[[repos]]\nurl = "{r["url"]}"\nrev = "{r["rev"]}"')
    task_dir.joinpath("task.toml").write_text("\n\n".join(blocks) + "\n")
    return tmp_path / "benchmarks"


def _mirrors(tmp_path: Path) -> Path:
    """An empty per-task mirrors dir, so a fixture never silently resolves
    against the REPO's real `configs/sg_mirrors/<task>.json` — which shares task
    ids with these fixtures and would make a test pass for the wrong reason."""
    d = tmp_path / "configs" / "sg_mirrors"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _audit(
    tmp_path: Path,
    lines: list,
    *,
    task: str = TASK,
    mode: str = "mcp_only",
    repos: list[dict] | None = None,
):
    """Audit one synthetic run whose task config exists on disk."""
    benchmarks = _pin(tmp_path, repos, task)
    root = tmp_path / "runs"
    trace = _trace(root, lines, f"{task}/{mode}/agent_trace.jsonl")
    return amc.audit_run(trace, root, benchmarks, _mirrors(tmp_path))


class TestAuditRun:
    """End-to-end over a fixture trace modelled on dead-code-003/mcp_only.

    Reproduces the real signature: a module-local symbol stays inside the pinned
    mirror, an exported symbol resolves into a sibling mirror, and the agent then
    read_files that sibling — i.e. wrong-revision source entered its context.
    """

    @pytest.fixture
    def audit(self, tmp_path):
        return _audit(
            tmp_path,
            [
                _msg("user", PREAMBLE),
                # A module-local symbol: references stay inside the pinned mirror.
                _tool_use(
                    "t1",
                    "find_references",
                    repo="github.com/sg-evals/react--ab18f33d",
                    symbol="retryErrors",
                ),
                _tool_result(
                    "t1", "# github.com/sg-evals/react--ab18f33d - Program.ts"
                ),
                # An exported symbol: references resolve into a sibling mirror.
                _tool_use(
                    "t2",
                    "find_references",
                    repo="github.com/sg-evals/react--ab18f33d",
                    symbol="transformProgram",
                ),
                _tool_result(
                    "t2",
                    "# github.com/sg-evals/react--56408a5b - Pipeline.ts\n"
                    "# github.com/sg-evals/react--ab18f33d - Pipeline.ts",
                ),
                # The agent follows the leaked reference into the WRONG revision.
                _tool_use(
                    "t3",
                    "read_file",
                    repo="github.com/sg-evals/react--56408a5b",
                    path="Pipeline.ts",
                ),
            ],
        )

    def test_authorized_set_comes_from_the_task_config(self, audit):
        assert [m.mirror for m in audit.authorized] == ["sg-evals/react--ab18f33d"]

    def test_local_symbol_reference_does_not_leak(self, audit):
        local = next(c for c in audit.calls if c.args.get("symbol") == "retryErrors")
        assert local.leaked_repos == ()

    def test_exported_symbol_reference_leaks_sibling_mirror(self, audit):
        exported = next(
            c for c in audit.calls if c.args.get("symbol") == "transformProgram"
        )
        assert "sg-evals/react--56408a5b" in exported.leaked_repos

    def test_pin_violating_repo_appears_in_results(self, audit):
        assert "sg-evals/react--56408a5b" in audit.pin_violating_cited

    def test_actively_read_wrong_revision_is_flagged(self, audit):
        """The acute finding: wrong-rev source actually entered the context."""
        assert "sg-evals/react--56408a5b" in audit.pin_violating_called
        assert audit.actively_read_wrong_revision is True

    def test_clean_run_reports_no_contamination(self, tmp_path):
        audit = _audit(
            tmp_path,
            [
                _msg("user", PREAMBLE),
                _tool_use(
                    "t1",
                    "read_file",
                    repo="github.com/sg-evals/react--ab18f33d",
                    path="a.ts",
                ),
            ],
        )
        assert audit.pin_violating_cited == ()
        assert audit.pin_violating_called == ()
        assert audit.actively_read_wrong_revision is False
        assert audit.trustworthy is True

    def test_run_without_a_preamble_is_not_scored(self, tmp_path):
        """A baseline run has no MCP preamble and only /workspace at the pin, so
        it cannot leak. Counting it as 'clean' would dilute the mcp_only rate
        with runs that were never at risk."""
        audit = _audit(
            tmp_path,
            [_msg("user", "Solve the task using files in /workspace.")],
            mode="baseline",
        )
        assert audit.has_mcp is False
        assert audit.scored is False

    def test_mcp_run_with_no_task_config_is_unresolved_not_clean(self, tmp_path):
        """The pin cannot be established, so there is nothing to judge against.
        Such a run must be excluded loudly, never counted as a clean MCP run."""
        root = tmp_path / "runs"
        trace = _trace(
            root, [_msg("user", PREAMBLE)], "ghost-task/mcp_only/agent_trace.jsonl"
        )
        audit = amc.audit_run(
            trace, root, _pin(tmp_path), _mirrors(tmp_path)
        )  # config for TASK only

        assert audit.has_mcp is True
        assert audit.authorized == ()
        assert audit.config_missing is True
        assert audit.scored is False
        assert audit.trustworthy is False


class TestMispinnedPreamble:
    """The defect that made the preamble-derived audit circular.

    Some tasks render an instruction naming a mirror the task never pinned
    (ccx-dep-trace-106 pins `releases/gcc-14.2.0` but instructs the agent to
    filter on `gcc--96dfb333`). If the authorized set is read off that
    instruction, the agent obeying it scores AUTHORIZED and the auditor
    certifies the contamination — a leak is laundered into a clean verdict
    exactly when the preamble is the thing at fault.
    """

    MISPINNED = PREAMBLE.replace("react--ab18f33d", "react--96dfb333")

    def test_leak_the_preamble_authorized_is_still_a_pin_violation(self, tmp_path):
        audit = _audit(
            tmp_path,
            [
                _msg("user", self.MISPINNED),
                _tool_use(
                    "t1",
                    "read_file",
                    repo="github.com/sg-evals/react--96dfb333",
                    path="Pipeline.ts",
                ),
                _tool_result("t1", "# github.com/sg-evals/react--96dfb333 - Pipeline.ts"),
            ],
        )

        # The task pins ab18f33d. The agent was TOLD 96dfb333 and obeyed.
        assert [m.mirror for m in audit.authorized] == ["sg-evals/react--ab18f33d"]
        assert "sg-evals/react--96dfb333" in audit.pin_violating_cited
        assert audit.actively_read_wrong_revision is True

    def test_the_mispin_itself_is_reported(self, tmp_path):
        audit = _audit(tmp_path, [_msg("user", self.MISPINNED)])
        assert audit.mispinned == ("sg-evals/react--96dfb333",)

    def test_a_correctly_pinned_preamble_reports_no_mispin(self, tmp_path):
        audit = _audit(tmp_path, [_msg("user", PREAMBLE)])
        assert audit.mispinned == ()

    def test_head_is_not_a_pin(self, tmp_path):
        """A stub config (`unknown/repo` @ `HEAD`) pins nothing. Deriving
        `sg-evals/repo--HEAD` from it would score every real mirror the agent
        touched as a violation — findings manufactured from a placeholder."""
        audit = _audit(
            tmp_path,
            [
                _msg("user", PREAMBLE),
                _tool_use("t1", "read_file", repo="github.com/sg-evals/react--ab18f33d"),
                _tool_result("t1", "# github.com/sg-evals/react--ab18f33d - a.ts"),
            ],
            repos=[{"url": "https://github.com/unknown/repo", "rev": "HEAD"}],
        )
        assert audit.authorized == ()
        assert audit.config_missing is True
        assert audit.scored is False
        assert audit.pin_violating_cited == ()

    def test_report_names_the_mispinned_runs(self, tmp_path):
        _audit(tmp_path, [_msg("user", self.MISPINNED)])
        audits = amc.audit_corpus(
            tmp_path / "runs", tmp_path / "benchmarks", _mirrors(tmp_path)
        )
        report = amc.format_report(audits)
        assert "MIS-PINNED PREAMBLE" in report
        assert "sg-evals/react--96dfb333" in report


class TestExposureSurface:
    """The exposure table is the denominator behind the audit. It used to be
    counted by hand, so the doc's "how to regenerate every number here" was not
    satisfiable and the table could drift from the corpus silently.
    """

    REGISTRY = {
        "repos": [
            {"sg_name": "sg-evals/grpc-go--v1.4.0"},
            {"sg_name": "sg-evals/grpc-go--v1.5.0"},
            {"sg_name": "sg-evals/grpc-go--v1.6.0"},
            {"sg_name": "sg-evals/etcd--v3.3.0"},
            {"sg_name": "sg-evals/etcd--v3.4.0"},
            {"sg_name": "sg-evals/react--ab18f33d"},  # single revision: not exposed
        ]
    }

    def _registry(self, tmp_path: Path) -> Path:
        p = tmp_path / "sg_indexing_list.json"
        p.write_text(json.dumps(self.REGISTRY))
        return p

    def test_counts_only_multi_revision_projects_as_exposed(self, tmp_path):
        exp = amc.exposure(self._registry(tmp_path))
        assert exp.mirrors == 6
        assert exp.projects == 3
        assert exp.multi_rev_projects == 2  # grpc-go, etcd — react is pinned once
        assert exp.exposed_mirrors == 5  # the single-revision react is NOT at risk
        assert round(exp.exposed_pct) == 83
        assert exp.worst[0] == ("grpc-go", 3)

    def test_missing_registry_is_none_not_a_zeroed_table(self, tmp_path):
        """A zeroed table would read as 'no exposure' — a false all-clear."""
        assert amc.exposure(tmp_path / "nope.json") is None


class TestProvenanceNotMereMention:
    """A repo counts as *cited* only when the result says the content CAME FROM
    it. Source files are full of github.com URLs pointing at their own upstream;
    matching those would manufacture leaks out of ordinary file content.
    """

    def _audit_with_result(self, tmp_path, text):
        return _audit(
            tmp_path,
            [
                _msg("user", PREAMBLE),
                _tool_use(
                    "t1",
                    "read_file",
                    repo="github.com/sg-evals/react--ab18f33d",
                    path="CHANGELOG.md",
                ),
                _tool_result("t1", text),
            ],
        )

    def test_upstream_url_in_file_content_is_not_a_leak(self, tmp_path):
        """Regression: a changelog line linking to the project's own GitHub issue
        tracker was being scored as wrong-revision contamination."""
        audit = self._audit_with_result(
            tmp_path,
            "# github.com/sg-evals/react--ab18f33d – CHANGELOG.md\n"
            "12: handle vault password file (https://github.com/facebook/react/issues/42960).",
        )
        assert audit.pin_violating_cited == ()
        assert audit.foreign_cited == ()

    def test_json_envelope_result_is_decoded(self, tmp_path):
        """Real traces wrap the result in a JSON envelope carried as a plain
        string, so its newlines arrive escaped. Without decoding, the
        line-anchored provenance markers never match and every leak is missed."""
        envelope = json.dumps(
            {
                "text": "# github.com/sg-evals/react--56408a5b – Pipeline.ts\n"
                "18: * see [#32742](https://github.com/facebook/react/pull/32742)"
            }
        )
        audit = self._audit_with_result(tmp_path, envelope)
        # Header => provenance. The inline pull-request URL => not provenance.
        assert "sg-evals/react--56408a5b" in audit.pin_violating_cited
        assert "facebook/react" not in audit.pin_violating_cited

    def test_result_header_line_is_provenance(self, tmp_path):
        audit = self._audit_with_result(
            tmp_path, "# github.com/sg-evals/react--56408a5b – Pipeline.ts\n1: x"
        )
        assert "sg-evals/react--56408a5b" in audit.pin_violating_cited

    def test_sourcegraph_url_line_is_provenance(self, tmp_path):
        audit = self._audit_with_result(
            tmp_path,
            "URL: https://demo.sourcegraph.com/github.com/sg-evals/react--56408a5b/-/blob/x.ts",
        )
        assert "sg-evals/react--56408a5b" in audit.pin_violating_cited


class TestCalledRepos:
    """`repo` is not the only arg that names a repo the agent reached into:
    `commit_search`/`diff_search` take a plural `repos` list, which some traces
    deliver JSON-encoded as a string. Ignoring those shapes would under-count
    the acute 'agent called into a wrong-revision repo' finding.
    """

    def test_singular_repo_arg(self):
        args = {"repo": "github.com/sg-evals/react--ab18f33d"}
        assert amc._called_repos(args) == ("sg-evals/react--ab18f33d",)

    def test_plural_repos_list(self):
        args = {"repos": ["github.com/sg-evals/a--1", "github.com/sg-evals/b--2"]}
        assert amc._called_repos(args) == ("sg-evals/a--1", "sg-evals/b--2")

    def test_plural_repos_json_encoded_string(self):
        args = {"repos": '["github.com/sg-evals/a--1"]'}
        assert amc._called_repos(args) == ("sg-evals/a--1",)

    def test_call_naming_no_repo(self):
        assert amc._called_repos({"query": "useState"}) == ()

    def test_wrong_revision_in_repos_list_is_a_pin_violating_call(self, tmp_path):
        audit = _audit(
            tmp_path,
            [
                _msg("user", PREAMBLE),
                _tool_use(
                    "t1",
                    "commit_search",
                    repos=["github.com/sg-evals/react--56408a5b"],
                    messageTerms=["tarfile"],
                ),
            ],
        )
        assert audit.pin_violating_called == ("sg-evals/react--56408a5b",)


class TestForeignBleed:
    def test_foreign_repo_in_results_is_bleed_not_pin_violation(self, tmp_path):
        audit = _audit(
            tmp_path,
            [
                _msg("user", PREAMBLE),
                _tool_use("t1", "keyword_search", query="useState"),
                _tool_result("t1", "# github.com/vercel/next.js - index.js"),
            ],
        )
        assert "vercel/next.js" in audit.foreign_cited
        assert audit.pin_violating_cited == ()
        assert audit.actively_read_wrong_revision is False


class TestTaskAndMode:
    """Run identity is derived from the trace path, which has three shapes.

    Getting this wrong is not cosmetic: a misparsed mode silently reassigns a
    run to a bucket that does not exist, and the per-mode rollup is the headline
    of the assessment.
    """

    def test_flat_run_has_no_mode(self):
        path = Path("results/runs/cal-drift-flask-config-001/agent_trace.jsonl")
        assert amc._task_and_mode(path) == ("cal-drift-flask-config-001", "unknown")

    def test_task_and_mode_run(self):
        path = Path("results/runs/dead-code-003/mcp_only/agent_trace.jsonl")
        assert amc._task_and_mode(path) == ("dead-code-003", "mcp_only")

    @pytest.mark.parametrize("rep", ["rep1", "rep2", "rep3", "rep10"])
    def test_replicate_dir_is_not_a_mode(self, rep):
        """`<task>/<mode>/repN/` — the replicate is a repeat OF a mode, not a mode."""
        path = Path(f"results/runs/incident-inv-docker-shutdown-004/mcp_only/{rep}/agent_trace.jsonl")
        assert amc._task_and_mode(path) == ("incident-inv-docker-shutdown-004", "mcp_only")

    def test_replicate_under_ablation_mode(self):
        path = Path("results/runs/incident-inv-docker-shutdown-004/ablate-containerd/rep1/agent_trace.jsonl")
        assert amc._task_and_mode(path) == (
            "incident-inv-docker-shutdown-004",
            "ablate-containerd",
        )

    def test_repo_like_dir_that_merely_starts_with_rep_is_still_a_mode(self):
        """Guard the regex: `replay` is a mode, not a replicate."""
        path = Path("results/runs/some-task/replay/agent_trace.jsonl")
        assert amc._task_and_mode(path) == ("some-task", "replay")


class TestAmbiguousAuthorizedSet:
    """A task that pins the SAME project at two revisions has no revision pin,
    so a leak into the second mirror would score AUTHORIZED.

    The tool has no grounds for a clean verdict there: the result is unsound
    rather than merely negative, and must not pass as clean.
    """

    AMBIGUOUS = """
- **ansible**
  - MCP filter: `repo:^github.com/sg-evals/ansible--379058e1$`
  - Upstream: `ansible/ansible@379058e1`
- **ansible**
  - MCP filter: `repo:^github.com/sg-evals/ansible--v2.16.0$`
  - Upstream: `ansible/ansible@v2.16.0`
"""

    TWO_REVS_OF_ONE_PROJECT = [
        {"url": "https://github.com/facebook/react", "rev": "ab18f33d4617aaaa"},
        {"url": "https://github.com/facebook/react", "rev": "56408a5bcafebabe"},
    ]

    def test_two_mirrors_of_one_project_are_flagged_ambiguous(self):
        mirrors = amc.parse_preamble_mirrors(self.AMBIGUOUS)
        assert amc.ambiguous_projects(mirrors) == ("ansible",)

    def test_single_mirror_per_project_is_not_ambiguous(self):
        mirrors = amc.parse_preamble_mirrors(PREAMBLE)
        assert amc.ambiguous_projects(mirrors) == ()

    def test_a_task_pinning_one_project_twice_is_ambiguous(self, tmp_path):
        audit = _audit(
            tmp_path, [_msg("user", PREAMBLE)], repos=self.TWO_REVS_OF_ONE_PROJECT
        )
        assert audit.ambiguous_projects == ("react",)
        assert audit.trustworthy is False

    def test_report_warns_that_an_ambiguous_run_is_not_clean(self, tmp_path):
        _audit(
            tmp_path, [_msg("user", PREAMBLE)], repos=self.TWO_REVS_OF_ONE_PROJECT
        )

        audits = amc.audit_corpus(
            tmp_path / "runs", tmp_path / "benchmarks", _mirrors(tmp_path)
        )

        assert audits[0].ambiguous_projects == ("react",)
        assert "AMBIGUOUS PIN" in amc.format_report(audits)


class TestDiffProvenanceIsNotMissed:
    """compare_revisions / diff_search declare their repo in a third shape:
    `github.com/<repo> <sha>...<sha>` at the head of the diff. Neither the
    `# github.com/` result header nor the `URL:` marker matches it, so a leak
    arriving this way was invisible.
    """

    HEADER = (
        "github.com/sg-evals/ansible--379058e1 e658995760ac1209...fb7dd7f1c321861...\n"
        "+49 -57 | 20 files modified\n"
    )

    def test_diff_header_provenance_is_extracted(self):
        assert amc._cited_repos(self.HEADER) == ("sg-evals/ansible--379058e1",)

    def test_a_bare_github_url_in_file_content_is_still_not_provenance(self):
        """The guard that must not regress: a repo mentioned mid-line inside
        file content is a mention, not provenance."""
        body = "// see https://github.com/facebook/react/issues/1 for context\n"
        assert amc._cited_repos(body) == ()

    def test_a_prose_line_starting_with_github_is_not_a_diff_header(self):
        """The new marker requires the commit-range that only a diff emits."""
        assert amc._cited_repos("github.com/foo/bar is a nice repo\n") == ()


class TestCorpusRootIsNotHardcoded:
    def test_flat_run_under_a_renamed_corpus_root(self, tmp_path):
        """`_task_and_mode` used to hardcode the literal dir name 'runs', so a
        corpus at any other path returned (task, mode) SWAPPED — inventing a
        mode bucket named after the real task. results/runs is gitignored, so
        re-running against an archived copy is the expected case, not exotic."""
        _trace(
            tmp_path,
            [_msg("user", PREAMBLE)],
            rel="prod_runs/cal-drift-flask-config-001/agent_trace.jsonl",
        )

        audits = amc.audit_corpus(tmp_path / "prod_runs")

        assert audits[0].task == "cal-drift-flask-config-001"
        assert audits[0].mode == "unknown"


class TestUpstreamPairing:
    def test_a_mirror_without_an_upstream_does_not_steal_the_next_ones(self):
        """FIFO pairing filed B's upstream under A's record when A had none."""
        preamble = """
- **a**
  - MCP filter: `repo:^github.com/sg-evals/a--1$`
- **b**
  - MCP filter: `repo:^github.com/sg-evals/b--2$`
  - Upstream: `borg/b@2`
"""
        by = {m.mirror: m for m in amc.parse_preamble_mirrors(preamble)}

        assert by["sg-evals/a--1"].upstream is None
        assert by["sg-evals/b--2"].upstream == "borg/b"


class TestNeverSilentlyClean:
    """The audit's one unacceptable failure is reporting a run as clean when it
    could not actually read the evidence. Every path that loses data must say so.
    """

    def test_unparseable_line_is_counted_not_silently_dropped(self, tmp_path):
        p = _trace(
            tmp_path,
            # The second line is a crashed run's truncated last write.
            [_msg("user", PREAMBLE), '{"truncated": mid-writ'],
            rel="t/mcp_only/agent_trace.jsonl",
        )

        audit = amc.audit_run(p)

        assert audit.unparseable_lines == 1

    def test_report_surfaces_unparseable_lines(self, tmp_path):
        _trace(
            tmp_path,
            [_msg("user", PREAMBLE), "{oops"],
            rel="t/mcp_only/agent_trace.jsonl",
        )

        report = amc.format_report(amc.audit_corpus(tmp_path))

        assert "UNPARSEABLE" in report

    def test_clean_trace_reports_no_unparseable_lines(self, tmp_path):
        p = _trace(
            tmp_path, [_msg("user", PREAMBLE)], rel="t/mcp_only/agent_trace.jsonl"
        )

        assert amc.audit_run(p).unparseable_lines == 0
        assert "UNPARSEABLE" not in amc.format_report(amc.audit_corpus(tmp_path))

    def test_deeply_nested_payload_does_not_crash_the_corpus(self):
        """One pathological trace must not take down the whole audit run.

        _text_of recurses through nested containers and re-decodes any string
        that looks like JSON, so depth compounds. A RecursionError escaping to
        audit_corpus would abort every remaining trace with a traceback.
        """
        deep = json.loads(("[" * 2000) + ("]" * 2000))

        assert amc._text_of(deep) == ""  # bounded, not RecursionError


class TestReplicateIdentity:
    """A replicate is rolled up under its mode, but must stay individually
    identifiable in the report.

    The rescore decision this audit feeds (bead .15) is per-RUN, not per-task:
    `config-drift-argocd-redis-ha-004/mcp_only/rep1` can be contaminated while
    `rep3` is clean. Rendering both as the bare label `config-drift-...
    (mcp_only)` produces repeated, apparently-identical lines with different
    counts — indistinguishable to the reader, and easily misread as the tool
    double-counting one run.
    """

    def test_replicate_of_extracts_the_replicate_dir(self):
        path = Path("results/runs/t/mcp_only/rep2/agent_trace.jsonl")
        assert amc._replicate_of(path) == "rep2"

    def test_replicate_of_is_none_for_a_plain_run(self):
        path = Path("results/runs/t/mcp_only/agent_trace.jsonl")
        assert amc._replicate_of(path) is None

    def test_replicate_of_ignores_a_mode_merely_starting_with_rep(self):
        path = Path("results/runs/t/replay/agent_trace.jsonl")
        assert amc._replicate_of(path) is None

    def test_report_distinguishes_two_replicates_of_the_same_task(self, tmp_path):
        """Two contaminated replicates of one task must render as two
        distinguishable lines, not two identical ones."""
        benchmarks = _pin(tmp_path)
        for rep in ("rep1", "rep2"):
            _trace(
                tmp_path / "runs",
                [
                    _msg("user", PREAMBLE),
                    _tool_use("x", "keyword_search", query="q"),
                    _tool_result("x", "# github.com/sg-evals/react--56408a5b - a.ts"),
                ],
                rel=f"{TASK}/mcp_only/{rep}/agent_trace.jsonl",
            )

        report = amc.format_report(
            amc.audit_corpus(tmp_path / "runs", benchmarks, _mirrors(tmp_path))
        )

        assert f"{TASK} (mcp_only/rep1)" in report
        assert f"{TASK} (mcp_only/rep2)" in report

    def test_json_carries_the_replicate(self, tmp_path):
        benchmarks = _pin(tmp_path)
        _trace(
            tmp_path / "runs",
            [_msg("user", PREAMBLE)],
            rel=f"{TASK}/mcp_only/rep3/agent_trace.jsonl",
        )

        payload = json.loads(
            amc.format_json(
                amc.audit_corpus(tmp_path / "runs", benchmarks, _mirrors(tmp_path))
            )
        )

        assert payload["runs"][0]["replicate"] == "rep3"


class TestCorpusDiscovery:
    def test_invalidated_runs_are_excluded(self, tmp_path):
        """`_invalidated/` holds runs already withdrawn from analysis.

        Auditing them would report contamination for runs nobody scores.
        """
        _trace(tmp_path, [_msg("user", PREAMBLE)], rel="good-task/mcp_only/agent_trace.jsonl")
        _trace(
            tmp_path,
            [_msg("user", PREAMBLE)],
            rel="_invalidated/old-task_20260405/rep1/agent_trace.jsonl",
        )

        audits = amc.audit_corpus(tmp_path)

        assert [a.task for a in audits] == ["good-task"]

    def test_replicates_roll_up_into_their_real_mode(self, tmp_path):
        _trace(tmp_path, [_msg("user", PREAMBLE)], rel="t/mcp_only/rep1/agent_trace.jsonl")
        _trace(tmp_path, [_msg("user", PREAMBLE)], rel="t/mcp_only/rep2/agent_trace.jsonl")

        audits = amc.audit_corpus(tmp_path)

        assert {a.mode for a in audits} == {"mcp_only"}
        assert {a.task for a in audits} == {"t"}
