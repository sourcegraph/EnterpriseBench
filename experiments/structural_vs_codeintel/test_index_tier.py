"""`scip-syntax` answers the same call as a semantic index. It must not count as one."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import index_tier


def _payload(*indexes, head="abc123"):
    return {
        "data": {
            "repository": {
                "defaultBranch": {"target": {"oid": head}},
                "codeIntelSummary": {
                    "recentActivity": [
                        {"state": state, "inputCommit": commit, "indexer": {"name": name}}
                        for name, state, commit in indexes
                    ]
                },
            }
        }
    }


def test_a_completed_scip_language_index_is_semantic():
    verdict = index_tier.classify(_payload(("scip-python", "COMPLETED", "abc123")))
    assert verdict["tier"] == "semantic"
    assert verdict["semantic_at_head"] == ["scip-python"]


def test_scip_syntax_alone_is_syntactic_not_semantic():
    """Tree-sitter knows a name's shape, not its resolved symbol identity."""
    verdict = index_tier.classify(_payload(("scip-syntax", "COMPLETED", "abc123")))
    assert verdict["tier"] == "syntactic"
    assert verdict["semantic_indexers"] == []


def test_scip_syntax_alongside_a_language_index_is_semantic():
    verdict = index_tier.classify(
        _payload(("scip-syntax", "COMPLETED", "abc123"), ("scip-go", "COMPLETED", "abc123"))
    )
    assert verdict["tier"] == "semantic"


def test_an_errored_index_is_a_job_not_coverage():
    """Crediting a failed index job would attribute navigation the arm never had."""
    verdict = index_tier.classify(
        _payload(("scip-typescript", "INDEXING_ERRORED", "abc123"))
    )
    assert verdict["tier"] == "none"
    assert verdict["indexers"] == []


def test_a_semantic_index_off_the_pinned_revision_is_recorded_as_such():
    verdict = index_tier.classify(_payload(("scip-rust", "COMPLETED", "deadbee")))
    assert verdict["tier"] == "semantic"
    assert verdict["semantic_indexers"] == ["scip-rust"]
    assert verdict["semantic_at_head"] == []


def test_a_missing_repository_is_unknown_rather_than_unindexed():
    verdict = index_tier.classify({"data": {"repository": None}})
    assert verdict["tier"] == "unknown"


def test_graphql_errors_are_unknown_rather_than_unindexed():
    verdict = index_tier.classify({"errors": [{"message": "field does not exist"}]})
    assert verdict["tier"] == "unknown"
    assert "field does not exist" in verdict["detail"]


def _stores(**tiers):
    return {store: {"tier": tier} for store, tier in tiers.items()}


def test_a_task_takes_the_weakest_tier_among_the_stores_it_spans():
    by_store = _stores(a="semantic", b="syntactic")
    assert index_tier.task_tier(["a", "b"], by_store) == "mixed"


def test_all_semantic_stores_make_a_semantic_task():
    by_store = _stores(a="semantic", b="semantic")
    assert index_tier.task_tier(["a", "b"], by_store) == "semantic"


def test_no_semantic_store_makes_a_syntactic_task():
    by_store = _stores(a="syntactic", b="none")
    assert index_tier.task_tier(["a", "b"], by_store) == "syntactic"


def test_one_unmeasured_store_makes_the_whole_task_unknown():
    """An absent measurement propagates; it is not averaged away by its neighbours."""
    by_store = _stores(a="semantic", b="unknown")
    assert index_tier.task_tier(["a", "b"], by_store) == "unknown"
    assert index_tier.task_tier(["a", "missing"], by_store) == "unknown"


def test_drift_names_the_direction_of_the_change():
    current = {"stores": {"a": {"tier": "syntactic"}}}
    previous = {"stores": {"a": {"tier": "semantic"}}}
    unknown, drift = index_tier.drifted(current, previous)
    assert unknown == []
    assert drift == ["a: semantic -> syntactic"]


def test_a_store_with_no_recorded_tier_is_unknown_not_drifted():
    current = {"stores": {"a": {"tier": "semantic"}, "b": {"tier": "semantic"}}}
    previous = {"stores": {"a": {"tier": "semantic"}}}
    unknown, drift = index_tier.drifted(current, previous)
    assert unknown == ["b"]
    assert drift == []


def test_graphql_endpoint_is_derived_from_the_seam_the_runs_used(monkeypatch):
    monkeypatch.delenv("SG_GRAPHQL_URL", raising=False)
    monkeypatch.setenv("SG_URL", "https://demo.sourcegraph.com/.api/mcp/v1")
    assert index_tier.graphql_endpoint() == "https://demo.sourcegraph.com/.api/graphql"


def test_an_explicit_graphql_url_wins(monkeypatch):
    monkeypatch.setenv("SG_GRAPHQL_URL", "https://elsewhere.example/.api/graphql")
    monkeypatch.setenv("SG_URL", "https://demo.sourcegraph.com/.api/mcp/v1")
    assert index_tier.graphql_endpoint() == "https://elsewhere.example/.api/graphql"
