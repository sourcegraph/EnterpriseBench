"""Tests for scripts/infra/verify_sg_indexing.py."""

import json
import os
import subprocess
import sys
import urllib.error

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(ROOT, "scripts", "infra", "verify_sg_indexing.py")

# Import the module for unit testing
sys.path.insert(0, os.path.join(ROOT, "scripts", "infra"))
import verify_sg_indexing as vsi  # noqa: E402
from verify_sg_indexing import (  # noqa: E402
    IndexingSummary,
    RepoStatus,
    SuiteStatus,
    compute_summary,
    format_json,
    format_summary,
    load_index,
)


@pytest.fixture
def sample_index() -> dict:
    """Minimal valid index for testing."""
    return {
        "suites": {
            "suite_a": {
                "_status": "pending_verification",
                "_indexed_count": 1,
                "_repo_count": 2,
                "_task_count": 3,
                "repos": [
                    {
                        "name": "org/repo1",
                        "url": "https://github.com/org/repo1",
                        "_indexed": True,
                    },
                    {
                        "name": "org/repo2",
                        "url": "https://github.com/org/repo2",
                        "_indexed": False,
                    },
                ],
            },
            "suite_b": {
                "_status": "pending_verification",
                "_indexed_count": 0,
                "_repo_count": 1,
                "_task_count": 1,
                "repos": [
                    {
                        "name": "org/repo3",
                        "url": "https://github.com/org/repo3",
                        "_indexed": False,
                    },
                ],
            },
        },
        "repos": [
            {
                "sg_name": "sg-evals/org/repo1",
                "github_repo": "org/repo1",
                "_indexed": True,
            },
            {
                "sg_name": "sg-evals/org/repo2",
                "github_repo": "org/repo2",
                "_indexed": False,
            },
            {
                "sg_name": "sg-evals/org/repo3",
                "github_repo": "org/repo3",
                "_indexed": False,
            },
        ],
    }


@pytest.fixture
def sample_index_file(sample_index: dict, tmp_path: os.PathLike) -> str:
    """Write sample index to a temp file and return path."""
    path = os.path.join(str(tmp_path), "test_index.json")
    with open(path, "w") as f:
        json.dump(sample_index, f)
    return path


class TestComputeSummary:
    """Test the compute_summary function."""

    def test_total_repos_count(self, sample_index: dict) -> None:
        summary = compute_summary(sample_index)
        assert summary.total_repos == 3

    def test_indexed_count(self, sample_index: dict) -> None:
        summary = compute_summary(sample_index)
        assert summary.indexed_count == 1

    def test_pending_count(self, sample_index: dict) -> None:
        summary = compute_summary(sample_index)
        assert summary.pending_count == 2

    def test_suite_count(self, sample_index: dict) -> None:
        summary = compute_summary(sample_index)
        assert len(summary.suites) == 2

    def test_suite_a_breakdown(self, sample_index: dict) -> None:
        summary = compute_summary(sample_index)
        suite_a = next(s for s in summary.suites if s.name == "suite_a")
        assert suite_a.total == 2
        assert suite_a.indexed == 1
        assert suite_a.pending == 1

    def test_suite_b_breakdown(self, sample_index: dict) -> None:
        summary = compute_summary(sample_index)
        suite_b = next(s for s in summary.suites if s.name == "suite_b")
        assert suite_b.total == 1
        assert suite_b.indexed == 0
        assert suite_b.pending == 1

    def test_suites_sorted_by_name(self, sample_index: dict) -> None:
        summary = compute_summary(sample_index)
        names = [s.name for s in summary.suites]
        assert names == sorted(names)

    def test_empty_index(self) -> None:
        summary = compute_summary({"repos": [], "suites": {}})
        assert summary.total_repos == 0
        assert summary.indexed_count == 0
        assert summary.pending_count == 0
        assert len(summary.suites) == 0

    def test_all_indexed(self) -> None:
        data = {
            "repos": [
                {"sg_name": "a", "_indexed": True},
                {"sg_name": "b", "_indexed": True},
            ],
            "suites": {},
        }
        summary = compute_summary(data)
        assert summary.indexed_count == 2
        assert summary.pending_count == 0

    def test_suite_without_repos_key(self) -> None:
        """Suite entry missing 'repos' key should default to empty."""
        data = {
            "repos": [{"sg_name": "a", "_indexed": False}],
            "suites": {"empty_suite": {"_status": "pending_verification"}},
        }
        summary = compute_summary(data)
        suite = summary.suites[0]
        assert suite.total == 0
        assert suite.indexed == 0


