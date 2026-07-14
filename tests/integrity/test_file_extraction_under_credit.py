"""file_extraction under-credit vectors (ssikq reject-cycle #2).

Scope note: unlike the rest of this corpus, these vectors are NOT about
scorer_guard/INFRA_SENTINEL routing. They close a different score-integrity
class the README also covers: a verifier that silently under-credits (or
zeros) a genuinely correct, spec-compliant agent answer, booking a harness
bug as an agent failure. Each vector below asserts an infra error is absent
and the score is the correct 1.0, not a number produced by the bug.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIB_DIR = REPO_ROOT / "lib"


def run_cli(answer_file, gt_file, keys):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(LIB_DIR)
    env["ANSWER_FILE"] = str(answer_file)
    env["GT_FILE"] = str(gt_file)
    proc = subprocess.run(
        [sys.executable, "-m", "eb_verify.plugins.file_extraction", "--keys", keys],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
    )
    return proc


def write_json(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload))
    return path


def gt_with(tmp_path: Path, paths) -> Path:
    return write_json(
        tmp_path / "ground_truth.json",
        {"required_files": [{"path": p, "repo": p.split("/")[0]} for p in paths]},
    )


DEFAULT_KEYS = "source_files,files,error_source.files,code_paths,citations"


def test_first_key_wins_discards_a_correct_answer(tmp_path):
    """BUG 1 (rejection_reason_addendum): a wrong guess under an earlier key
    must not discard a fully correct answer sitting under a later, advertised
    key. Pre-fix, agent_files() returned on the first non-empty key and this
    scored 0.0 even though a complete correct answer was present in the JSON.
    """
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
    """BUG 2: run_task.py's output appendix mandates `code_paths` for every
    task, and task.toml's require_grounded_citations=true additionally
    mandates a top-level `citations` list. An agent that follows the harness's
    own instructions to the letter must not score 0.0 because --keys omitted
    the keys the harness told it to use.
    """
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
    """BUG 3: agents commonly cite an exact line alongside the evidence span
    ('httpx/_config.py:120' or '#L120'). Before components() stripped the
    suffix, this citation-style path failed to match its own file's ground
    truth entry and the answer scored 0.0 despite naming the right file.
    """
    gt = gt_with(tmp_path, ["httpx/httpx/_config.py"])
    for suffix in (":120", "#L120"):
        answer = write_json(tmp_path / "answer.json",
                            {"source_files": [f"httpx/_config.py{suffix}"]})
        proc = run_cli(answer, gt, DEFAULT_KEYS)
        payload = json.loads(proc.stdout)
        assert payload["score"] == 1.0, f"suffix {suffix!r}: {payload['detail']}"
