#!/usr/bin/env python3
"""Verify Sourcegraph indexing status for EnterpriseBench repos.

Reads configs/sg_indexing_list.json and outputs a summary of indexing
status across all suites and repos.

Usage:
    python scripts/infra/verify_sg_indexing.py
    python scripts/infra/verify_sg_indexing.py --check-api
    python scripts/infra/verify_sg_indexing.py --json
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_INDEX_PATH = os.path.join(ROOT, "configs", "sg_indexing_list.json")


@dataclass(frozen=True)
class RepoStatus:
    name: str
    url: str
    indexed: bool


@dataclass(frozen=True)
class SuiteStatus:
    name: str
    repos: tuple[RepoStatus, ...]
    total: int
    indexed: int
    pending: int


@dataclass(frozen=True)
class IndexingSummary:
    total_repos: int
    indexed_count: int
    pending_count: int
    suites: tuple[SuiteStatus, ...]


def load_index(path: str) -> dict[str, Any]:
    """Load the indexing list JSON file."""
    with open(path) as f:
        return json.load(f)


def compute_summary(data: dict[str, Any]) -> IndexingSummary:
    """Compute indexing summary from the loaded JSON data."""
    # Count from flat repos array
    all_repos = data.get("repos", [])
    total = len(all_repos)
    indexed = sum(1 for r in all_repos if r.get("_indexed", False))
    pending = total - indexed

    # Per-suite breakdown
    suites_data = data.get("suites", {})
    suite_statuses: list[SuiteStatus] = []

    for suite_name in sorted(suites_data.keys()):
        suite = suites_data[suite_name]
        suite_repos_raw = suite.get("repos", [])
        repo_statuses = tuple(
            RepoStatus(
                name=r["name"],
                url=r["url"],
                indexed=r.get("_indexed", False),
            )
            for r in suite_repos_raw
        )
        suite_total = len(repo_statuses)
        suite_indexed = sum(1 for r in repo_statuses if r.indexed)
        suite_statuses.append(
            SuiteStatus(
                name=suite_name,
                repos=repo_statuses,
                total=suite_total,
                indexed=suite_indexed,
                pending=suite_total - suite_indexed,
            )
        )

    return IndexingSummary(
        total_repos=total,
        indexed_count=indexed,
        pending_count=pending,
        suites=tuple(suite_statuses),
    )


def format_summary(summary: IndexingSummary) -> str:
    """Format the summary as human-readable text."""
    lines = [
        "=== Sourcegraph Mirror Inventory ===",
        "",
        f"Total repos:   {summary.total_repos}",
        "",
        "`_indexed` is a PLACEHOLDER written as a literal False by "
        "generate_sg_index.py.",
        "It is not a verified signal — a 0 here means 'never checked', NOT "
        "'not indexed'.",
        "Run --check-api for real precise-index status.",
        f"  _indexed=true:  {summary.indexed_count}",
        f"  _indexed=false: {summary.pending_count}",
        "",
        "--- Per-Suite Breakdown (_indexed placeholder) ---",
    ]
    lines.extend(
        f"  {s.name}: {s.indexed}/{s.total} indexed, {s.pending} pending"
        for s in summary.suites
    )
    lines.append("")
    return "\n".join(lines)


def format_json(summary: IndexingSummary) -> str:
    """Format the summary as JSON."""
    result = {
        "total_repos": summary.total_repos,
        "indexed_count": summary.indexed_count,
        "pending_count": summary.pending_count,
        "suites": {
            s.name: {
                "total": s.total,
                "indexed": s.indexed,
                "pending": s.pending,
                "repos": [{"name": r.name, "indexed": r.indexed} for r in s.repos],
            }
            for s in summary.suites
        },
    }
    return json.dumps(result, indent=2)


PRECISE = "PRECISE"
NONE = "NONE"
ABSENT = "ABSENT"
UNKNOWN = "UNKNOWN"
STATUSES = (PRECISE, NONE, ABSENT, UNKNOWN)

DEFAULT_ENDPOINT = "https://demo.sourcegraph.com"
# sourcegraph.com rejects the stock Python-urllib User-Agent with a 403.
USER_AGENT = "EnterpriseBench-verify-sg-indexing/1.0"
REQUEST_TIMEOUT = 20
# Each repo is an independent round-trip. Run serially, a dead token (every
# request burning REQUEST_TIMEOUT) takes 133 * 20s ≈ 45 minutes to report
# UNKNOWN for all of them.
MAX_CONCURRENT_CHECKS = 8

INDEX_QUERY = """
query RepoPreciseIndex($name: String!) {
  repository(name: $name) {
    name
    lsifUploads(state: COMPLETED, first: 1) {
      totalCount
    }
  }
}
"""


@dataclass(frozen=True)
class RepoIndexStatus:
    """Precise-index status of one mirror on a Sourcegraph instance.

    UNKNOWN is a first-class outcome, not an error to be smoothed away. Auth
    failure, transport failure and schema drift all mean "we could not tell" —
    coercing any of them to NONE would manufacture a "not indexed" finding out
    of a broken request.
    """

    name: str
    status: str
    detail: str = ""


@dataclass(frozen=True)
class IndexReport:
    results: tuple[RepoIndexStatus, ...]

    @property
    def counts(self) -> dict[str, int]:
        """One entry per status, including the zeroes — a status missing from
        the table would read as 'not applicable' rather than 'none of these'."""
        tally = Counter(r.status for r in self.results)
        return {s: tally[s] for s in STATUSES}

    @property
    def conclusive(self) -> bool:
        """False if any repo came back UNKNOWN — the report cannot support a
        claim about per-repo indexing until every repo resolved.

        An empty result set is also inconclusive, not vacuously true: checking
        zero repos (a schema drift renaming `sg_name`, say) would otherwise exit
        0 and read exactly like 'every repo confirmed'. Reporting a false GREEN
        is the failure this module exists to refuse.
        """
        if not self.results:
            return False
        return not any(r.status == UNKNOWN for r in self.results)


def _http_post(url: str, body: bytes, headers: dict[str, str], timeout: int) -> bytes:
    """Real transport. Injected as `fetch` so tests never touch the network."""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def mirror_names(data: dict[str, Any]) -> list[str]:
    """Mirror names to query, from the flat `repos` array.

    Keyed on `sg_name` deliberately. The per-suite view carries only *upstream*
    names (`ansible/ansible`), and querying those would ask the instance about
    the upstream repo — which is indexed on any public instance — and report a
    false GREEN for a mirror that may not exist at all.
    """
    return [r["sg_name"] for r in data.get("repos", []) if r.get("sg_name")]


def check_repo_index(
    name: str,
    endpoint: str = DEFAULT_ENDPOINT,
    token: str | None = None,
    fetch=_http_post,
) -> RepoIndexStatus:
    """Query one repo's precise-index status."""
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"token {token}"

    body = json.dumps({"query": INDEX_QUERY, "variables": {"name": name}}).encode()
    url = f"{endpoint.rstrip('/')}/.api/graphql"

    try:
        raw = fetch(url, body, headers, REQUEST_TIMEOUT)
    except urllib.error.HTTPError as exc:
        return RepoIndexStatus(name, UNKNOWN, f"HTTP {exc.code}: {exc.reason}")
    except urllib.error.URLError as exc:
        return RepoIndexStatus(name, UNKNOWN, f"transport: {exc.reason}")
    except OSError as exc:  # socket timeouts and friends
        return RepoIndexStatus(name, UNKNOWN, f"transport: {exc}")

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return RepoIndexStatus(name, UNKNOWN, "malformed response body")

    # Every shape check below funnels to UNKNOWN. ABSENT and NONE are definite
    # findings about the corpus; neither may be manufactured from a response we
    # could not read. Only an explicit `repository: null` — the instance
    # answering "no such repo" — is allowed to mean ABSENT.
    if not isinstance(payload, dict):
        return RepoIndexStatus(name, UNKNOWN, "response is not a JSON object")

    if payload.get("errors"):
        messages = "; ".join(
            e.get("message", "?") for e in payload["errors"] if isinstance(e, dict)
        )
        return RepoIndexStatus(name, UNKNOWN, f"graphql: {messages}")

    data = payload.get("data")
    if not isinstance(data, dict):
        return RepoIndexStatus(name, UNKNOWN, "no `data` object in response")

    if "repository" not in data:
        return RepoIndexStatus(name, UNKNOWN, "no `repository` field in response")

    repo = data["repository"]
    if repo is None:
        return RepoIndexStatus(name, ABSENT, "repository not found on instance")
    if not isinstance(repo, dict):
        return RepoIndexStatus(name, UNKNOWN, "`repository` is not an object")

    uploads_field = repo.get("lsifUploads")
    if not isinstance(uploads_field, dict):
        return RepoIndexStatus(name, UNKNOWN, "no `lsifUploads` object in response")

    uploads = uploads_field.get("totalCount")
    # `bool` is an `int` subclass, and a JSON `true` would sail through as
    # "1 completed upload(s)" — a false GREEN, the one thing this module refuses.
    if not isinstance(uploads, int) or isinstance(uploads, bool):
        return RepoIndexStatus(name, UNKNOWN, "no totalCount in response")

    if uploads > 0:
        return RepoIndexStatus(name, PRECISE, f"{uploads} completed upload(s)")
    return RepoIndexStatus(name, NONE, "no completed precise-index uploads")


