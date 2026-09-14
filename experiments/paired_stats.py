"""Paired statistics shared by the experiment writeups.

One implementation, because two writeups quote overlapping numbers and a second
copy is how they start to disagree.

Scores are compared as absolute differences: a task score is already on a fixed
0-1 scale, so a ratio of two scores means nothing. Resources (tokens, dollars,
calls, seconds) are compared as geometric means of per-task log ratios, because
spend spans an order of magnitude across tasks and an arithmetic mean would be
whatever the most expensive task did.
"""
import math
import statistics as st


def _betacf(a, b, x):
    """Continued fraction for the incomplete beta function (Lentz's method)."""
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        c = 1.0 + aa / c
        if abs(d) < tiny:
            d = tiny
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        c = 1.0 + aa / c
        if abs(d) < tiny:
            d = tiny
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-16:
            return h
    raise ArithmeticError(f"incomplete beta did not converge at a={a}, b={b}, x={x}")


def _betai(a, b, x):
    """Regularized incomplete beta I_x(a, b)."""
    if not 0.0 <= x <= 1.0:
        raise ValueError(f"x out of range: {x}")
    if x in (0.0, 1.0):
        return x
    lead = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                    + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return lead * _betacf(a, b, x) / a
    return 1.0 - lead * _betacf(b, a, 1.0 - x) / b


def t_two_sided(df, alpha=0.05):
    """Critical t for a two-sided interval. Computed, not looked up in a table.

    A table has to guess at any sample size it does not list, and the guess is
    always in the direction of an interval that is too narrow.
    """
    if df < 1:
        raise ValueError("t is undefined below 1 degree of freedom")
    lo, hi = 0.0, 400.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        # P(|T| > mid) for df degrees of freedom
        p = _betai(df / 2.0, 0.5, df / (df + mid * mid))
        if p > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def diff_ci(values, alpha=0.05):
    """Mean and CI of paired absolute differences."""
    n = len(values)
    if n == 0:
        raise ValueError("no pairs")
    mu = st.mean(values)
    half = t_two_sided(n - 1, alpha) * st.stdev(values) / math.sqrt(n) if n > 1 else 0.0
    return mu, mu - half, mu + half


def ratio_ci(numerators, denominators, alpha=0.05):
    """Geometric mean and CI of paired ratios, computed in log space.

    Pairs where either side is non-positive are dropped: a zero denominator has
    no ratio, and a zero numerator has no log. The returned n says how many
    pairs actually contributed.
    """
    logs = [math.log(a / b) for a, b in zip(numerators, denominators) if a > 0 and b > 0]
    n = len(logs)
    if n == 0:
        raise ValueError("no positive pairs")
    mu = st.mean(logs)
    half = t_two_sided(n - 1, alpha) * st.stdev(logs) / math.sqrt(n) if n > 1 else 0.0
    return math.exp(mu), math.exp(mu - half), math.exp(mu + half), n


def proportion_ci(successes, total, alpha=0.05):
    """Wilson score interval for a proportion.

    The normal approximation is unusable here: at 0 successes it returns the
    degenerate interval [0, 0], which would report a rate we cannot rule out as
    a rate we have excluded.
    """
    if total <= 0:
        raise ValueError("no trials")
    z = 1.959963984540054 if alpha == 0.05 else t_two_sided(10 ** 6, alpha)
    p = successes / total
    d = 1.0 + z * z / total
    centre = (p + z * z / (2 * total)) / d
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / d
    return p, max(0.0, centre - half), min(1.0, centre + half)


def loglog_fit(xs, ys):
    """Slope and r of log(y) on log(x). Slope 0 = flat cost, 1 = proportional.

    A perfectly constant y is the limit this analysis is looking for, and its
    correlation is undefined rather than zero. Report it as nan so it prints as
    nan instead of quietly becoming a number the reader would trust.
    """
    lx = [math.log(v) for v in xs]
    ly = [math.log(v) for v in ys]
    mx, my = st.mean(lx), st.mean(ly)
    sxy = sum((a - mx) * (b - my) for a, b in zip(lx, ly))
    sxx = sum((a - mx) ** 2 for a in lx)
    syy = sum((b - my) ** 2 for b in ly)
    if sxx == 0:
        raise ValueError("the x series is constant; slope is undefined")
    return sxy / sxx, (sxy / math.sqrt(sxx * syy) if syy > 0 else float("nan"))


def cv(values):
    return st.stdev(values) / st.mean(values)
