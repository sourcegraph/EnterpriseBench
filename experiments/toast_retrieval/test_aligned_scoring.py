"""The repo-qualification asymmetry that cost every arm real credit.

Each case here is drawn from an actual hard-slice report, so a regression in
the matcher shows up as a failing real example rather than a synthetic one.
"""

import pytest

from aligned_scoring import align, found, match_pairs, prefix_drop, score, suffix_match


class TestSuffixMatch:
    @pytest.mark.parametrize(
        "required,retrieved",
        [
            # GT repo-qualified, policy repo-relative -- the failing direction
            # that normalize_path's allowlist corrupts.
            ("otel-go/sdk/trace/batch_span_processor.go", "sdk/trace/batch_span_processor.go"),
            ("envoy/source/common/listener_manager/listener_impl.cc",
             "source/common/listener_manager/listener_impl.cc"),
            ("babel/packages/babel-plugin-transform-typescript/src/index.ts",
             "packages/babel-plugin-transform-typescript/src/index.ts"),
            # Repo name colliding with an inner directory.
            ("axum/axum/src/routing/mod.rs", "axum/src/routing/mod.rs"),
            ("fastapi/fastapi/dependencies/utils.py", "fastapi/dependencies/utils.py"),
        ],
    )
    def test_repo_qualified_gt_matches_repo_relative_retrieval(self, required, retrieved):
        assert suffix_match(required, retrieved)

    def test_the_other_direction_still_matches(self):
        # What EB's own aligner already handled: retrieved longer than GT.
        assert suffix_match("zerver/actions/x.py", "/workspace/zulip/zerver/actions/x.py")

    def test_bare_filename_does_not_match_a_deep_path(self):
        # Otherwise naming "index.ts" would score as retrieving every index.ts.
        assert not suffix_match("packages/babel-core/src/index.ts", "index.ts")

    def test_bare_filename_matches_bare_filename(self):
        assert suffix_match("js.go", "js.go")

    def test_partial_component_is_not_a_match(self):
        # "processor.go" must not match ".../batch_span_processor.go".
        assert not suffix_match("sdk/trace/batch_span_processor.go", "trace/processor.go")

    def test_unrelated_paths_do_not_match(self):
        assert not suffix_match("pkg/bpf/collection.go", "pkg/datapath/loader.go")

    def test_empty_paths_never_match(self):
        assert not suffix_match("", "sdk/trace/x.go")


class TestAlign:
    def test_matched_path_is_rewritten_to_the_required_string(self):
        required = ["otel-go/sdk/trace/batch_span_processor.go"]
        assert align(["sdk/trace/batch_span_processor.go"], required) == required

    def test_unmatched_paths_keep_their_own_form(self):
        out = align(["docs/readme.md"], ["otel-go/sdk/trace/x.go"])
        assert out == ["docs/readme.md"]

    def test_order_is_preserved(self):
        required = ["envoy/source/a.cc", "envoy/source/b.cc"]
        out = align(["source/b.cc", "source/a.cc"], required)
        assert out == ["envoy/source/b.cc", "envoy/source/a.cc"]

    def test_duplicates_collapse_after_alignment(self):
        required = ["axum/axum/src/routing/mod.rs"]
        out = align(["axum/src/routing/mod.rs", "axum/axum/src/routing/mod.rs"], required)
        assert out == required


class TestScore:
    def test_correct_retrieval_no_longer_scores_as_a_miss(self):
        """The regression this module exists for: real report, real paths."""
        required = [
            "otel-go/sdk/trace/batch_span_processor.go",
            "jaeger/cmd/collector/app/handler/grpc_handler.go",
        ]
        retrieved = [
            "sdk/trace/batch_span_processor.go",
            "cmd/collector/app/handler/grpc_handler.go",
        ]
        scores = score(retrieved, required, "error-prov-dual-otel-jaeger-001", "test")
        assert scores.file_recall == 1.0

    def test_a_genuine_miss_still_scores_zero(self):
        scores = score(["docs/readme.md"], ["otel-go/sdk/trace/x.go"], "t", "test")
        assert scores.file_recall == 0.0


class TestBoundedPrefixDrop:
    """Real false credits from the sweeps, each one a coincidence not a match."""

    def test_vendored_copy_in_another_repo_is_not_the_required_file(self):
        # gatekeeper vendors OPA; the required file is OPA's own.
        assert not suffix_match(
            "ast/parser.go",
            "constraint/vendor/github.com/open-policy-agent/opa/v1/ast/parser.go",
        )

    def test_seven_deep_java_path_is_not_matched_by_two_components(self):
        assert not suffix_match(
            "spring-kafka/src/main/java/org/springframework/kafka/listener/"
            "ConcurrentMessageListenerContainer.java",
            "listener/ConcurrentMessageListenerContainer.java",
        )

    def test_repo_name_plus_one_source_root_still_matches(self):
        # Flask's real path, cited without src/flask/. Two dropped components
        # is the documented ceiling, not an accident.
        assert prefix_drop("src/flask/json/__init__.py", "json/__init__.py") == 2

    def test_identical_paths_drop_nothing(self):
        assert prefix_drop("pkg/bpf/collection.go", "pkg/bpf/collection.go") == 0


class TestOneToOneAssignment:
    """config-drift-dual-setuptools-pip-001: one citation, two candidates."""

    REQUIRED = [
        "setuptools/pkg_resources/__init__.py",
        "pip/src/pip/_vendor/pkg_resources/__init__.py",
    ]

    def test_one_citation_credits_one_required_file(self):
        assert found(["pkg_resources/__init__.py"], self.REQUIRED) == {
            "setuptools/pkg_resources/__init__.py"
        }

    def test_two_citations_credit_both_when_both_are_named(self):
        got = ["setuptools/pkg_resources/__init__.py", "pip/src/pip/_vendor/pkg_resources/__init__.py"]
        assert found(got, self.REQUIRED) == set(self.REQUIRED)

    def test_exact_match_wins_over_a_qualifier_drop(self):
        required = ["axum/axum/src/routing/mod.rs", "src/routing/mod.rs"]
        pairs = match_pairs(["src/routing/mod.rs"], required)
        assert pairs == {"src/routing/mod.rs": "src/routing/mod.rs"}

    def test_found_and_score_agree_on_the_same_input(self):
        """The complementarity table and the recall table must not diverge."""
        got = ["pkg_resources/__init__.py"]
        scores = score(got, self.REQUIRED, "config-drift-dual-setuptools-pip-001", "test")
        assert scores.file_recall == len(found(got, self.REQUIRED)) / len(self.REQUIRED)

    def test_duplicate_citations_of_one_file_are_not_two_results(self):
        required = ["axum/axum/src/routing/mod.rs"]
        got = ["axum/src/routing/mod.rs", "axum/axum/src/routing/mod.rs"]
        assert align(got, required) == required