class TestFormatSummary:
    """Test human-readable output formatting."""

    def test_contains_header(self, sample_index: dict) -> None:
        summary = compute_summary(sample_index)
        output = format_summary(summary)
        assert "Sourcegraph Mirror Inventory" in output

    def test_contains_totals(self, sample_index: dict) -> None:
        summary = compute_summary(sample_index)
        output = format_summary(summary)
        assert "Total repos:   3" in output
        assert "_indexed=true:  1" in output
        assert "_indexed=false: 2" in output

    def test_labels_indexed_flag_as_unverified_placeholder(
        self, sample_index: dict
    ) -> None:
        """A bare 'Indexed: 0' reads as a finding. It is not one — the flag is a
        literal written by the generator, so the output must say so."""
        output = format_summary(compute_summary(sample_index))
        assert "PLACEHOLDER" in output
        assert "--check-api" in output

    def test_contains_suite_breakdown(self, sample_index: dict) -> None:
        summary = compute_summary(sample_index)
        output = format_summary(summary)
        assert "suite_a: 1/2 indexed, 1 pending" in output
        assert "suite_b: 0/1 indexed, 1 pending" in output


class TestFormatJson:
    """Test JSON output formatting."""

    def test_valid_json(self, sample_index: dict) -> None:
        summary = compute_summary(sample_index)
        output = format_json(summary)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_json_structure(self, sample_index: dict) -> None:
        summary = compute_summary(sample_index)
        output = format_json(summary)
        parsed = json.loads(output)
        assert parsed["total_repos"] == 3
        assert parsed["indexed_count"] == 1
        assert parsed["pending_count"] == 2
        assert "suite_a" in parsed["suites"]
        assert "suite_b" in parsed["suites"]

    def test_json_suite_repos(self, sample_index: dict) -> None:
        summary = compute_summary(sample_index)
        output = format_json(summary)
        parsed = json.loads(output)
        suite_a_repos = parsed["suites"]["suite_a"]["repos"]
        assert len(suite_a_repos) == 2
        indexed_names = [r["name"] for r in suite_a_repos if r["indexed"]]
        assert "org/repo1" in indexed_names


