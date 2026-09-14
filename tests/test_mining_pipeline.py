"""Tests for the breaking-change mining pipeline."""

from __future__ import annotations

import json
import textwrap
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

# Add scripts/ to sys.path so we can import the pipeline
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "mining"))

from pipeline import (
    ChangelogEntry,
    ImpactCandidate,
    LinkedEntry,
    SkeletonOutput,
    _curated_candidates,
    _detect_ecosystem,
    _estimate_difficulty,
    _extract_commit_refs,
    _extract_pr_refs,
    _make_task_id,
    _parse_keepachangelog,
    _slugify,
    generate_skeletons,
    link_commits,
    parse_changelogs,
)


# ---------------------------------------------------------------------------
# Stage 1: Changelog parser tests
# ---------------------------------------------------------------------------

SAMPLE_CHANGELOG = textwrap.dedent("""\
    # Changelog

    ## [2.0.0] - 2024-06-15

    ### Breaking Changes

    - Removed `legacy_api()` function (#1234)
    - Renamed `OldClass` to `NewClass` (breaking change)
    - Changed default timeout from 30s to 10s (incompatible)

    ### Added

    - New feature X
    - New feature Y

    ## [1.9.0] - 2024-03-01

    ### Added

    - Feature Z

    ## [1.8.0] - 2023-12-01

    ### Removed

    - Deprecated `helper_v1()` removed (breaking change)
    - `Config.old_field` no longer supported
""")


class TestParseChangelog:
    def test_extracts_breaking_entries(self):
        entries = _parse_keepachangelog(
            SAMPLE_CHANGELOG, "https://github.com/test/repo", "CHANGELOG.md",
        )
        assert len(entries) >= 1
        # v2.0.0 should have breaking items
        v2 = [e for e in entries if e.version == "2.0.0"]
        assert len(v2) == 1
        assert len(v2[0].breaking_items) >= 2

    def test_date_range_filtering(self):
        entries = _parse_keepachangelog(
            SAMPLE_CHANGELOG, "https://github.com/test/repo", "CHANGELOG.md",
            date_start=date(2024, 1, 1),
            date_end=date(2024, 12, 31),
        )
        versions = [e.version for e in entries]
        assert "2.0.0" in versions
        # 1.8.0 is from 2023, should be filtered out
        assert "1.8.0" not in versions

    def test_ignores_non_breaking_versions(self):
        entries = _parse_keepachangelog(
            SAMPLE_CHANGELOG, "https://github.com/test/repo", "CHANGELOG.md",
        )
        versions = [e.version for e in entries]
        assert "1.9.0" not in versions  # no breaking changes in 1.9.0

    def test_captures_repo_url(self):
        entries = _parse_keepachangelog(
            SAMPLE_CHANGELOG, "https://github.com/test/repo", "CHANGELOG.md",
        )
        for entry in entries:
            assert entry.repo_url == "https://github.com/test/repo"
            assert entry.source_file == "CHANGELOG.md"

    def test_empty_changelog(self):
        entries = _parse_keepachangelog("", "url", "file")
        assert entries == []

    def test_no_breaking_items(self):
        changelog = "## [1.0.0] - 2024-01-01\n\n### Added\n\n- New feature\n"
        entries = _parse_keepachangelog(changelog, "url", "file")
        assert entries == []


class TestParseChangelogsLocal:
    def test_finds_local_changelog(self, tmp_path: Path):
        (tmp_path / "CHANGELOG.md").write_text(SAMPLE_CHANGELOG)
        entries = parse_changelogs("https://github.com/test/repo", tmp_path)
        assert len(entries) >= 1

    def test_no_changelog_falls_through(self, tmp_path: Path):
        # No changelog files — should return empty (no network fallback in test)
        with patch("pipeline._fetch_github_releases", return_value=[]):
            entries = parse_changelogs("https://github.com/test/repo", tmp_path)
        assert entries == []


# ---------------------------------------------------------------------------
# Stage 2: PR/commit linker tests
# ---------------------------------------------------------------------------

