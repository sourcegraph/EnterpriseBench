"""An unindexed store must make the code-intel run invalid, never a zero."""

from __future__ import annotations

import subprocess
from pathlib import Path

import preflight_index


def _store(status: str) -> dict:
    return {"store_identifier": "s", "status": status, "probes": []}


def test_indexed_stores_block_nothing():
    entries = [{"task_id": "t1", "store_identifiers": ["a", "b"]}]
    by_store = {"a": _store("indexed"), "b": _store("indexed")}
    assert preflight_index.ineligible(entries, by_store) == []


def test_one_unindexed_store_blocks_the_whole_task():
    """A multi-repo task is only navigable if EVERY repo it spans is indexed."""
    entries = [{"task_id": "t1", "store_identifiers": ["a", "b"]}]
    by_store = {"a": _store("indexed"), "b": _store("unindexed")}
    blocked = preflight_index.ineligible(entries, by_store)
    assert blocked == [{"task_id": "t1", "stores": {"b": "unindexed"}}]


def test_undetermined_and_missing_clone_block_too():
    """Absence of evidence still bars a score; only 'indexed' clears a task."""
    entries = [
        {"task_id": "probe", "store_identifiers": ["a"]},
        {"task_id": "clone", "store_identifiers": ["b"]},
    ]
    by_store = {"a": _store("no_probe"), "b": _store("no_clone")}
    blocked = {e["task_id"]: e["stores"] for e in preflight_index.ineligible(entries, by_store)}
    assert blocked == {"probe": {"a": "no_probe"}, "clone": {"b": "no_clone"}}


def test_candidates_are_column_zero_definitions(tmp_path: Path):
    """An indented method is skipped: its receiver would make the probe ambiguous."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "mod.py").write_text(
        "class Widget:\n    def indented_method(self):\n        pass\n"
    )
    (tmp_path / "other.py").write_text("def top_level_helper():\n    pass\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    found = dict(preflight_index.candidates(tmp_path, limit=10))
    assert found == {"mod.py": "Widget", "other.py": "top_level_helper"}


def test_candidates_skip_test_and_vendor_paths(tmp_path: Path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "dep.py").write_text("def vendored_thing():\n    pass\n")
    (tmp_path / "test_mod.py").write_text("def test_thing():\n    pass\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    assert preflight_index.candidates(tmp_path, limit=10) == []
