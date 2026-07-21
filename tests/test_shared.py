"""Tests for scripts/lib/shared.py."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib.shared import MODE_SUFFIXES, VALID_MODES, strip_mode_suffix


class TestValidModes:
    def test_cli_is_a_valid_mode(self) -> None:
        assert "cli" in VALID_MODES

    def test_every_non_baseline_mode_has_a_suffix(self) -> None:
        """Every arm but baseline (the no-suffix default) needs a MODE_SUFFIXES entry."""
        for mode in VALID_MODES:
            if mode == "baseline":
                continue
            assert f"_{mode}" in MODE_SUFFIXES, f"missing suffix for arm {mode!r}"


class TestStripModeSuffix:
    def test_cli_suffix(self) -> None:
        task_id, mode = strip_mode_suffix("dep-traversal-001_cli")
        assert task_id == "dep-traversal-001"
        assert mode == "cli"

    def test_hybrid_suffix(self) -> None:
        task_id, mode = strip_mode_suffix("cal-drift-001_hybrid")
        assert task_id == "cal-drift-001"
        assert mode == "hybrid"

    def test_mcp_only_suffix(self) -> None:
        task_id, mode = strip_mode_suffix("cal-drift-001_mcp_only")
        assert task_id == "cal-drift-001"
        assert mode == "mcp_only"

    def test_no_suffix_defaults_to_baseline(self) -> None:
        task_id, mode = strip_mode_suffix("cal-drift-001")
        assert task_id == "cal-drift-001"
        assert mode == "baseline"
