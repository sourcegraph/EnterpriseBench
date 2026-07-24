"""A retired OAuth account must not block infra checks or receive dispatch.

Account 2's credentials are gone for good. Its `~/.claude-homes/account2`
directory still exists (it holds unrelated state), and account discovery is
purely "any accountN directory", so every `check_infra.py` run reported a
BLOCKED critical failure for an account nobody can log into again — the exact
shape of gate that gets ignored, and an ignored gate stops catching the real
expired-token case it exists for.

Retirement is declared in `configs/retired_accounts.json` so it is
version-controlled and reversible, and honored in both places that matter:
discovery (health checks) and account-spec expansion (dispatch).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "infra"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from account_health import (  # noqa: E402
    discover_managed_account_homes,
    load_retired_accounts,
)
from run_benchmark import parse_accounts  # noqa: E402

RETIRED_CONFIG = REPO_ROOT / "configs" / "retired_accounts.json"


def _make_homes(tmp_path: Path, numbers: list[int]) -> Path:
    home = tmp_path / "home"
    (home / ".claude-homes").mkdir(parents=True)
    for n in numbers:
        (home / ".claude-homes" / f"account{n}").mkdir()
    return home


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def test_config_declares_account_2_retired():
    data = json.loads(RETIRED_CONFIG.read_text())
    assert 2 in data["retired"]
    # A retirement without a reason is indistinguishable from a typo.
    assert data["reasons"]["2"].strip()


def test_load_retired_accounts_reads_the_repo_config():
    assert 2 in load_retired_accounts()


def test_missing_config_retires_nothing(tmp_path):
    assert load_retired_accounts(tmp_path / "absent.json") == frozenset()


def test_malformed_config_is_not_silently_empty(tmp_path):
    """A broken config must fail loudly, not quietly re-enable a dead account."""
    bad = tmp_path / "retired_accounts.json"
    bad.write_text("{not json")
    try:
        load_retired_accounts(bad)
    except ValueError as exc:
        assert "retired_accounts" in str(exc)
    else:
        raise AssertionError("malformed retired-account config must raise")


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def test_discovery_skips_retired_accounts(tmp_path):
    home = _make_homes(tmp_path, [1, 2, 3])
    names = [p.name for p in discover_managed_account_homes(home, retired={2})]
    assert names == ["account1", "account3"]


def test_discovery_without_retirement_is_unchanged(tmp_path):
    home = _make_homes(tmp_path, [1, 2, 3])
    names = [p.name for p in discover_managed_account_homes(home, retired=frozenset())]
    assert names == ["account1", "account2", "account3"]


def test_discovery_keeps_numeric_order_after_a_gap(tmp_path):
    home = _make_homes(tmp_path, [1, 2, 3, 4, 5, 10])
    names = [p.name for p in discover_managed_account_homes(home, retired={2})]
    assert names == ["account1", "account3", "account4", "account5", "account10"]


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


def test_range_spec_drops_the_retired_account():
    assert parse_accounts("1-5", retired={2}) == [1, 3, 4, 5]


def test_explicit_list_drops_the_retired_account():
    assert parse_accounts("1,2,3", retired={2}) == [1, 3]


def test_asking_only_for_a_retired_account_is_an_error():
    """Silently returning [] would look like "no --account given" and dispatch
    every task to the default account."""
    try:
        parse_accounts("2", retired={2})
    except ValueError as exc:
        assert "retired" in str(exc).lower()
    else:
        raise AssertionError("a spec naming only retired accounts must raise")


def test_parse_accounts_defaults_to_the_repo_config():
    assert 2 not in parse_accounts("1-5")


def test_empty_spec_still_returns_empty():
    assert parse_accounts(None) == []
