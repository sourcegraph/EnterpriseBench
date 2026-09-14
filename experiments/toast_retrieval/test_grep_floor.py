"""Unit tests for the grep-effort probe's pure parts.

The Sourcegraph query path is exercised by running the probe, not mocked
here; what needs pinning is the literal extractor (it decides how hard the
probe tries) and the path matcher (it decides what counts as reached).
"""

from grep_floor import _MIN_LITERAL, _matches_required, literals


def test_literals_lifts_backticks_quotes_and_identifiers():
    text = 'Trace `handle_request` through "ProxyError" and rego.ast parsing.'
    found = literals(text)
    assert "handle_request" in found
    assert "ProxyError" in found
    assert "rego.ast" in found


def test_literals_drops_short_terms_and_plain_prose():
    assert literals("the api is down and we do not know why") == []


def test_literals_are_deduped_and_meet_the_length_floor():
    found = literals("`retry_policy` and `retry_policy` again, plus `abc`")
    assert found.count("retry_policy") == 1
    assert all(len(term) >= _MIN_LITERAL for term in found)


def test_matches_required_handles_repo_qualified_ground_truth():
    assert _matches_required("ast/parser.go", "ast/parser.go")
    # index path is repo-relative, ground truth repo-qualified
    assert _matches_required("ast/parser.go", "opa/ast/parser.go")
    # and the reverse skew, which parts of the corpus also have
    assert _matches_required("opa/ast/parser.go", "ast/parser.go")


def test_matches_required_rejects_a_bare_basename_collision():
    assert not _matches_required("b/parser.go", "a/parser.go")
