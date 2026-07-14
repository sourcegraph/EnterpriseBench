"""file_extraction under-credit vectors.

The under-credit half of this corpus: a verifier that zeros a correct,
spec-compliant answer books a harness bug as an agent failure. Each vector
asserts the correct 1.0 against an answer that a buggy verifier scored 0.0.
Run as an un-skippable gate (CI), separately from the marker-filtered suite.
"""

from __future__ import annotations

import json

from tests.test_file_extraction import gt_with, run_cli, write_json

DEFAULT_KEYS = "source_files,files,error_source.files,code_paths,citations"


def test_first_key_wins_discards_a_correct_answer(tmp_path):
    """A wrong guess under an earlier key must not discard a correct answer under
    a later one. First-key-wins scored this 0.0 with the full answer in the JSON."""
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py", "httpcore/httpcore/_client.py"])
    answer = write_json(tmp_path / "answer.json", {
        "source_files": ["totally/unrelated.py"],
        "files": ["httpx/_config.py", "httpcore/_client.py"],
    })
    proc = run_cli(answer, gt, DEFAULT_KEYS)
    payload = json.loads(proc.stdout)
    assert proc.returncode == 0
    assert payload["score"] == 1.0, payload["detail"]
    assert payload["passed"] is True


def test_omitted_mandated_keys_zero_a_spec_compliant_answer(tmp_path):
    """run_task.py's appendix mandates `code_paths`, and require_grounded_citations
    additionally mandates `citations`. An agent following those instructions to the
    letter must not score 0.0 because --keys omitted the keys it was told to use."""
    gt = gt_with(tmp_path, ["httpx/httpx/_transports/default.py"])
    code_paths_answer = write_json(tmp_path / "answer.json", {
        "code_paths": [{"path": "/workspace/httpx/httpx/_transports/default.py"}],
    })
    proc = run_cli(code_paths_answer, gt, DEFAULT_KEYS)
    payload = json.loads(proc.stdout)
    assert payload["score"] == 1.0, payload["detail"]

    citations_answer = write_json(tmp_path / "answer.json", {
        "citations": [{
            "repo": "httpx",
            "file": "httpx/_transports/default.py",
            "evidence_span": "class HTTPTransport(BaseTransport):",
        }],
    })
    proc = run_cli(citations_answer, gt, DEFAULT_KEYS)
    payload = json.loads(proc.stdout)
    assert payload["score"] == 1.0, payload["detail"]


def test_unstripped_citation_suffix_breaks_a_match(tmp_path):
    """Agents cite an exact line alongside the evidence span ('_config.py:120',
    '#L120'). Unstripped, the suffix fails the match and zeros a right answer."""
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py"])
    for suffix in (":120", "#L120"):
        answer = write_json(tmp_path / "answer.json",
                            {"source_files": [f"httpx/_config.py{suffix}"]})
        proc = run_cli(answer, gt, DEFAULT_KEYS)
        payload = json.loads(proc.stdout)
        assert payload["score"] == 1.0, f"suffix {suffix!r}: {payload['detail']}"
