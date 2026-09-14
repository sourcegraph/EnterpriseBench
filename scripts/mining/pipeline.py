#!/usr/bin/env python3
"""
pipeline.py — End-to-end pipeline for mining historical breaking changes
from OSS repos to generate benchmark task candidates.

Stages:
  1. parse_changelogs  — Scan CHANGELOG/release notes for breaking-change entries
  2. link_commits      — Resolve changelog entries to specific PRs/commits
  3. analyze_impact    — Identify cross-repo API surfaces affected
  4. generate_skeleton — Output draft task.toml + instruction.md + metadata.json

Usage:
    # Mine from a GitHub repo (requires gh CLI or GITHUB_TOKEN)
    python pipeline.py --repo https://github.com/grpc/grpc-go \\
        --date-range 2020-01-01 2025-12-31 --output-dir output/

    # Use curated database (no network needed)
    python pipeline.py --curated --output-dir output/

    # Mine from a local clone
    python pipeline.py --local-repo /path/to/repo --output-dir output/

    # Run CRNT validation on generated candidates
    python pipeline.py --curated --output-dir output/ --validate
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import textwrap
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

log = logging.getLogger("mining-pipeline")

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

BREAKING_KEYWORDS = [
    "breaking change",
    "breaking:",
    "breaking-change",
    "incompatible",
    "removed",
    "deprecated",
    "migration required",
    "backward incompatible",
    "backwards incompatible",
    "api change",
    "no longer supported",
]

CHANGELOG_FILENAMES = [
    "CHANGELOG.md",
    "CHANGES.md",
    "HISTORY.md",
    "NEWS.md",
    "RELEASE_NOTES.md",
    "RELEASES.md",
    "changelog.md",
    "CHANGELOG.rst",
    "CHANGES.rst",
    "CHANGELOG",
]


@dataclass(frozen=True)
class ChangelogEntry:
    """A breaking-change entry extracted from a changelog or release."""

    repo_url: str
    version: str
    release_date: Optional[str]
    raw_text: str
    breaking_items: tuple[str, ...]
    source_file: str  # e.g. "CHANGELOG.md" or "GitHub release v1.27.0"


@dataclass(frozen=True)
class LinkedEntry:
    """A changelog entry enriched with PR/commit references."""

    entry: ChangelogEntry
    pr_numbers: tuple[int, ...]
    pr_urls: tuple[str, ...]
    commit_shas: tuple[str, ...]
    changed_files: tuple[str, ...]
    confidence: str  # "high", "medium", "low"


@dataclass(frozen=True)
class ImpactCandidate:
    """A breaking change with identified cross-repo impact."""

    linked: LinkedEntry
    ecosystem: str  # "go", "python", "rust", "typescript"
    upstream_repo: str
    upstream_version: str
    downstream_repo: str
    downstream_affected_version: str
    downstream_fix_version: Optional[str]
    downstream_fix_pr: Optional[str]
    downstream_fix_commit: Optional[str]
    breaking_symbols: tuple[str, ...]
    affected_downstream_files: tuple[str, ...]
    description: str
    confidence: str


@dataclass
class SkeletonOutput:
    """Generated task skeleton for one candidate."""

    candidate_id: str
    output_dir: str
    task_toml: str
    instruction_md: str
    metadata_json: dict
    crnt_passed: Optional[bool] = None


# ---------------------------------------------------------------------------
# Stage 1: Changelog parser
# ---------------------------------------------------------------------------

def _find_changelog_files(repo_path: Path) -> list[Path]:
    """Find changelog files in a local repo clone."""
    found = []
    for name in CHANGELOG_FILENAMES:
        p = repo_path / name
        if p.is_file():
            found.append(p)
    # Also check docs/ subdirectory
    docs_dir = repo_path / "docs"
    if docs_dir.is_dir():
        for name in CHANGELOG_FILENAMES:
            p = docs_dir / name
            if p.is_file():
                found.append(p)
    return found


def _parse_keepachangelog(text: str, repo_url: str, source_file: str,
                          date_start: Optional[date] = None,
                          date_end: Optional[date] = None) -> list[ChangelogEntry]:
    """Parse a Keep a Changelog (https://keepachangelog.com) formatted file.

    Looks for version headers like '## [1.2.0] - 2024-01-15' and extracts
    breaking-change items from sections containing breaking keywords.
    """
    entries: list[ChangelogEntry] = []

    # Match version headers: ## [version] or ## version, optionally with date
    version_pattern = re.compile(
        r"^##\s+\[?([^\]\s]+)\]?\s*(?:-\s*(\d{4}-\d{2}-\d{2}))?",
        re.MULTILINE,
    )

    splits = list(version_pattern.finditer(text))
    for i, match in enumerate(splits):
        version = match.group(1)
        release_date_str = match.group(2)

        # Date filtering
        if release_date_str and (date_start or date_end):
            try:
                rd = date.fromisoformat(release_date_str)
                if date_start and rd < date_start:
                    continue
                if date_end and rd > date_end:
                    continue
            except ValueError:
                pass

        # Extract the section text until the next version header
        start = match.end()
        end = splits[i + 1].start() if i + 1 < len(splits) else len(text)
        section = text[start:end]

        # Find breaking-change items
        section_lower = section.lower()
        has_breaking = any(kw in section_lower for kw in BREAKING_KEYWORDS)
        if not has_breaking:
            continue

        # Extract individual breaking items (lines starting with - or *)
        items = []
        for line in section.splitlines():
            stripped = line.strip()
            if stripped.startswith(("-", "*")) and len(stripped) > 2:
                item_text = stripped[1:].strip()
                item_lower = item_text.lower()
                if any(kw in item_lower for kw in BREAKING_KEYWORDS):
                    items.append(item_text)

        # If no individual items matched, take all bullet items from breaking sections
        if not items:
            in_breaking_section = False
            for line in section.splitlines():
                stripped = line.strip()
                if stripped.lower().startswith(("### breaking", "### removed",
                                                "**breaking", "breaking:")):
                    in_breaking_section = True
                    continue
                if stripped.startswith("###") or stripped.startswith("**"):
                    in_breaking_section = False
                if in_breaking_section and stripped.startswith(("-", "*")):
                    items.append(stripped[1:].strip())

        if items:
            entries.append(ChangelogEntry(
                repo_url=repo_url,
                version=version,
                release_date=release_date_str,
                raw_text=section.strip()[:2000],
                breaking_items=tuple(items),
                source_file=source_file,
            ))

    return entries


def _fetch_github_releases(repo_url: str, date_start: Optional[date] = None,
                           date_end: Optional[date] = None) -> list[ChangelogEntry]:
    """Fetch releases from GitHub API via `gh` CLI."""
    # Extract owner/repo from URL
    m = re.search(r"github\.com/([^/]+/[^/]+?)(?:\.git)?$", repo_url)
    if not m:
        log.warning("Cannot extract owner/repo from %s", repo_url)
        return []
    owner_repo = m.group(1)

    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{owner_repo}/releases", "--paginate",
             "-q", '.[] | {tag: .tag_name, date: .published_at, body: .body}'],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            log.warning("gh api failed for %s: %s", owner_repo, result.stderr[:200])
            return []
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.warning("gh CLI unavailable or timed out: %s", exc)
        return []

    entries: list[ChangelogEntry] = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        try:
            rel = json.loads(line)
        except json.JSONDecodeError:
            continue

        tag = rel.get("tag", "")
        body = rel.get("body", "") or ""
        pub_date = (rel.get("date") or "")[:10]

        # Date filtering
        if pub_date and (date_start or date_end):
            try:
                rd = date.fromisoformat(pub_date)
                if date_start and rd < date_start:
                    continue
                if date_end and rd > date_end:
                    continue
            except ValueError:
                pass

        body_lower = body.lower()
        if not any(kw in body_lower for kw in BREAKING_KEYWORDS):
            continue

        # Extract breaking items
        items = []
        for raw_line in body.splitlines():
            stripped = raw_line.strip()
            if stripped.startswith(("-", "*")) and len(stripped) > 2:
                item_text = stripped[1:].strip()
                if any(kw in item_text.lower() for kw in BREAKING_KEYWORDS):
                    items.append(item_text)

        if not items:
            # Take all bullet points from the release body as potential items
            items = [stripped[1:].strip() for stripped in
                     (l.strip() for l in body.splitlines())
                     if stripped.startswith(("-", "*")) and len(stripped) > 2]

        if items:
            entries.append(ChangelogEntry(
                repo_url=repo_url,
                version=tag,
                release_date=pub_date or None,
                raw_text=body[:2000],
                breaking_items=tuple(items[:20]),
                source_file=f"GitHub release {tag}",
            ))

    return entries


def parse_changelogs(repo_url: str,
                     local_path: Optional[Path] = None,
                     date_start: Optional[date] = None,
                     date_end: Optional[date] = None) -> list[ChangelogEntry]:
    """Stage 1: Parse changelogs for breaking-change entries.

    Tries local files first if local_path is provided, then falls back to
    GitHub releases API.
    """
    entries: list[ChangelogEntry] = []

    # Try local changelog files
    if local_path and local_path.is_dir():
        for changelog_file in _find_changelog_files(local_path):
            text = changelog_file.read_text(errors="replace")
            parsed = _parse_keepachangelog(
                text, repo_url, changelog_file.name, date_start, date_end,
            )
            entries.extend(parsed)
            log.info("Parsed %d breaking entries from %s", len(parsed), changelog_file.name)

    # Try GitHub releases
    if not entries:
        gh_entries = _fetch_github_releases(repo_url, date_start, date_end)
        entries.extend(gh_entries)
        log.info("Fetched %d breaking entries from GitHub releases", len(gh_entries))

    return entries


# ---------------------------------------------------------------------------
# Stage 2: PR/Commit linker
# ---------------------------------------------------------------------------

_PR_REF = re.compile(r"#(\d+)")
_COMMIT_REF = re.compile(r"\b([0-9a-f]{7,40})\b")


def _extract_pr_refs(text: str) -> list[int]:
    """Extract PR/issue numbers from text (#1234 patterns)."""
    return [int(m.group(1)) for m in _PR_REF.finditer(text)]


def _extract_commit_refs(text: str) -> list[str]:
    """Extract commit SHA references from text."""
    return [m.group(1) for m in _COMMIT_REF.finditer(text)
            if len(m.group(1)) >= 7 and not m.group(1).isdigit()]


def _get_pr_changed_files(owner_repo: str, pr_number: int) -> list[str]:
    """Get changed files for a PR via gh CLI."""
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{owner_repo}/pulls/{pr_number}/files",
             "-q", ".[].filename"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return [f for f in result.stdout.strip().splitlines() if f]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return []


def link_commits(entries: list[ChangelogEntry]) -> list[LinkedEntry]:
    """Stage 2: Resolve changelog entries to specific PRs/commits.

    Extracts PR numbers and commit SHAs from changelog text, then queries
    GitHub for changed files when possible.
    """
    linked: list[LinkedEntry] = []

    for entry in entries:
        all_text = entry.raw_text + " " + " ".join(entry.breaking_items)

        pr_numbers = sorted(set(_extract_pr_refs(all_text)))
        commit_shas = _extract_commit_refs(all_text)

        # Extract owner/repo for API calls
        m = re.search(r"github\.com/([^/]+/[^/]+?)(?:\.git)?$", entry.repo_url)
        owner_repo = m.group(1) if m else ""

        # Get changed files from PRs
        changed_files: list[str] = []
        if owner_repo and pr_numbers:
            for pr_num in pr_numbers[:3]:  # Limit API calls
                changed_files.extend(_get_pr_changed_files(owner_repo, pr_num))

        pr_urls = tuple(
            f"https://github.com/{owner_repo}/pull/{n}" if owner_repo else f"#{n}"
            for n in pr_numbers
        )

        # Determine confidence
        if pr_numbers and (commit_shas or changed_files):
            confidence = "high"
        elif pr_numbers or commit_shas:
            confidence = "medium"
        else:
            confidence = "low"

        linked.append(LinkedEntry(
            entry=entry,
            pr_numbers=tuple(pr_numbers),
            pr_urls=pr_urls,
            commit_shas=tuple(commit_shas[:5]),
            changed_files=tuple(sorted(set(changed_files))),
            confidence=confidence,
        ))

    return linked


# ---------------------------------------------------------------------------
# Stage 3: Cross-repo impact analyzer
# ---------------------------------------------------------------------------

def _detect_ecosystem(repo_url: str) -> str:
    """Infer the primary ecosystem from the repo URL or known patterns."""
    url_lower = repo_url.lower()
    go_indicators = ["grpc-go", "etcd", "containerd", "kubernetes", "nerdctl",
                     "buildkit", "/go-", "moby", "consul", "nomad"]
    rust_indicators = ["tokio", "hyper", "tonic", "tower", "axum", "reqwest",
                       "actix", "serde", "warp"]
    python_indicators = ["urllib3", "requests", "boto", "setuptools", "pip",
                         "poetry", "pydantic", "fastapi", "paramiko", "cryptography"]
    ts_indicators = ["eslint", "typescript", "webpack", "babel", "jest", "vite"]

    for ind in go_indicators:
        if ind in url_lower:
            return "go"
    for ind in rust_indicators:
        if ind in url_lower:
            return "rust"
    for ind in python_indicators:
        if ind in url_lower:
            return "python"
    for ind in ts_indicators:
        if ind in url_lower:
            return "typescript"
    return "unknown"


def _search_downstream_consumers(owner_repo: str, ecosystem: str) -> list[dict]:
    """Search for downstream repos that depend on the upstream.

    Uses `gh search code` to find import/dependency references.
    """
    short_name = owner_repo.split("/")[-1]

    query_patterns = {
        "go": f'"{owner_repo}" filename:go.mod',
        "python": f'"{short_name}" filename:requirements.txt OR filename:setup.cfg OR filename:pyproject.toml',
        "rust": f'"{short_name}" filename:Cargo.toml',
        "typescript": f'"{short_name}" filename:package.json',
    }

    query = query_patterns.get(ecosystem, f'"{short_name}"')

    try:
        result = subprocess.run(
            ["gh", "search", "code", query, "--limit", "20",
             "-q", ".[] | .repository.full_name"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            repos = list(set(
                r.strip() for r in result.stdout.strip().splitlines()
                if r.strip() and r.strip() != owner_repo
            ))
            return [{"repo": r, "source": "gh_search"} for r in repos[:10]]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return []


def analyze_impact(linked_entries: list[LinkedEntry]) -> list[ImpactCandidate]:
    """Stage 3: Identify cross-repo API surfaces affected by each breaking change.

    For each linked changelog entry, searches for downstream repos that import
    the upstream and may be affected.
    """
    candidates: list[ImpactCandidate] = []

    for le in linked_entries:
        repo_url = le.entry.repo_url
        ecosystem = _detect_ecosystem(repo_url)

        m = re.search(r"github\.com/([^/]+/[^/]+?)(?:\.git)?$", repo_url)
        owner_repo = m.group(1) if m else ""

        # Search for downstream consumers
        downstream_list = _search_downstream_consumers(owner_repo, ecosystem)

        # Extract breaking symbols from items
        symbols = []
        for item in le.entry.breaking_items:
            # Look for code-like tokens: PascalCase, snake_case, package.Function
            tokens = re.findall(r"[A-Z][a-zA-Z0-9]+(?:\.[A-Z][a-zA-Z0-9]+)*", item)
            tokens += re.findall(r"[a-z_][a-z_0-9]+(?:\.[a-z_][a-z_0-9]+)+", item)
            symbols.extend(tokens)

        if not downstream_list:
            # Still produce a candidate with unknown downstream
            candidates.append(ImpactCandidate(
                linked=le,
                ecosystem=ecosystem,
                upstream_repo=repo_url,
                upstream_version=le.entry.version,
                downstream_repo="(unknown — needs manual identification)",
                downstream_affected_version="(unknown)",
                downstream_fix_version=None,
                downstream_fix_pr=None,
                downstream_fix_commit=None,
                breaking_symbols=tuple(sorted(set(symbols))[:10]),
                affected_downstream_files=(),
                description=le.entry.breaking_items[0] if le.entry.breaking_items else "",
                confidence=le.confidence,
            ))
        else:
            for ds in downstream_list[:3]:  # Limit candidates per upstream
                candidates.append(ImpactCandidate(
                    linked=le,
                    ecosystem=ecosystem,
                    upstream_repo=repo_url,
                    upstream_version=le.entry.version,
                    downstream_repo=f"https://github.com/{ds['repo']}",
                    downstream_affected_version="(needs pinning)",
                    downstream_fix_version=None,
                    downstream_fix_pr=None,
                    downstream_fix_commit=None,
                    breaking_symbols=tuple(sorted(set(symbols))[:10]),
                    affected_downstream_files=(),
                    description=le.entry.breaking_items[0] if le.entry.breaking_items else "",
                    confidence=le.confidence,
                ))

    return candidates


# ---------------------------------------------------------------------------
# Stage 4: Skeleton generator
# ---------------------------------------------------------------------------

_SUITE_PREFIX = {
    "dependency_management": "dep-mgmt",
    "incident_response": "inc-resp",
    "platform_engineering": "plat-eng",
    "security_operations": "sec-ops",
    "customer_escalation": "cust-esc",
    "feature_delivery": "feat-del",
    "technical_debt": "tech-debt",
}


def _slugify(text: str, max_words: int = 4) -> str:
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9 ]", "", slug)
    return "-".join(slug.split()[:max_words])


def _make_task_id(suite: str, description: str, number: int) -> str:
    prefix = _SUITE_PREFIX.get(suite, suite[:8])
    slug = _slugify(description)
    return f"{prefix}-{slug}-{number:03d}"


def _estimate_difficulty(num_files: int, num_symbols: int) -> tuple[str, int]:
    if num_files <= 3 and num_symbols <= 2:
        return "medium", 30
    if num_files <= 8 and num_symbols <= 6:
        return "hard", 45
    return "expert", 90


def _render_task_toml(candidate: ImpactCandidate, task_id: str,
                      difficulty: str, duration: int) -> str:
    """Render a task.toml without external TOML library."""
    upstream_name = candidate.upstream_repo.rstrip("/").split("/")[-1]
    downstream_name = candidate.downstream_repo.rstrip("/").split("/")[-1]

    symbols_md = "\n".join(f"- {s}" for s in candidate.breaking_symbols) or "- (see changelog)"
    prompt = (
        f"The {upstream_name} library has released {candidate.upstream_version} which\n"
        f"contains breaking API changes. The {downstream_name} project depends on\n"
        f"{upstream_name} and is affected by these changes.\n"
        f"\n"
        f"Known breaking changes in {upstream_name} {candidate.upstream_version}:\n"
        f"{symbols_md}\n"
        f"\n"
        f"Your task:\n"
        f"1. Identify all files in {downstream_name} affected by the breaking API changes\n"
        f"2. Determine the correct migration path for each affected file\n"
        f"3. Apply the necessary code changes to make {downstream_name} compatible\n"
        f"4. Verify the changes compile and tests pass\n"
        f"\n"
        f"Both repositories are available under /workspace/."
    )

    lines = [
        f"# Generated by mining pipeline — requires human review",
        f"# Source: {candidate.linked.entry.source_file}",
        f"",
        f"[task]",
        f'id = "{task_id}"',
        f'suite = "dependency_management"',
        f'task_type = "dependency_graph"',
        f'difficulty = "{difficulty}"',
        f"estimated_duration_minutes = {duration}",
        f'session_type = "single"',
        f'description = "{candidate.description[:78]}"',
        f'prompt = """',
        prompt.rstrip(),
        f'"""',
        f"",
        f"[[repos]]",
        f'url = "{candidate.upstream_repo}"',
        f'rev = "{candidate.upstream_version}"',
        f'path = "{upstream_name}"',
        f'role = "dependency"',
        f"",
        f"[[repos]]",
        f'url = "{candidate.downstream_repo}"',
        f'rev = "{candidate.downstream_affected_version}"',
        f'path = "{downstream_name}"',
        f'role = "primary"',
        f"",
        f"[metadata]",
        f'languages = ["{candidate.ecosystem}"]',
        f"dependency_depth = 2",
        f"frameworks = []",
        f'multi_repo_pattern = "propagate"',
        f"",
        f"[[checkpoints]]",
        f'name = "identify_affected_files"',
        f"weight = 0.20",
        f'verifier = "checks/check_identification.sh"',
        f'description = "Agent identifies files in {downstream_name} affected by the breaking change"',
        f"",
        f"[[checkpoints]]",
        f'name = "apply_api_migration"',
        f"weight = 0.45",
        f'verifier = "checks/check_api_migration.sh"',
        f'description = "Breaking API calls replaced with updated equivalents"',
        f"",
        f"[[checkpoints]]",
        f'name = "tests_pass"',
        f"weight = 0.35",
        f'verifier = "checks/check_tests.sh"',
        f'description = "{downstream_name} tests pass after migration"',
        f"",
        f"[artifacts]",
        f'required = ["code_patch"]',
        f'optional = ["migration_guide"]',
        f"",
        f"[ground_truth]",
        f'tiers = ["deterministic"]',
        f"",
        f"[[ground_truth.required_files]]",
        f'path = "(needs human review)"',
        f'repo = "{upstream_name}"',
        f"confidence = 0.5",
        f'source = "deterministic"',
        f"",
        f"[[ground_truth.required_files]]",
        f'path = "(needs human review)"',
        f'repo = "{downstream_name}"',
        f"confidence = 0.5",
        f'source = "deterministic"',
        f"",
        f"[tool_access]",
        f'expected_mcp_benefit = "medium"',
        f'mcp_benefit_rationale = "Cross-repo symbol search helps find affected files"',
    ]
    return "\n".join(lines) + "\n"


def _render_instruction_md(candidate: ImpactCandidate, task_id: str) -> str:
    """Generate a draft instruction.md for human refinement."""
    upstream_name = candidate.upstream_repo.rstrip("/").split("/")[-1]
    downstream_name = candidate.downstream_repo.rstrip("/").split("/")[-1]

    symbols_list = "\n".join(f"- `{s}`" for s in candidate.breaking_symbols) or "- (see changelog)"

    prs = ", ".join(candidate.linked.pr_urls) or "(none linked)"
    commits = ", ".join(f"`{s[:12]}`" for s in candidate.linked.commit_shas) or "(none linked)"

    return textwrap.dedent(f"""\
        # {task_id}

        > **Draft** — generated by mining pipeline. Requires human review.

        ## Context

        The `{upstream_name}` library released version **{candidate.upstream_version}**
        which introduced breaking API changes. The `{downstream_name}` project depends
        on `{upstream_name}` and needs to adapt to these changes.

        **Breaking changes:**
        {symbols_list}

        **Source:** {candidate.linked.entry.source_file}
        **Upstream PRs:** {prs}
        **Linked commits:** {commits}

        ## Task

        You are investigating the impact of the `{upstream_name}` {candidate.upstream_version}
        release on `{downstream_name}`.

        1. **Identify** all files in `{downstream_name}` that reference the changed APIs
        2. **Analyze** the nature of each breaking change (removed, renamed, signature changed)
        3. **Apply** the necessary migration to each affected file
        4. **Verify** the changes compile and existing tests pass

        ## Output

        Write your analysis and changes to `/workspace/analysis/IMPACT_REPORT.md`.

        ## Hints

        - Both repos are available under `/workspace/`
        - Focus on import statements and direct API usage
        - Check for version pins in dependency manifests

        ---
        *Generated from {candidate.linked.entry.source_file}*
    """)


def generate_skeletons(candidates: list[ImpactCandidate],
                       output_dir: Path,
                       validate: bool = False) -> list[SkeletonOutput]:
    """Stage 4: Generate task skeleton directories for each candidate.

    Creates one directory per candidate with:
      - task.toml (draft)
      - instruction.md (draft)
      - metadata.json (provenance)
      - checks/ (stub verifier scripts)
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    skeletons: list[SkeletonOutput] = []

    for i, candidate in enumerate(candidates, 1):
        task_id = _make_task_id(
            "dependency_management",
            candidate.description,
            i,
        )

        num_files = len(candidate.affected_downstream_files) + len(candidate.linked.changed_files)
        difficulty, duration = _estimate_difficulty(num_files, len(candidate.breaking_symbols))

        # Create candidate directory
        candidate_dir = output_dir / task_id
        candidate_dir.mkdir(parents=True, exist_ok=True)
        checks_dir = candidate_dir / "checks"
        checks_dir.mkdir(exist_ok=True)

        # Generate task.toml
        task_toml = _render_task_toml(candidate, task_id, difficulty, duration)
        (candidate_dir / "task.toml").write_text(task_toml)

        # Generate instruction.md
        instruction_md = _render_instruction_md(candidate, task_id)
        (candidate_dir / "instruction.md").write_text(instruction_md)

        # Generate metadata.json
        metadata = {
            "candidate_id": task_id,
            "pipeline_version": "1.0.0",
            "ecosystem": candidate.ecosystem,
            "upstream_repo": candidate.upstream_repo,
            "upstream_version": candidate.upstream_version,
            "downstream_repo": candidate.downstream_repo,
            "downstream_affected_version": candidate.downstream_affected_version,
            "downstream_fix_version": candidate.downstream_fix_version,
            "downstream_fix_pr": candidate.downstream_fix_pr,
            "downstream_fix_commit": candidate.downstream_fix_commit,
            "breaking_symbols": list(candidate.breaking_symbols),
            "source_prs": list(candidate.linked.pr_urls),
            "source_commits": list(candidate.linked.commit_shas),
            "source_file": candidate.linked.entry.source_file,
            "confidence": candidate.confidence,
            "affected_downstream_files": list(candidate.affected_downstream_files),
            "changed_upstream_files": list(candidate.linked.changed_files),
            "raw_changelog_text": candidate.linked.entry.raw_text[:500],
        }
        (candidate_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2) + "\n",
        )

        # Generate stub verifier scripts
        _write_check_stub(checks_dir / "check_identification.sh", "identify_affected_files")
        _write_check_stub(checks_dir / "check_api_migration.sh", "apply_api_migration")
        _write_check_stub(checks_dir / "check_tests.sh", "tests_pass")

        # CRNT validation
        crnt_passed = None
        if validate:
            crnt_passed = _run_crnt_validation(candidate_dir / "task.toml")

        skeletons.append(SkeletonOutput(
            candidate_id=task_id,
            output_dir=str(candidate_dir),
            task_toml=task_toml,
            instruction_md=instruction_md,
            metadata_json=metadata,
            crnt_passed=crnt_passed,
        ))

    return skeletons


def _write_check_stub(path: Path, checkpoint_name: str) -> None:
    """Write a stub checkpoint verifier script."""
    content = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        # Stub verifier for checkpoint: {checkpoint_name}
        # Generated by mining pipeline — must be filled in during human review.
        set -euo pipefail

        REPORT="${{WORKSPACE:-/workspace}}/analysis/IMPACT_REPORT.md"

        if [[ ! -f "$REPORT" ]]; then
            printf '{{"score": 0.0, "passed": false, "reason": "Report not found at %s"}}\\n' "$REPORT"
            exit 0
        fi

        # TODO: Add specific grep checks for this checkpoint.
        # Example:
        #   FOUND=0; TOTAL=3
        #   grep -qiE 'pattern1' "$REPORT" && FOUND=$((FOUND + 1))
        #   SCORE=$(awk "BEGIN {{printf \\"%.2f\\", $FOUND/$TOTAL}}")
        #   printf '{{"score": %s, "passed": %s, "reason": "Found %d/%d"}}\\n' "$SCORE" "$([ $FOUND -ge $TOTAL ] && echo true || echo false)" "$FOUND" "$TOTAL"

        printf '{{"score": 0.0, "passed": false, "reason": "Stub verifier — needs implementation"}}\\n'
    """)
    path.write_text(content)
    path.chmod(0o755)


def _run_crnt_validation(task_toml_path: Path) -> bool:
    """Run CRNT validator on a generated task.toml."""
    validator = Path(__file__).resolve().parent.parent / "validation" / "crnt_validator.py"
    if not validator.is_file():
        log.warning("CRNT validator not found at %s", validator)
        return False

    try:
        result = subprocess.run(
            [sys.executable, str(validator), str(task_toml_path), "--json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("passed", False)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass

    return False


# ---------------------------------------------------------------------------
# Curated database (for --curated mode, no network required)
# ---------------------------------------------------------------------------

def _curated_candidates() -> list[ImpactCandidate]:
    """Return curated breaking-change candidates from 3 ecosystems.

    These are verified historical breaking changes suitable for benchmark tasks.
    The curated database allows testing the pipeline without network access.
    """
    def _make(ecosystem: str, upstream_repo: str, version: str,
              downstream_repo: str, ds_affected: str, ds_fix: Optional[str],
              ds_fix_pr: Optional[str], ds_fix_commit: Optional[str],
              symbols: list[str], ds_files: list[str],
              description: str, confidence: str,
              pr_urls: list[str], commit_shas: list[str],
              changed_files: list[str], source: str,
              raw_text: str = "", items: Optional[list[str]] = None) -> ImpactCandidate:
        entry = ChangelogEntry(
            repo_url=upstream_repo, version=version,
            release_date=None, raw_text=raw_text[:500],
            breaking_items=tuple(items or [description]),
            source_file=source,
        )
        linked = LinkedEntry(
            entry=entry,
            pr_numbers=tuple(int(u.split("/")[-1]) for u in pr_urls if u.split("/")[-1].isdigit()),
            pr_urls=tuple(pr_urls),
            commit_shas=tuple(commit_shas),
            changed_files=tuple(changed_files),
            confidence=confidence,
        )
        return ImpactCandidate(
            linked=linked, ecosystem=ecosystem,
            upstream_repo=upstream_repo, upstream_version=version,
            downstream_repo=downstream_repo,
            downstream_affected_version=ds_affected,
            downstream_fix_version=ds_fix,
            downstream_fix_pr=ds_fix_pr,
            downstream_fix_commit=ds_fix_commit,
            breaking_symbols=tuple(symbols),
            affected_downstream_files=tuple(ds_files),
            description=description, confidence=confidence,
        )

    return [
        # ── Go ecosystem ──────────────────────────────────────────────
        _make(
            ecosystem="go",
            upstream_repo="https://github.com/grpc/grpc-go",
            version="v1.27.0",
            downstream_repo="https://github.com/etcd-io/etcd",
            ds_affected="v3.4.7", ds_fix="v3.4.8",
            ds_fix_pr="https://github.com/etcd-io/etcd/pull/11564",
            ds_fix_commit="4258cdd2efdf79a145067ae426f93b9f220c05f1",
            symbols=["resolver.BuildOption", "resolver.ResolveNowOption", "balancer.PickOptions"],
            ds_files=["clientv3/balancer/resolver/endpoint/endpoint.go",
                       "clientv3/balancer/picker/err.go",
                       "clientv3/balancer/picker/roundrobin_balanced.go",
                       "clientv3/balancer/utils.go"],
            description="grpc-go v1.27.0 removed backward-compat type aliases for balancer/resolver APIs",
            confidence="high",
            pr_urls=["https://github.com/etcd-io/etcd/pull/11564"],
            commit_shas=["4258cdd2efdf79a145067ae426f93b9f220c05f1"],
            changed_files=["clientv3/balancer/resolver/endpoint/endpoint.go",
                           "clientv3/balancer/picker/err.go"],
            source="CHANGELOG.md grpc-go v1.27.0",
        ),
        _make(
            ecosystem="go",
            upstream_repo="https://github.com/grpc/grpc-go",
            version="v1.32.0",
            downstream_repo="https://github.com/etcd-io/etcd",
            ds_affected="v3.4.13", ds_fix="v3.5.0",
            ds_fix_pr="https://github.com/etcd-io/etcd/pull/12671",
            ds_fix_commit=None,
            symbols=["naming.Resolver", "naming.Update", "naming.Watcher"],
            ds_files=["clientv3/balancer/"],
            description="grpc-go v1.32.0 removed naming interface, requiring etcd balancer rewrite",
            confidence="medium",
            pr_urls=["https://github.com/etcd-io/etcd/pull/12671"],
            commit_shas=[],
            changed_files=["clientv3/balancer/"],
            source="CHANGELOG.md grpc-go v1.32.0",
        ),
        _make(
            ecosystem="go",
            upstream_repo="https://github.com/containerd/containerd",
            version="v1.7.0",
            downstream_repo="https://github.com/containerd/nerdctl",
            ds_affected="v1.2.1", ds_fix="v1.3.0",
            ds_fix_pr="https://github.com/containerd/nerdctl/pull/2267",
            ds_fix_commit=None,
            symbols=["errdefs.ErrNotFound", "errdefs.ErrAlreadyExists",
                      "content.Store", "images.Store"],
            ds_files=["pkg/imgutil/imgutil.go", "pkg/containerutil/containerutil.go",
                       "cmd/nerdctl/run.go"],
            description="containerd v1.7 relocated errdefs and store interfaces to sub-packages",
            confidence="high",
            pr_urls=["https://github.com/containerd/nerdctl/pull/2267"],
            commit_shas=[],
            changed_files=["pkg/imgutil/imgutil.go", "pkg/containerutil/containerutil.go"],
            source="GitHub release containerd v1.7.0",
        ),
        _make(
            ecosystem="go",
            upstream_repo="https://github.com/containerd/containerd",
            version="v1.7.0",
            downstream_repo="https://github.com/moby/buildkit",
            ds_affected="v0.11.6", ds_fix="v0.12.0",
            ds_fix_pr="https://github.com/moby/buildkit/pull/3901",
            ds_fix_commit=None,
            symbols=["content.Store", "leases.Manager", "snapshots.Snapshotter"],
            ds_files=["util/contentutil/buffer.go", "snapshot/localmounter.go",
                       "executor/containerdexecutor/executor.go"],
            description="containerd v1.7 API reorganization broke buildkit content and snapshot interfaces",
            confidence="high",
            pr_urls=["https://github.com/moby/buildkit/pull/3901"],
            commit_shas=[],
            changed_files=["util/contentutil/buffer.go", "snapshot/localmounter.go"],
            source="GitHub release containerd v1.7.0",
        ),

        # ── Python ecosystem ─────────────────────────────────────────
        _make(
            ecosystem="python",
            upstream_repo="https://github.com/urllib3/urllib3",
            version="2.0.0",
            downstream_repo="https://github.com/psf/requests",
            ds_affected="v2.28.2", ds_fix="v2.31.0",
            ds_fix_pr=None,
            ds_fix_commit="2ad18e0e10e7d7ecd5384c378f25ec8821a10a29",
            symbols=["Retry.DEFAULT_REDIRECT", "HTTPResponse.getheaders",
                      "urllib3.util.ssl_.DEFAULT_CIPHERS"],
            ds_files=["setup.cfg", "requests/adapters.py",
                       "requests/compat.py", "requests/models.py"],
            description="urllib3 2.0.0 major version broke requests version pin and deprecated APIs",
            confidence="high",
            pr_urls=[],
            commit_shas=["2ad18e0e10e7d7ecd5384c378f25ec8821a10a29"],
            changed_files=["setup.cfg", "requests/adapters.py"],
            source="CHANGELOG.rst urllib3 2.0.0",
        ),
        _make(
            ecosystem="python",
            upstream_repo="https://github.com/pydantic/pydantic",
            version="v2.0.0",
            downstream_repo="https://github.com/tiangolo/fastapi",
            ds_affected="0.99.1", ds_fix="0.100.0",
            ds_fix_pr="https://github.com/tiangolo/fastapi/pull/9816",
            ds_fix_commit=None,
            symbols=["BaseModel.dict", "BaseModel.json", "BaseModel.__fields__",
                      "validator", "root_validator", "Field.regex"],
            ds_files=["fastapi/encoders.py", "fastapi/utils.py",
                       "fastapi/routing.py", "fastapi/dependencies/utils.py"],
            description="pydantic v2 rewrote BaseModel API, breaking FastAPI model serialization",
            confidence="high",
            pr_urls=["https://github.com/tiangolo/fastapi/pull/9816"],
            commit_shas=[],
            changed_files=["fastapi/encoders.py", "fastapi/utils.py",
                           "fastapi/routing.py"],
            source="CHANGELOG.md pydantic v2.0",
        ),
        _make(
            ecosystem="python",
            upstream_repo="https://github.com/pyca/cryptography",
            version="41.0.0",
            downstream_repo="https://github.com/paramiko/paramiko",
            ds_affected="3.2.0", ds_fix="3.3.1",
            ds_fix_pr="https://github.com/paramiko/paramiko/pull/2324",
            ds_fix_commit=None,
            symbols=["default_backend", "Cipher.encryptor",
                      "serialization.Encoding"],
            ds_files=["paramiko/transport.py", "paramiko/kex_group1.py",
                       "paramiko/packet.py"],
            description="cryptography 41.0 dropped OpenSSL 1.1.1 and removed deprecated backend param",
            confidence="high",
            pr_urls=["https://github.com/paramiko/paramiko/pull/2324"],
            commit_shas=[],
            changed_files=["paramiko/transport.py", "paramiko/kex_group1.py"],
            source="CHANGELOG.rst cryptography 41.0.0",
        ),
        _make(
            ecosystem="python",
            upstream_repo="https://github.com/pypa/setuptools",
            version="67.0.0",
            downstream_repo="https://github.com/pypa/pip",
            ds_affected="23.0", ds_fix="23.1",
            ds_fix_pr=None,
            ds_fix_commit=None,
            symbols=["pkg_resources.declare_namespace", "pkg_resources.working_set",
                      "easy_install"],
            ds_files=["src/pip/_vendor/pkg_resources/__init__.py",
                       "src/pip/_internal/metadata/pkg_resources.py"],
            description="setuptools 67.0 deprecated pkg_resources in favor of importlib.metadata",
            confidence="medium",
            pr_urls=[],
            commit_shas=[],
            changed_files=["src/pip/_vendor/pkg_resources/__init__.py"],
            source="CHANGELOG.rst setuptools 67.0.0",
        ),

        # ── Rust ecosystem ────────────────────────────────────────────
        _make(
            ecosystem="rust",
            upstream_repo="https://github.com/tokio-rs/tokio",
            version="1.0.0",
            downstream_repo="https://github.com/hyperium/hyper",
            ds_affected="0.13.10", ds_fix="0.14.0",
            ds_fix_pr="https://github.com/hyperium/hyper/pull/2449",
            ds_fix_commit=None,
            symbols=["tokio::runtime::Builder", "tokio::task::JoinHandle",
                      "rt-core", "rt-threaded"],
            ds_files=["src/server/tcp.rs", "src/client/connect/http.rs",
                       "Cargo.toml"],
            description="tokio 1.0 renamed runtime features and changed Builder API",
            confidence="high",
            pr_urls=["https://github.com/hyperium/hyper/pull/2449"],
            commit_shas=[],
            changed_files=["src/server/tcp.rs", "Cargo.toml"],
            source="CHANGELOG.md tokio 1.0.0",
        ),
        _make(
            ecosystem="rust",
            upstream_repo="https://github.com/hyperium/hyper",
            version="1.0.0",
            downstream_repo="https://github.com/seanmonstar/reqwest",
            ds_affected="0.11.22", ds_fix="0.12.0",
            ds_fix_pr="https://github.com/seanmonstar/reqwest/pull/1920",
            ds_fix_commit=None,
            symbols=["hyper::Client", "hyper::Body", "hyper::server::Server",
                      "hyper::service::make_service_fn"],
            ds_files=["src/async_impl/client.rs", "src/async_impl/body.rs",
                       "src/connect.rs", "Cargo.toml"],
            description="hyper 1.0 split client/server into separate crates and replaced Body type",
            confidence="high",
            pr_urls=["https://github.com/seanmonstar/reqwest/pull/1920"],
            commit_shas=[],
            changed_files=["src/async_impl/client.rs", "src/connect.rs", "Cargo.toml"],
            source="CHANGELOG.md hyper 1.0.0",
        ),
        _make(
            ecosystem="rust",
            upstream_repo="https://github.com/hyperium/tonic",
            version="0.11.0",
            downstream_repo="https://github.com/tikv/tikv",
            ds_affected="7.4.0", ds_fix="7.5.0",
            ds_fix_pr=None,
            ds_fix_commit=None,
            symbols=["tonic::transport::Server", "tonic::transport::Channel",
                      "tonic::codec::ProstCodec"],
            ds_files=["src/server/service/kv.rs", "components/grpcio-client/src/lib.rs",
                       "Cargo.toml"],
            description="tonic 0.11 transport API restructuring broke downstream gRPC services",
            confidence="medium",
            pr_urls=[],
            commit_shas=[],
            changed_files=["src/server/service/kv.rs"],
            source="CHANGELOG.md tonic 0.11.0",
        ),
        _make(
            ecosystem="rust",
            upstream_repo="https://github.com/tokio-rs/axum",
            version="0.7.0",
            downstream_repo="https://github.com/tokio-rs/axum",
            ds_affected="0.6.20", ds_fix="0.7.0",
            ds_fix_pr="https://github.com/tokio-rs/axum/pull/2521",
            ds_fix_commit=None,
            symbols=["Router.merge", "Router.nest", "axum::body::BoxBody",
                      "axum::extract::State"],
            ds_files=["examples/", "axum-extra/src/lib.rs",
                       "axum-core/src/extract/mod.rs"],
            description="axum 0.7 changed Router generics and Body type constraints",
            confidence="medium",
            pr_urls=["https://github.com/tokio-rs/axum/pull/2521"],
            commit_shas=[],
            changed_files=["axum-core/src/extract/mod.rs"],
            source="CHANGELOG.md axum 0.7.0",
        ),
    ]


# ---------------------------------------------------------------------------
# CLI and orchestration
# ---------------------------------------------------------------------------

def print_summary(candidates: list[ImpactCandidate],
                  skeletons: list[SkeletonOutput]) -> None:
    """Print pipeline run summary."""
    print(f"\n{'='*70}")
    print(f"MINING PIPELINE SUMMARY")
    print(f"{'='*70}")
    print(f"Candidates analyzed: {len(candidates)}")
    print(f"Skeletons generated: {len(skeletons)}")

    by_eco = {}
    for c in candidates:
        by_eco.setdefault(c.ecosystem, []).append(c)
    print(f"\nBy ecosystem:")
    for eco, items in sorted(by_eco.items()):
        print(f"  {eco}: {len(items)} candidates")

    by_conf = {}
    for c in candidates:
        by_conf.setdefault(c.confidence, []).append(c)
    print(f"\nBy confidence:")
    for conf in ("high", "medium", "low"):
        count = len(by_conf.get(conf, []))
        if count:
            print(f"  {conf}: {count}")

    crnt_validated = [s for s in skeletons if s.crnt_passed is not None]
    if crnt_validated:
        passed = sum(1 for s in crnt_validated if s.crnt_passed)
        print(f"\nCRNT validation: {passed}/{len(crnt_validated)} passed")
        for s in crnt_validated:
            icon = "PASS" if s.crnt_passed else "FAIL"
            print(f"  [{icon}] {s.candidate_id}")

    print(f"\nGenerated skeletons:")
    for s in skeletons:
        print(f"  {s.output_dir}/")
        print(f"    task.toml, instruction.md, metadata.json, checks/")

    print(f"\n{'='*70}")
    print(f"Next steps: review each candidate using the checklist in")
    print(f"  scripts/mining/REVIEW_CHECKLIST.md")
    print(f"{'='*70}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mine historical breaking changes from OSS repos to generate benchmark task candidates",
    )
    parser.add_argument(
        "--repo",
        help="GitHub repo URL to mine (e.g., https://github.com/grpc/grpc-go)",
    )
    parser.add_argument(
        "--local-repo",
        type=Path,
        help="Path to a local repo clone to mine",
    )
    parser.add_argument(
        "--date-range",
        nargs=2, metavar=("START", "END"),
        help="Date range for mining (YYYY-MM-DD YYYY-MM-DD)",
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=Path, default=Path("output/mining_candidates"),
        help="Output directory for generated skeletons (default: output/mining_candidates)",
    )
    parser.add_argument(
        "--curated",
        action="store_true",
        help="Use curated database instead of live mining (no network needed)",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Run CRNT validator on generated task.toml files",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON to stdout",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    # Parse date range
    date_start = date_end = None
    if args.date_range:
        try:
            date_start = date.fromisoformat(args.date_range[0])
            date_end = date.fromisoformat(args.date_range[1])
        except ValueError as exc:
            parser.error(f"Invalid date format: {exc}")

    if args.curated:
        # Use curated database — no network needed
        log.info("Using curated breaking-change database")
        candidates = _curated_candidates()
    elif args.repo or args.local_repo:
        repo_url = args.repo or ""
        local_path = args.local_repo

        # Stage 1: Parse changelogs
        log.info("Stage 1: Parsing changelogs for %s", repo_url or local_path)
        entries = parse_changelogs(repo_url, local_path, date_start, date_end)
        log.info("Found %d breaking-change entries", len(entries))

        if not entries:
            log.warning("No breaking-change entries found. Try --curated for demo data.")
            return 1

        # Stage 2: Link commits
        log.info("Stage 2: Linking PRs and commits")
        linked = link_commits(entries)
        log.info("Linked %d entries", len(linked))

        # Stage 3: Analyze cross-repo impact
        log.info("Stage 3: Analyzing cross-repo impact")
        candidates = analyze_impact(linked)
        log.info("Found %d impact candidates", len(candidates))
    else:
        parser.error("Provide --repo, --local-repo, or --curated")
        return 1

    if not candidates:
        log.warning("No candidates found")
        return 1

    # Stage 4: Generate skeletons
    log.info("Stage 4: Generating task skeletons in %s", args.output_dir)
    skeletons = generate_skeletons(candidates, args.output_dir, validate=args.validate)

    if args.json:
        output = {
            "candidates": len(candidates),
            "skeletons": [
                {
                    "candidate_id": s.candidate_id,
                    "output_dir": s.output_dir,
                    "crnt_passed": s.crnt_passed,
                    "metadata": s.metadata_json,
                }
                for s in skeletons
            ],
        }
        print(json.dumps(output, indent=2))
    else:
        print_summary(candidates, skeletons)

    return 0


if __name__ == "__main__":
    sys.exit(main())
