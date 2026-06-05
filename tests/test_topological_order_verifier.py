"""Tests for topological_order plugin verifier.

Covers:
  1. Correct ordering -> score >= 0.85
  2. Reversed ordering -> score <= 0.10
  3. Partial ordering (some constraints correct) -> 0.15 <= score <= 0.70
  4. Cyclic proposal -> score = 0.0
  5. Alternative valid ordering -> score >= 0.85
  6. Single-node graph -> score = 1.0
  7. Diamond dependency graph with valid ordering
  8. Empty proposal -> score = 0.0
"""

from __future__ import annotations

import pytest

from eb_verify.plugins.topological_order import (
    TopologicalOrderValidator,
    _strip_markup,
    validate_topological_order,
)


# -- Fixtures ----------------------------------------------------------------

# Linear chain: A -> B -> C -> D (A depends on nothing, D depends on C, etc.)
# Correct build order: A, B, C, D
LINEAR_GRAPH = {
    "A": [],
    "B": ["A"],
    "C": ["B"],
    "D": ["C"],
}

# Diamond: A -> B, A -> C, B -> D, C -> D
# Valid orders: [A, B, C, D], [A, C, B, D]
DIAMOND_GRAPH = {
    "A": [],
    "B": ["A"],
    "C": ["A"],
    "D": ["B", "C"],
}

# Multi-root: A and B are independent roots, C depends on both
MULTI_ROOT_GRAPH = {
    "A": [],
    "B": [],
    "C": ["A", "B"],
}

# Larger graph for partial credit testing
# A -> B -> D
# A -> C -> D
# D -> E
LARGER_GRAPH = {
    "A": [],
    "B": ["A"],
    "C": ["A"],
    "D": ["B", "C"],
    "E": ["D"],
}


class TestValidateTopologicalOrder:
    """Unit tests for validate_topological_order scoring function."""

    def test_correct_linear_order(self) -> None:
        """Correct ordering of linear chain scores >= 0.85."""
        result = validate_topological_order(
            proposed_order=["A", "B", "C", "D"],
            dependency_graph=LINEAR_GRAPH,
        )
        assert result["score"] >= 0.85
        assert "valid" in result["detail"].lower() or result["score"] == 1.0

    def test_reversed_linear_order(self) -> None:
        """Completely reversed ordering scores <= 0.10."""
        result = validate_topological_order(
            proposed_order=["D", "C", "B", "A"],
            dependency_graph=LINEAR_GRAPH,
        )
        assert result["score"] <= 0.10

    def test_partial_order(self) -> None:
        """Partially correct ordering gets partial credit."""
        # A is correct, B is correct, but D before C violates C->D
        result = validate_topological_order(
            proposed_order=["A", "B", "D", "C"],
            dependency_graph=LINEAR_GRAPH,
        )
        assert 0.15 <= result["score"] <= 0.70

    def test_cyclic_proposal(self) -> None:
        """Proposal containing a cycle scores 0.0."""
        # The proposal itself lists repos that form a cycle in the declared graph
        cyclic_graph = {
            "A": ["C"],
            "B": ["A"],
            "C": ["B"],
        }
        result = validate_topological_order(
            proposed_order=["A", "B", "C"],
            dependency_graph=cyclic_graph,
        )
        assert result["score"] == 0.0
        assert "cycl" in result["detail"].lower()

    def test_alternative_valid_ordering_diamond(self) -> None:
        """Alternative valid topological order for diamond graph scores >= 0.85."""
        # Both [A, B, C, D] and [A, C, B, D] are valid
        result1 = validate_topological_order(
            proposed_order=["A", "B", "C", "D"],
            dependency_graph=DIAMOND_GRAPH,
        )
        result2 = validate_topological_order(
            proposed_order=["A", "C", "B", "D"],
            dependency_graph=DIAMOND_GRAPH,
        )
        assert result1["score"] >= 0.85
        assert result2["score"] >= 0.85

    def test_single_node(self) -> None:
        """Single-node graph always scores 1.0."""
        result = validate_topological_order(
            proposed_order=["A"],
            dependency_graph={"A": []},
        )
        assert result["score"] == 1.0

    def test_diamond_reversed(self) -> None:
        """Reversed diamond order scores low."""
        result = validate_topological_order(
            proposed_order=["D", "C", "B", "A"],
            dependency_graph=DIAMOND_GRAPH,
        )
        assert result["score"] <= 0.10

    def test_empty_proposal(self) -> None:
        """Empty proposed order scores 0.0."""
        result = validate_topological_order(
            proposed_order=[],
            dependency_graph=LINEAR_GRAPH,
        )
        assert result["score"] == 0.0

    def test_multi_root_valid(self) -> None:
        """Multi-root graph with valid ordering."""
        result = validate_topological_order(
            proposed_order=["A", "B", "C"],
            dependency_graph=MULTI_ROOT_GRAPH,
        )
        assert result["score"] >= 0.85

    def test_multi_root_alternative(self) -> None:
        """Multi-root graph: B before A is also valid."""
        result = validate_topological_order(
            proposed_order=["B", "A", "C"],
            dependency_graph=MULTI_ROOT_GRAPH,
        )
        assert result["score"] >= 0.85

    def test_missing_repos_in_proposal(self) -> None:
        """Proposal missing some repos gets penalized."""
        result = validate_topological_order(
            proposed_order=["A", "B"],
            dependency_graph=LINEAR_GRAPH,
        )
        # Missing C and D — partial at best
        assert result["score"] < 0.85

    def test_extra_repos_in_proposal(self) -> None:
        """Extra repos not in graph are ignored, valid order still scores high."""
        result = validate_topological_order(
            proposed_order=["A", "B", "C", "D", "X"],
            dependency_graph=LINEAR_GRAPH,
        )
        assert result["score"] >= 0.85

    def test_larger_graph_partial(self) -> None:
        """Larger graph with multiple violations gets partial score."""
        # D before B and C violates D->B, D->C; E before D violates E->D
        # Satisfied: B->A (A@0 < B@3), C->A (A@0 < C@4)
        # Violated: D->B (D@1, B@3), D->C (D@1, C@4), E->D (E@2, D@1 — satisfied!)
        # Actually: [A, D, E, B, C]: D@1 needs B,C before it (violated x2), E@2 needs D@1 (satisfied)
        # B->A satisfied, C->A satisfied. Score = 3/5 = 0.6
        result = validate_topological_order(
            proposed_order=["A", "D", "E", "B", "C"],
            dependency_graph=LARGER_GRAPH,
        )
        assert 0.15 <= result["score"] <= 0.70