def _check_one(
    name: str, endpoint: str, token: str | None, fetch
) -> RepoIndexStatus:
    """Per-mirror containment. Not a swallow: the failure is *promoted* into the
    report as UNKNOWN with its cause attached, which makes the report
    inconclusive and exits non-zero. Unguarded, one bad reply raises out of
    `pool.map` and every other mirror's status dies with it.
    """
    try:
        return check_repo_index(name, endpoint=endpoint, token=token, fetch=fetch)
    except Exception as exc:  # noqa: BLE001 — containment is the point
        return RepoIndexStatus(
            name, UNKNOWN, f"check failed: {type(exc).__name__}: {exc}"
        )


def check_all(
    data: dict[str, Any],
    endpoint: str = DEFAULT_ENDPOINT,
    token: str | None = None,
    fetch=_http_post,
) -> IndexReport:
    """Check every mirror. `_indexed` in the config is ignored — it is a
    hardcoded placeholder written by generate_sg_index.py, so reading it back as
    evidence would be circular.

    Checks run concurrently: `check_repo_index` holds no shared state, and
    `pool.map` yields in input order, so the report is identical to a serial run.
    """
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_CHECKS) as pool:
        results = pool.map(
            lambda name: _check_one(name, endpoint, token, fetch),
            mirror_names(data),
        )
        return IndexReport(results=tuple(results))


