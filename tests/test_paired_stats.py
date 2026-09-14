"""Pin the statistics behind the published Code Finder numbers.

These functions produce figures that go into external material, so the failure
mode that matters is a silently wrong interval, not a crash.
"""
import math
import os
import statistics as st
import sys

import pytest

EXPERIMENTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "experiments")
sys.path.insert(0, EXPERIMENTS)

pytestmark = pytest.mark.skipif(
    not os.path.exists(os.path.join(EXPERIMENTS, "paired_stats.py")),
    reason="experiments/paired_stats.py not present")


def _mod():
    import paired_stats
    return paired_stats


# Two-sided t at alpha=0.05, from a published table, keyed by degrees of freedom.
# An earlier hand-written table in this repo was off by one df between 6 and 10,
# which narrowed every interval in the primary arm by about 1.5%.
T_TABLE = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
           7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 16: 2.120, 18: 2.101,
           29: 2.045, 45: 2.014, 100: 1.984}


@pytest.mark.parametrize("df,expected", sorted(T_TABLE.items()))
def test_t_matches_the_published_table_at_every_sample_size_we_publish(df, expected):
    assert _mod().t_two_sided(df) == pytest.approx(expected, abs=5e-4)


def test_t_is_computed_for_sample_sizes_no_table_would_list():
    """A table has to guess off the end; this must not."""
    p = _mod()
    assert p.t_two_sided(9) > p.t_two_sided(29) > p.t_two_sided(45) > 1.96
    assert p.t_two_sided(10 ** 6) == pytest.approx(1.96, abs=1e-3)
    with pytest.raises(ValueError):
        p.t_two_sided(0)


def test_ratio_ci_is_a_geometric_mean_not_an_arithmetic_one():
    """One order-of-magnitude task must not drag the central estimate."""
    g, lo, hi, n = _mod().ratio_ci([1, 1, 1, 100], [1, 1, 1, 1])
    assert n == 4
    # geometric mean of (1,1,1,100) is 100**0.25 = 3.162; arithmetic is 25.75.
    assert g == pytest.approx(100 ** 0.25, rel=1e-9)
    assert lo < g < hi


def test_ratio_ci_drops_nonpositive_pairs_rather_than_taking_log_of_zero():
    p = _mod()
    g, _, _, n = p.ratio_ci([2, 0, 2], [1, 1, 1])
    assert n == 2
    assert g == pytest.approx(2.0)
    with pytest.raises(ValueError):
        p.ratio_ci([0], [1])


def test_small_samples_use_the_t_multiplier_not_the_normal_approximation():
    """At n=10 the normal approximation would understate the interval by ~15%."""
    values = [0.1 * i for i in range(10)]
    _, lo, hi = _mod().diff_ci(values)
    half = (hi - lo) / 2
    assert half == pytest.approx(2.262 * st.stdev(values) / math.sqrt(10), rel=1e-3)
    assert half > 1.96 * st.stdev(values) / math.sqrt(10)


def test_diff_ci_on_a_single_pair_has_zero_width_rather_than_raising():
    assert _mod().diff_ci([0.4]) == (0.4, 0.4, 0.4)


def test_proportion_ci_at_zero_successes_does_not_claim_a_rate_of_exactly_zero():
    """The normal approximation collapses to [0, 0] here and would overclaim."""
    p, lo, hi = _mod().proportion_ci(0, 30)
    assert p == 0.0
    assert lo == 0.0
    assert 0.05 < hi < 0.20, hi


def test_proportion_ci_brackets_the_point_estimate():
    p, lo, hi = _mod().proportion_ci(10, 30)
    assert p == pytest.approx(1 / 3)
    assert lo < p < hi
    with pytest.raises(ValueError):
        _mod().proportion_ci(0, 0)


def test_loglog_slope_separates_flat_cost_from_proportional_cost():
    p = _mod()
    xs = [1e5, 1e6, 1e7]
    flat_slope, flat_r = p.loglog_fit(xs, [2e5, 2e5, 2e5])
    assert flat_slope == pytest.approx(0.0, abs=1e-9)
    assert math.isnan(flat_r), "correlation of a constant series is undefined, not 0"
    with pytest.raises(ValueError):
        p.loglog_fit([1e5, 1e5], [1e5, 2e5])
    prop_slope, prop_r = p.loglog_fit(xs, [x / 2 for x in xs])
    assert prop_slope == pytest.approx(1.0, rel=1e-9)
    assert prop_r == pytest.approx(1.0, rel=1e-9)