class TestSlugBasenameEquivalence:
    """Repos are keyed by org/repo slug in ground truth but named by workspace
    basename in agent output. Basename-normalization must make the two forms
    compare equal without loosening topological correctness."""

    # Ground-truth-style graph keyed by slugs (etcd before kubernetes).
    SLUG_GRAPH = {
        "etcd-io/etcd": [],
        "kubernetes/kubernetes": ["etcd-io/etcd"],
    }

    def test_basename_proposal_matches_slug_graph(self) -> None:
        """Agent ordering by basename scores full credit against a slug graph."""
        result = validate_topological_order(
            proposed_order=["etcd", "kubernetes"],
            dependency_graph=self.SLUG_GRAPH,
        )
        assert result["score"] == 1.0

    def test_slug_proposal_still_matches_slug_graph(self) -> None:
        """Agent ordering by slug remains backward-compatible."""
        result = validate_topological_order(
            proposed_order=["etcd-io/etcd", "kubernetes/kubernetes"],
            dependency_graph=self.SLUG_GRAPH,
        )
        assert result["score"] == 1.0

    def test_basename_reversed_still_fails(self) -> None:
        """Normalization must not loosen the correctness check — reversed fails."""
        result = validate_topological_order(
            proposed_order=["kubernetes", "etcd"],
            dependency_graph=self.SLUG_GRAPH,
        )
        assert result["score"] == 0.0

    def test_mixed_forms_match(self) -> None:
        """A mix of slug and basename in the proposal both resolve."""
        result = validate_topological_order(
            proposed_order=["etcd-io/etcd", "kubernetes"],
            dependency_graph=self.SLUG_GRAPH,
        )
        assert result["score"] == 1.0


class TestMarkdownEmphasisStripping:
    """Agents write the numbered ordering with markdown emphasis (`**grpc-go**`),
    inline-code backticks (`` `cobra` ``), or trailing list punctuation (`etcd,`).
    These wrappers must be stripped before the basename match, or a substantively
    correct ordering scores 0.0 (EnterpriseBench-9nb)."""

    SLUG_GRAPH = {
        "etcd-io/etcd": [],
        "kubernetes/kubernetes": ["etcd-io/etcd"],
    }

    @pytest.mark.parametrize(
        ("token", "expected"),
        [
            ("**grpc-go**", "grpc-go"),
            ("`cobra`", "cobra"),
            ("__etcd__", "etcd"),
            ("etcd,", "etcd"),
            ("kubernetes.", "kubernetes"),
            ("**spf13/cobra**", "spf13/cobra"),
            ("spring-boot", "spring-boot"),  # internal hyphen preserved
            ("  `etcd` ", "etcd"),
            ("grpc-go", "grpc-go"),  # bare token untouched
        ],
    )
    def test_strip_markup_cases(self, token: str, expected: str) -> None:
        assert _strip_markup(token) == expected

    def test_bold_proposal_scores_full_credit(self) -> None:
        """A correct ordering wrapped in **bold** must score 1.0, not 0.0."""
        result = validate_topological_order(
            proposed_order=["**etcd**", "**kubernetes**"],
            dependency_graph=self.SLUG_GRAPH,
        )
        assert result["score"] == 1.0

    def test_backtick_proposal_scores_full_credit(self) -> None:
        """A correct ordering wrapped in `code` ticks must score 1.0."""
        result = validate_topological_order(
            proposed_order=["`etcd`", "`kubernetes`"],
            dependency_graph=self.SLUG_GRAPH,
        )
        assert result["score"] == 1.0

    def test_markup_does_not_loosen_correctness(self) -> None:
        """Stripping markup must not rescue a topologically wrong ordering."""
        result = validate_topological_order(
            proposed_order=["**kubernetes**", "**etcd**"],
            dependency_graph=self.SLUG_GRAPH,
        )
        assert result["score"] == 0.0


class TestTopologicalOrderValidatorPlugin:
    """Tests for the plugin interface wrapper."""

    def test_artifact_type(self) -> None:
        validator = TopologicalOrderValidator()
        assert validator.artifact_type == "topological_order"
