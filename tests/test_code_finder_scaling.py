"""Pin the arm contract of the frozen Code Finder scaling dataset.

The statistics that consume it are tested in test_paired_stats.py.
"""
import json
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
EXP = os.path.join(os.path.dirname(HERE), "experiments/code_finder_scaling")
DATASET = os.path.join(EXP, "dataset.json")


@pytest.mark.skipif(not os.path.exists(DATASET), reason="dataset not frozen")
def test_the_frozen_dataset_keeps_the_arms_distinguishable():
    """The arm contract is what makes the comparison mean anything.

    baseline reaches no MCP at all; mcp_only has the server but not the tool
    under test; only the finder arm calls code_finder. If any of these collapse,
    the measured delta is not attributable to Code Finder.
    """
    doc = json.load(open(DATASET))
    for task, arms in doc["arm_a"]["tasks"].items():
        assert arms["baseline"]["mcp_calls"] == 0, task
        assert arms["mcp_only"]["mcp_calls"] > 0, task
        assert arms["mcp_only"]["code_finder_calls"] == 0, task
        assert arms["mcp_code_finder"]["code_finder_calls"] > 0, task


@pytest.mark.skipif(not os.path.exists(DATASET), reason="dataset not frozen")
def test_arm_b_context_tokens_exceed_the_cache_read_component():
    """Context tokens are the sum, not one term of it, so the identity must hold."""
    doc = json.load(open(DATASET))
    for task, arms in doc["arm_b"]["tasks"].items():
        for mode, rec in arms.items():
            assert rec["context_tokens"] >= rec["cache_read"] + rec["cache_creation"], \
                f"{task}/{mode}"