class TestExtractRefs:
    def test_extract_pr_numbers(self):
        assert _extract_pr_refs("Fixed in #1234 and #5678") == [1234, 5678]

    def test_extract_pr_no_match(self):
        assert _extract_pr_refs("No references here") == []

    def test_extract_commit_shas(self):
        refs = _extract_commit_refs("commit abc1234def and 0123456789abcdef")
        assert "abc1234def" in refs or "abc1234d" in refs
        assert any(len(r) >= 7 for r in refs)

    def test_extract_commit_no_match(self):
        assert _extract_commit_refs("no commits") == []


class TestLinkCommits:
    def test_links_entries_with_pr_refs(self):
        entry = ChangelogEntry(
            repo_url="https://github.com/test/repo",
            version="2.0.0",
            release_date="2024-01-01",
            raw_text="Fixed via #42 (commit abc1234)",
            breaking_items=("Removed function",),
            source_file="CHANGELOG.md",
        )
        with patch("pipeline._get_pr_changed_files", return_value=[]):
            linked = link_commits([entry])
        assert len(linked) == 1
        assert 42 in linked[0].pr_numbers
        assert linked[0].confidence in ("high", "medium")

    def test_links_entry_without_refs(self):
        entry = ChangelogEntry(
            repo_url="https://github.com/test/repo",
            version="2.0.0",
            release_date=None,
            raw_text="Something broke",
            breaking_items=("API removed",),
            source_file="CHANGELOG.md",
        )
        with patch("pipeline._get_pr_changed_files", return_value=[]):
            linked = link_commits([entry])
        assert len(linked) == 1
        assert linked[0].confidence == "low"


# ---------------------------------------------------------------------------
# Stage 3: Ecosystem detection tests
# ---------------------------------------------------------------------------

class TestDetectEcosystem:
    def test_go_repos(self):
        assert _detect_ecosystem("https://github.com/grpc/grpc-go") == "go"
        assert _detect_ecosystem("https://github.com/etcd-io/etcd") == "go"
        assert _detect_ecosystem("https://github.com/containerd/containerd") == "go"

    def test_python_repos(self):
        assert _detect_ecosystem("https://github.com/urllib3/urllib3") == "python"
        assert _detect_ecosystem("https://github.com/psf/requests") == "python"
        assert _detect_ecosystem("https://github.com/pydantic/pydantic") == "python"

    def test_rust_repos(self):
        assert _detect_ecosystem("https://github.com/tokio-rs/tokio") == "rust"
        assert _detect_ecosystem("https://github.com/hyperium/hyper") == "rust"

    def test_typescript_repos(self):
        assert _detect_ecosystem("https://github.com/eslint/eslint") == "typescript"

    def test_unknown_repos(self):
        assert _detect_ecosystem("https://github.com/some/unknown-repo") == "unknown"


# ---------------------------------------------------------------------------
# Stage 4: Skeleton generator tests
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_slugify(self):
        assert _slugify("grpc-go v1.27.0 removed backward-compat") == "grpcgo-v1270-removed-backwardcompat"

    def test_slugify_max_words(self):
        assert len(_slugify("a b c d e f g", max_words=3).split("-")) == 3

    def test_make_task_id(self):
        tid = _make_task_id("dependency_management", "some description", 1)
        assert tid.startswith("dep-mgmt-")
        assert tid.endswith("-001")

    def test_estimate_difficulty(self):
        assert _estimate_difficulty(2, 1) == ("medium", 30)
        assert _estimate_difficulty(5, 4) == ("hard", 45)
        assert _estimate_difficulty(10, 8) == ("expert", 90)