class TestLoadIndex:
    """Test file loading."""

    def test_load_valid_file(self, sample_index_file: str) -> None:
        data = load_index(sample_index_file)
        assert "repos" in data
        assert "suites" in data

    def test_load_nonexistent_file(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_index("/nonexistent/path.json")


class TestCLI:
    """Test the script as a CLI tool."""

    def test_runs_with_real_index(self) -> None:
        result = subprocess.run(
            [sys.executable, SCRIPT_PATH],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        assert result.returncode == 0
        assert "Sourcegraph Mirror Inventory" in result.stdout

    def test_json_output(self) -> None:
        result = subprocess.run(
            [sys.executable, SCRIPT_PATH, "--json"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        assert "total_repos" in parsed

    def test_check_api_without_token_refuses_rather_than_reporting_zero(self) -> None:
        """No token means we cannot tell. Exit non-zero instead of emitting a
        table of NONEs that would read as 'nothing is indexed'."""
        env = {k: v for k, v in os.environ.items() if k != "SOURCEGRAPH_ACCESS_TOKEN"}
        result = subprocess.run(
            [sys.executable, SCRIPT_PATH, "--check-api"],
            capture_output=True,
            text=True,
            cwd=ROOT,
            env=env,
        )
        assert result.returncode == 2
        assert "token" in result.stderr.lower()

    def test_custom_index_path(self, sample_index_file: str) -> None:
        result = subprocess.run(
            [sys.executable, SCRIPT_PATH, "--index-path", sample_index_file],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        assert result.returncode == 0

    def test_missing_index_path(self) -> None:
        result = subprocess.run(
            [sys.executable, SCRIPT_PATH, "--index-path", "/nonexistent/path.json"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        assert result.returncode == 1
        assert "not found" in result.stderr.lower()

    def test_malformed_index_json_errors_cleanly(self, tmp_path) -> None:
        """A truncated/corrupt index must exit 1 with a message, not traceback."""
        bad = os.path.join(str(tmp_path), "bad.json")
        with open(bad, "w") as f:
            f.write('{"repos": [')
        result = subprocess.run(
            [sys.executable, SCRIPT_PATH, "--index-path", bad],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        assert result.returncode == 1
        assert "malformed json" in result.stderr.lower()
        assert "Traceback" not in result.stderr

    def test_help_flag(self) -> None:
        result = subprocess.run(
            [sys.executable, SCRIPT_PATH, "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        assert result.returncode == 0
        assert "verify" in result.stdout.lower() or "indexing" in result.stdout.lower()


def _graphql(payload: dict, record_headers: dict | None = None):
    """Build a fetcher returning a canned GraphQL response body.

    Pass `record_headers` to capture the request headers the checker sent.
    """

    def fetch(url, body, headers, timeout):
        if record_headers is not None:
            record_headers.update(headers)
        return json.dumps(payload).encode()

    return fetch


def _raising(exc: Exception):
    """Build a fetcher that fails the way a dead token or dead network does."""

    def fetch(url, body, headers, timeout):
        raise exc

    return fetch


def _http_error(code: int, reason: str) -> Exception:
    return urllib.error.HTTPError(
        "https://demo.sourcegraph.com/.api/graphql", code, reason, {}, None
    )


class TestMirrorNames:
    """The checker must key on the mirror name, not the upstream name."""

    def test_keys_on_sg_name_from_repos_array(self) -> None:
        data = {
            "repos": [
                {"sg_name": "sg-evals/NodeBB--8fd8079a", "github_repo": "NodeBB/NodeBB"}
            ],
            "suites": {
                "customer_escalation": {
                    "repos": [{"name": "ansible/ansible", "url": "http://x"}]
                }
            },
        }
        assert vsi.mirror_names(data) == ["sg-evals/NodeBB--8fd8079a"]

    def test_does_not_fall_back_to_upstream_suite_names(self) -> None:
        """Querying `ansible/ansible` (upstream — certainly indexed on a public
        instance) instead of the sg-evals mirror would report a false GREEN."""
        data = {
            "repos": [],
            "suites": {"s": {"repos": [{"name": "ansible/ansible", "url": "http://x"}]}},
        }
        assert vsi.mirror_names(data) == []


def _malformed(url, body, headers, timeout) -> bytes:
    return b"<html>gateway timeout</html>"


class TestCheckRepoIndex:
    @pytest.mark.parametrize(
        "fetch, expected",
        [
            # A completed upload is the only thing that means PRECISE...
            (_graphql({"data": {"repository": {"lsifUploads": {"totalCount": 3}}}}), vsi.PRECISE),
            # ...zero uploads on a repo that EXISTS is a real negative...
            (_graphql({"data": {"repository": {"lsifUploads": {"totalCount": 0}}}}), vsi.NONE),
            # ...and a repo the instance has never heard of is ABSENT, not NONE.
            (_graphql({"data": {"repository": None}}), vsi.ABSENT),
            # Everything below is a failure to ASK. None of it may read as
            # 'not indexed' — that is the false-negative this module refuses.
            (_raising(_http_error(403, "Forbidden")), vsi.UNKNOWN),
            (_raising(urllib.error.URLError("connection reset")), vsi.UNKNOWN),
            (_malformed, vsi.UNKNOWN),
        ],
    )
    def test_response_maps_to_status(self, fetch, expected) -> None:
        assert vsi.check_repo_index("sg-evals/react--ab18f33d", fetch=fetch).status == expected

    def test_auth_failure_is_unknown_not_none(self) -> None:
        """The live case (the token 401s). The detail must name the cause, or a
        dead token is indistinguishable from a genuinely unindexed corpus."""
        fetch = _raising(_http_error(401, "Unauthorized"))
        r = vsi.check_repo_index("sg-evals/react--ab18f33d", fetch=fetch)
        assert r.status == vsi.UNKNOWN
        assert "401" in r.detail

    def test_graphql_schema_error_is_unknown_not_none(self) -> None:
        """Schema drift (field renamed on a newer SG) must not read as 'no index'."""
        fetch = _graphql({"errors": [{"message": "Cannot query field 'lsifUploads'"}]})
        r = vsi.check_repo_index("sg-evals/react--ab18f33d", fetch=fetch)
        assert r.status == vsi.UNKNOWN
        assert "lsifUploads" in r.detail


def _returns(raw: bytes):
    """Transport returning a fixed raw body — used for bodies that are valid
    JSON but the wrong *shape*, which `_graphql` cannot express."""

    def fetch(url, body, headers, timeout) -> bytes:
        return raw

    return fetch


class TestWrongShapeIsUnknown:
    """Valid JSON of an unexpected shape is still a failure to *ask*.

    `_malformed` only covers a non-JSON body. A response that parses but does
    not match the schema — a proxy error page rendered as JSON, schema drift, a
    truncated envelope — must land in UNKNOWN. ABSENT and NONE are definite
    findings about the corpus and may never be manufactured from a response we
    could not read.
    """

    @pytest.mark.parametrize(
        "raw",
        [
            b'{"data": []}',
            b'{"data": {"repository": "a string"}}',
            b'{"data": {"repository": {"lsifUploads": "a string"}}}',
            b"[]",
            b"null",
            b'"a string"',
        ],
    )
    def test_wrong_shape_never_yields_a_definite_finding(self, raw) -> None:
        status = vsi.check_repo_index("sg-evals/x--1", fetch=_returns(raw)).status
        assert status == vsi.UNKNOWN, f"{raw!r} was read as a definite {status}"


class TestRequestShape:
    def test_sends_non_default_user_agent(self) -> None:
        """sourcegraph.com 403s the default Python-urllib UA."""
        seen: dict = {}
        fetch = _graphql(
            {"data": {"repository": {"lsifUploads": {"totalCount": 1}}}}, seen
        )
        vsi.check_repo_index("sg-evals/react--ab18f33d", fetch=fetch)
        ua = seen.get("User-Agent", "")
        assert ua and "python-urllib" not in ua.lower()

    def test_sends_bearer_token_when_provided(self) -> None:
        seen: dict = {}
        fetch = _graphql({"data": {"repository": None}}, seen)
        vsi.check_repo_index("sg-evals/x--1", token="sgp_abc", fetch=fetch)
        assert seen.get("Authorization") == "token sgp_abc"

    def test_omits_authorization_header_when_no_token(self) -> None:
        seen: dict = {}
        fetch = _graphql({"data": {"repository": None}}, seen)
        vsi.check_repo_index("sg-evals/x--1", token=None, fetch=fetch)
        assert "Authorization" not in seen


class TestCheckAll:
    def test_counts_are_bucketed_by_status(self) -> None:
        data = {
            "repos": [
                {"sg_name": "sg-evals/a--1"},
                {"sg_name": "sg-evals/b--2"},
                {"sg_name": "sg-evals/c--3"},
            ]
        }
        responses = {
            "sg-evals/a--1": {"data": {"repository": {"lsifUploads": {"totalCount": 2}}}},
            "sg-evals/b--2": {"data": {"repository": {"lsifUploads": {"totalCount": 0}}}},
            "sg-evals/c--3": {"data": {"repository": None}},
        }

        def fetch(url, body, headers, timeout):
            name = json.loads(body)["variables"]["name"]
            return json.dumps(responses[name]).encode()

        report = vsi.check_all(data, fetch=fetch)
        assert report.counts[vsi.PRECISE] == 1
        assert report.counts[vsi.NONE] == 1
        assert report.counts[vsi.ABSENT] == 1
        assert report.counts[vsi.UNKNOWN] == 0
        assert report.conclusive is True

    def test_all_unknown_when_instance_unreachable(self) -> None:
        """The real-world case: dead token. Must not read as '0 indexed'."""
        data = {"repos": [{"sg_name": "sg-evals/a--1"}, {"sg_name": "sg-evals/b--2"}]}
        report = vsi.check_all(data, fetch=_raising(_http_error(401, "Unauthorized")))
        assert report.counts[vsi.UNKNOWN] == 2
        assert report.counts[vsi.NONE] == 0

    def test_one_bad_response_does_not_destroy_the_other_statuses(self) -> None:
        """Failure must be contained per mirror. Unguarded, one bad reply raises
        out of `pool.map` and takes every other mirror's status with it — at
        corpus scale, all 133 lost to a single malformed response."""
        data = {"repos": [{"sg_name": f"sg-evals/r{i}--a"} for i in range(5)]}

        def fetch(url, body, headers, timeout):
            name = json.loads(body)["variables"]["name"]
            if name == "sg-evals/r2--a":
                return b"null"  # valid JSON, unusable shape
            return b'{"data": {"repository": {"lsifUploads": {"totalCount": 1}}}}'

        report = vsi.check_all(data, fetch=fetch)
        assert len(report.results) == 5
        assert report.counts[vsi.PRECISE] == 4  # the healthy four survive
        assert report.counts[vsi.UNKNOWN] == 1  # only the poisoned one degrades
        assert report.conclusive is False

    def test_unexpected_exception_on_one_mirror_is_contained(self) -> None:
        """Belt and braces: even a parser bug degrades that one mirror to
        UNKNOWN rather than aborting the report."""
        data = {"repos": [{"sg_name": "sg-evals/a--1"}, {"sg_name": "sg-evals/b--2"}]}

        def fetch(url, body, headers, timeout):
            if json.loads(body)["variables"]["name"] == "sg-evals/b--2":
                raise RuntimeError("boom")
            return b'{"data": {"repository": {"lsifUploads": {"totalCount": 1}}}}'

        report = vsi.check_all(data, fetch=fetch)
        assert report.counts[vsi.PRECISE] == 1
        assert report.counts[vsi.UNKNOWN] == 1
        assert report.conclusive is False

    def test_zero_repos_checked_is_not_conclusive(self) -> None:
        """Checking nothing must not read as 'everything confirmed'.

        `conclusive` is 'no repo came back UNKNOWN'. Over an empty result set
        that is vacuously true, so a schema drift that renames `sg_name` would
        silently yield an empty report, exit 0, and look like a clean pass —
        the false-GREEN this whole module is built to refuse.
        """
        report = vsi.check_all({"repos": []}, fetch=_raising(_http_error(401, "nope")))
        assert report.results == ()
        assert report.conclusive is False

    def test_main_refuses_a_zero_repo_check(self, tmp_path) -> None:
        path = tmp_path / "empty.json"
        path.write_text(json.dumps({"repos": []}))
        code = vsi.main(["--index-path", str(path), "--check-api", "--token", "t"])
        assert code != 0


class TestIndexedLiteralIsNotEvidence:
    def test_placeholder_flag_is_ignored_by_api_checker(self) -> None:
        """`_indexed` is written as a hardcoded False by generate_sg_index.py, so
        reading it back as evidence of 'not indexed' is circular. The API checker
        must ignore it entirely and report what the instance actually says."""
        data = {"repos": [{"sg_name": "sg-evals/a--1", "_indexed": False}]}
        fetch = _graphql({"data": {"repository": {"lsifUploads": {"totalCount": 5}}}})
        report = vsi.check_all(data, fetch=fetch)
        assert report.results[0].status == vsi.PRECISE


class TestDataclassImmutability:
    """Verify dataclasses are frozen."""

    def test_repo_status_frozen(self) -> None:
        r = RepoStatus(name="a", url="b", indexed=True)
        with pytest.raises(AttributeError):
            r.name = "c"  # type: ignore[misc]

    def test_suite_status_frozen(self) -> None:
        s = SuiteStatus(name="a", repos=(), total=0, indexed=0, pending=0)
        with pytest.raises(AttributeError):
            s.name = "c"  # type: ignore[misc]

    def test_indexing_summary_frozen(self) -> None:
        summary = IndexingSummary(
            total_repos=0, indexed_count=0, pending_count=0, suites=()
        )
        with pytest.raises(AttributeError):
            summary.total_repos = 1  # type: ignore[misc]