def format_index_report(report: IndexReport) -> str:
    counts = report.counts
    lines = ["", "=== Precise-Index Status (live) ===", ""]
    lines.extend(f"{status:9} {counts[status]}" for status in STATUSES)
    lines.append("")
    lines.extend(f"  {r.status:9} {r.name}  {r.detail}" for r in report.results)
    lines.append("")

    if not report.results:
        lines.append(
            "INCONCLUSIVE: zero repos were checked. This is not a clean result — "
            "the mirror list resolved to nothing (schema drift?)."
        )
        lines.append("")
    elif not report.conclusive:
        lines.append(
            "INCONCLUSIVE: some repos returned UNKNOWN (auth/transport/schema). "
            "UNKNOWN is not NONE — do not read this as 'not indexed'."
        )
        lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        description="Verify Sourcegraph indexing status for EnterpriseBench repos.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/infra/verify_sg_indexing.py\n"
            "  python scripts/infra/verify_sg_indexing.py --json\n"
            "  python scripts/infra/verify_sg_indexing.py --check-api\n"
        ),
    )
    parser.add_argument(
        "--index-path",
        default=DEFAULT_INDEX_PATH,
        help="Path to sg_indexing_list.json (default: configs/sg_indexing_list.json)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output summary as JSON instead of human-readable text",
    )
    parser.add_argument(
        "--check-api",
        action="store_true",
        help="Query the Sourcegraph instance for each mirror's precise-index status",
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("SOURCEGRAPH_URL", DEFAULT_ENDPOINT),
        help=f"Sourcegraph instance to query (default: {DEFAULT_ENDPOINT})",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("SOURCEGRAPH_ACCESS_TOKEN"),
        help="Access token (default: $SOURCEGRAPH_ACCESS_TOKEN)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not os.path.exists(args.index_path):
        print(f"Error: Index file not found: {args.index_path}", file=sys.stderr)
        return 1

    try:
        data = load_index(args.index_path)
    except json.JSONDecodeError as exc:
        print(f"Error: malformed JSON in {args.index_path}: {exc}", file=sys.stderr)
        return 1

    summary = compute_summary(data)

    if args.output_json:
        print(format_json(summary))
    else:
        print(format_summary(summary))

    if args.check_api:
        if not args.token:
            print(
                "Error: --check-api needs a token (--token or $SOURCEGRAPH_ACCESS_TOKEN). "
                "Without one every repo resolves to UNKNOWN, which must not be "
                "mistaken for 'not indexed'.",
                file=sys.stderr,
            )
            return 2
        report = check_all(data, endpoint=args.endpoint, token=args.token)
        print(format_index_report(report))
        if not report.conclusive:
            return 3

    return 0


if __name__ == "__main__":
    sys.exit(main())