class TestGenerateSkeletons:
    def test_generates_correct_file_structure(self, tmp_path: Path):
        candidates = _curated_candidates()[:2]
        skeletons = generate_skeletons(candidates, tmp_path)

        assert len(skeletons) == 2
        for sk in skeletons:
            candidate_dir = Path(sk.output_dir)
            assert (candidate_dir / "task.toml").is_file()
            assert (candidate_dir / "instruction.md").is_file()
            assert (candidate_dir / "metadata.json").is_file()
            assert (candidate_dir / "checks" / "check_identification.sh").is_file()
            assert (candidate_dir / "checks" / "check_api_migration.sh").is_file()
            assert (candidate_dir / "checks" / "check_tests.sh").is_file()

    def test_metadata_json_has_required_fields(self, tmp_path: Path):
        candidates = _curated_candidates()[:1]
        skeletons = generate_skeletons(candidates, tmp_path)

        metadata = skeletons[0].metadata_json
        required_fields = [
            "candidate_id", "pipeline_version", "ecosystem",
            "upstream_repo", "upstream_version", "downstream_repo",
            "breaking_symbols", "confidence", "source_file",
        ]
        for f in required_fields:
            assert f in metadata, f"Missing field: {f}"

    def test_task_toml_contains_two_repos(self, tmp_path: Path):
        candidates = _curated_candidates()[:1]
        skeletons = generate_skeletons(candidates, tmp_path)

        toml_text = skeletons[0].task_toml
        assert toml_text.count("[[repos]]") == 2
        assert 'role = "dependency"' in toml_text
        assert 'role = "primary"' in toml_text

    def test_check_scripts_are_executable(self, tmp_path: Path):
        candidates = _curated_candidates()[:1]
        generate_skeletons(candidates, tmp_path)

        candidate_dir = tmp_path / next(tmp_path.iterdir()).name
        for script in (candidate_dir / "checks").iterdir():
            assert script.stat().st_mode & 0o111, f"{script} is not executable"

    def test_instruction_md_has_context(self, tmp_path: Path):
        candidates = _curated_candidates()[:1]
        skeletons = generate_skeletons(candidates, tmp_path)

        md = skeletons[0].instruction_md
        assert "## Context" in md
        assert "## Task" in md
        assert "## Output" in md
        assert "Draft" in md  # Draft marker


# ---------------------------------------------------------------------------
# Curated database tests
# ---------------------------------------------------------------------------

class TestCuratedDatabase:
    def test_has_at_least_10_candidates(self):
        candidates = _curated_candidates()
        assert len(candidates) >= 10

    def test_covers_three_ecosystems(self):
        candidates = _curated_candidates()
        ecosystems = {c.ecosystem for c in candidates}
        assert "go" in ecosystems
        assert "python" in ecosystems
        assert "rust" in ecosystems

    def test_each_ecosystem_has_multiple_candidates(self):
        candidates = _curated_candidates()
        by_eco = {}
        for c in candidates:
            by_eco.setdefault(c.ecosystem, []).append(c)
        for eco, items in by_eco.items():
            assert len(items) >= 3, f"{eco} has only {len(items)} candidates, need >= 3"

    def test_has_high_confidence_candidates(self):
        candidates = _curated_candidates()
        high = [c for c in candidates if c.confidence == "high"]
        assert len(high) >= 5

    def test_candidates_have_upstream_downstream(self):
        for c in _curated_candidates():
            assert c.upstream_repo, f"{c.description}: missing upstream_repo"
            assert c.downstream_repo, f"{c.description}: missing downstream_repo"
            assert c.upstream_version, f"{c.description}: missing upstream_version"

    def test_candidates_have_breaking_symbols_or_description(self):
        for c in _curated_candidates():
            assert c.breaking_symbols or c.description


# ---------------------------------------------------------------------------
# Integration test: full pipeline in curated mode
# ---------------------------------------------------------------------------

class TestFullPipeline:
    def test_curated_pipeline_produces_skeletons(self, tmp_path: Path):
        candidates = _curated_candidates()
        skeletons = generate_skeletons(candidates, tmp_path)

        assert len(skeletons) >= 10
        # Verify each skeleton produced all 3 files
        for sk in skeletons:
            d = Path(sk.output_dir)
            assert (d / "task.toml").exists()
            assert (d / "instruction.md").exists()
            assert (d / "metadata.json").exists()

            # Verify metadata.json is valid JSON
            with open(d / "metadata.json") as f:
                meta = json.load(f)
            assert meta["candidate_id"] == sk.candidate_id

    def test_no_duplicate_task_ids(self, tmp_path: Path):
        candidates = _curated_candidates()
        skeletons = generate_skeletons(candidates, tmp_path)
        ids = [s.candidate_id for s in skeletons]
        assert len(ids) == len(set(ids)), f"Duplicate IDs found: {ids}"
