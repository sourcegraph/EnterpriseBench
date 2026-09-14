"""Every number in the Code Finder scaling writeup, recomputed from dataset.json.

Paired statistics throughout: absolute differences for scores (a score is already
on a fixed 0-1 scale, so a ratio would be meaningless), geometric means of
per-task log ratios for resources (token spend spans an order of magnitude across
tasks, so an arithmetic mean would be dominated by the largest task).
"""
import json, math, os, statistics as st, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paired_stats import cv, diff_ci, loglog_fit, ratio_ci  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATASET = os.path.join(HERE, "dataset.json")


def _contrast(tasks, arms, a, b, keys):
    print(f"  {a} vs {b}")
    mu, lo, hi = diff_ci([arms[t][a]["score"] - arms[t][b]["score"] for t in tasks])
    print(f"    score  {mu:+.3f} [{lo:+.3f}, {hi:+.3f}]")
    for key, label in keys:
        g, lo, hi, n = ratio_ci([arms[t][a][key] for t in tasks],
                                [arms[t][b][key] for t in tasks])
        print(f"    {label:7s} {g:.3f}x [{lo:.3f}, {hi:.3f}]  n={n}")


def arm_a(doc):
    d = doc["arm_a"]
    arms = d["tasks"]
    tasks = sorted(arms, key=lambda t: -arms[t]["baseline"]["input_tokens"])
    print(f"ARM A  {d['harness']} / {d['model']} / {d['corpus']}")
    print(f"       {len(tasks)} tasks x 3 arms x {len(d['reps'])} reps = {d['runs']} runs, "
          f"{d['invalid']} invalid")
    print(f"       selection (pre-registered): {json.dumps(d['selection'])}")
    print()
    print(f"  {'task':52s} {'baseline':>10s} {'mcp_only':>10s} {'finder':>9s} {'f/b':>6s} "
          f"{'b.score':>8s} {'f.score':>8s}")
    for t in tasks:
        b, o, c = (arms[t][m]["input_tokens"] for m in ("baseline", "mcp_only", "mcp_code_finder"))
        print(f"  {t:52s} {b:10,.0f} {o:10,.0f} {c:9,.0f} {c / b:6.2f} "
              f"{arms[t]['baseline']['score']:8.3f} {arms[t]['mcp_code_finder']['score']:8.3f}")
    print()
    print("  arm means (per task)")
    for m in ("baseline", "mcp_only", "mcp_code_finder"):
        v = lambda k: st.mean([arms[t][m][k] for t in tasks])
        print(f"    {m:16s} score {v('score'):.3f}  in {v('input_tokens'):10,.0f}  "
              f"out {v('output_tokens'):7,.0f}  calls {v('tool_calls'):5.1f}  "
              f"mcp {v('mcp_calls'):5.1f}  cf {v('code_finder_calls'):4.1f}  "
              f"wall {v('wall_sec'):4.0f}s")
    print()
    keys = [("input_tokens", "input"), ("output_tokens", "output"),
            ("tool_calls", "calls"), ("wall_sec", "wall")]
    _contrast(tasks, arms, "mcp_code_finder", "baseline", keys)
    _contrast(tasks, arms, "mcp_code_finder", "mcp_only", keys)
    _contrast(tasks, arms, "mcp_only", "baseline", keys)
    print()
    bs = [arms[t]["baseline"]["input_tokens"] for t in tasks]
    fs = [arms[t]["mcp_code_finder"]["input_tokens"] for t in tasks]
    slope, r = loglog_fit(bs, fs)
    print(f"  mechanism: finder_input ~ baseline_input^{slope:.3f}  (r={r:+.3f})")
    print(f"    baseline spread {min(bs):,.0f}-{max(bs):,.0f}  CV {cv(bs):.2f}")
    print(f"    finder   spread {min(fs):,.0f}-{max(fs):,.0f}  CV {cv(fs):.2f}")
    return tasks, arms


def arm_b(doc):
    d = doc["arm_b"]
    arms = d["tasks"]
    tasks = sorted(arms, key=lambda t: -arms[t]["baseline"]["context_tokens"])
    print()
    print(f"ARM B  {d['harness']} / {d['model']} / {d['corpus']}")
    print(f"       {len(tasks)} tasks x 2 arms x {len(d['reps'])} rep = {len(tasks) * 2} runs")
    print(f"       {d['selection']['note']}")
    print()
    bs = [arms[t]["baseline"]["context_tokens"] for t in tasks]
    fs = [arms[t]["mcp_code_finder"]["context_tokens"] for t in tasks]
    slope, r = loglog_fit(bs, fs)
    print(f"  mechanism: finder_context ~ baseline_context^{slope:.3f}  (r={r:+.3f})")
    print(f"    baseline spread {min(bs):,.0f}-{max(bs):,.0f}  CV {cv(bs):.2f}")
    print(f"    finder   spread {min(fs):,.0f}-{max(fs):,.0f}  CV {cv(fs):.2f}")
    print()
    bands = [(0, 4e5, "< 400k"), (4e5, 7e5, "400k - 700k"),
             (7e5, 1.2e6, "700k - 1.2M"), (1.2e6, float("inf"), ">= 1.2M")]
    print(f"  {'baseline context spend':24s} {'n':>3s} {'tokens':>24s} {'USD':>24s} {'score':>24s}")
    for lo, hi, label in bands + [(0, float("inf"), "ALL (unselected)")]:
        sel = [t for t in tasks if lo <= arms[t]["baseline"]["context_tokens"] < hi]
        if len(sel) < 3:
            print(f"  {label:24s} {len(sel):3d}  (too few to report)")
            continue
        g = ratio_ci([arms[t]["mcp_code_finder"]["context_tokens"] for t in sel],
                     [arms[t]["baseline"]["context_tokens"] for t in sel])
        c = ratio_ci([arms[t]["mcp_code_finder"]["cost_usd"] for t in sel],
                     [arms[t]["baseline"]["cost_usd"] for t in sel])
        s = diff_ci([arms[t]["mcp_code_finder"]["score"] - arms[t]["baseline"]["score"]
                     for t in sel])
        print(f"  {label:24s} {len(sel):3d}  {g[0]:6.3f}x [{g[1]:.3f}, {g[2]:.3f}]  "
              f"{c[0]:6.3f}x [{c[1]:.3f}, {c[2]:.3f}]  "
              f"{s[0]:+.3f} [{s[1]:+.3f}, {s[2]:+.3f}]")
    print()
    print("  why the dollar ratio is milder than the token ratio: Sonnet prompt caching")
    cr = st.mean([arms[t]["baseline"]["cache_read"] for t in tasks])
    ct = st.mean([arms[t]["baseline"]["context_tokens"] for t in tasks])
    print(f"    baseline context is {cr / ct:.1%} cache reads, billed at 10% of input")
    return tasks, arms


def main():
    doc = json.load(open(DATASET))
    arm_a(doc)
    arm_b(doc)


if __name__ == "__main__":
    main()
