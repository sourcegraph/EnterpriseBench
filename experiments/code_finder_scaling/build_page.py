"""Render the evidence page for the Code Finder scaling claim from dataset.json.

The page is generated rather than hand-written so every published figure comes
from the frozen dataset and cannot drift from the runs behind it.
"""
import json, math, os, statistics as st
import analyze

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("CF_PAGE_OUT", os.path.join(HERE, "where-code-finder-pays.html"))
DOC = json.load(open(os.path.join(HERE, "dataset.json")))

A, B = DOC["arm_a"], DOC["arm_b"]
mean = lambda x: sum(x) / len(x)

a_tasks = sorted(A["tasks"], key=lambda t: -A["tasks"][t]["baseline"]["input_tokens"])
b_tasks = sorted(B["tasks"], key=lambda t: -B["tasks"][t]["baseline"]["context_tokens"])


def fmt_ratio(g, lo, hi):
    return f'{g:.3f}<span class="x">&times;</span>', f"{lo:.3f} – {hi:.3f}"


def contrast(a, b, key):
    t = a_tasks
    return analyze.ratio_ci([A["tasks"][x][a][key] for x in t],
                            [A["tasks"][x][b][key] for x in t])


def score_delta(a, b):
    return analyze.diff_ci([A["tasks"][t][a]["score"] - A["tasks"][t][b]["score"]
                            for t in a_tasks])


# ---- chart points -----------------------------------------------------------
points = [dict(s="A", t=t.replace("-001", ""),
               x=round(A["tasks"][t]["baseline"]["input_tokens"]),
               y=round(A["tasks"][t]["mcp_code_finder"]["input_tokens"]),
               d=round(A["tasks"][t]["mcp_code_finder"]["score"] - A["tasks"][t]["baseline"]["score"], 3))
          for t in a_tasks]
points += [dict(s="B", t=t,
                x=B["tasks"][t]["baseline"]["context_tokens"],
                y=B["tasks"][t]["mcp_code_finder"]["context_tokens"],
                d=round(B["tasks"][t]["mcp_code_finder"]["score"] - B["tasks"][t]["baseline"]["score"], 3))
           for t in b_tasks]

a_slope, a_r = analyze.loglog_fit([A["tasks"][t]["baseline"]["input_tokens"] for t in a_tasks],
                                  [A["tasks"][t]["mcp_code_finder"]["input_tokens"] for t in a_tasks])
b_slope, b_r = analyze.loglog_fit([B["tasks"][t]["baseline"]["context_tokens"] for t in b_tasks],
                                  [B["tasks"][t]["mcp_code_finder"]["context_tokens"] for t in b_tasks])
a_fin = [A["tasks"][t]["mcp_code_finder"]["input_tokens"] for t in a_tasks]
a_bas = [A["tasks"][t]["baseline"]["input_tokens"] for t in a_tasks]
b_fin = [B["tasks"][t]["mcp_code_finder"]["context_tokens"] for t in b_tasks]
b_bas = [B["tasks"][t]["baseline"]["context_tokens"] for t in b_tasks]


# ---- arm A per-task rows ----------------------------------------------------
def a_rows():
    out = []
    for t in a_tasks:
        r = A["tasks"][t]
        b, o, c = (r[m]["input_tokens"] for m in ("baseline", "mcp_only", "mcp_code_finder"))
        ratio = c / b
        out.append(
            f'<tr><td class="name">{t.replace("-001", "")}</td>'
            f'<td class="num">{b:,.0f}</td><td class="num">{o:,.0f}</td>'
            f'<td class="num hero">{c:,.0f}</td>'
            f'<td class="num"><span class="chip {"good" if ratio < 0.5 else "mid"}">{ratio:.2f}&times;</span></td>'
            f'<td class="num">{r["baseline"]["score"]:.3f}</td>'
            f'<td class="num">{r["mcp_code_finder"]["score"]:.3f}</td></tr>')
    return "\n".join(out)


def arm_means():
    out = []
    labels = {"baseline": "baseline", "mcp_only": "mcp_only", "mcp_code_finder": "mcp_code_finder"}
    notes = {"baseline": "no MCP server at all; shell, grep and read on a local clone",
             "mcp_only": "Sourcegraph MCP server, every tool except Code Finder",
             "mcp_code_finder": "same server, Code Finder available"}
    for m in ("baseline", "mcp_only", "mcp_code_finder"):
        v = lambda k: mean([A["tasks"][t][m][k] for t in a_tasks])
        cls = ' class="row-hero"' if m == "mcp_code_finder" else ""
        out.append(
            f'<tr{cls}><td class="name"><code>{labels[m]}</code><span class="sub">{notes[m]}</span></td>'
            f'<td class="num">{v("score"):.3f}</td><td class="num">{v("input_tokens"):,.0f}</td>'
            f'<td class="num">{v("tool_calls"):.1f}</td><td class="num">{v("mcp_calls"):.1f}</td>'
            f'<td class="num">{v("code_finder_calls"):.1f}</td><td class="num">{v("wall_sec"):.0f}s</td></tr>')
    return "\n".join(out)


def contrast_rows():
    out = []
    pairs = [("mcp_code_finder", "baseline", "Code Finder vs. no MCP at all",
              "The headline. Replacing a local shell-and-grep loop with one Code Finder call."),
             ("mcp_code_finder", "mcp_only", "Code Finder vs. the rest of the MCP server",
              "Isolates the tool from the server. This is the delta Code Finder itself buys."),
             ("mcp_only", "baseline", "MCP without Code Finder vs. no MCP",
              "The control. Search tools alone cost more than the shell they replace.")]
    for a, b, title, note in pairs:
        s = score_delta(a, b)
        i = contrast(a, b, "input_tokens")
        c = contrast(a, b, "tool_calls")
        w = contrast(a, b, "wall_sec")
        crosses = s[1] < 0 < s[2]
        out.append(f'''<div class="contrast">
  <h4>{title}</h4>
  <p class="note">{note}</p>
  <dl>
    <div><dt>input tokens</dt><dd class="big">{i[0]:.3f}&times;</dd><dd class="ci">{i[1]:.3f} – {i[2]:.3f}</dd></div>
    <div><dt>tool calls</dt><dd class="big">{c[0]:.3f}&times;</dd><dd class="ci">{c[1]:.3f} – {c[2]:.3f}</dd></div>
    <div><dt>wall clock</dt><dd class="big">{w[0]:.3f}&times;</dd><dd class="ci">{w[1]:.3f} – {w[2]:.3f}</dd></div>
    <div><dt>task score</dt><dd class="big">{s[0]:+.3f}</dd><dd class="ci">{s[1]:+.3f} to {s[2]:+.3f}{' · spans zero' if crosses else ''}</dd></div>
  </dl>
</div>''')
    return "\n".join(out)


def band_rows():
    bands = [(0, 4e5, "under 400k", "Code Finder costs more than it saves"),
             (4e5, 7e5, "400k – 700k", "roughly break-even in dollars"),
             (7e5, 1.2e6, "700k – 1.2M", "a real saving appears"),
             (1.2e6, float("inf"), "1.2M and above", "the regime the claim is about")]
    out = []
    tasks = b_tasks
    for lo, hi, label, note in bands:
        sel = [t for t in tasks if lo <= B["tasks"][t]["baseline"]["context_tokens"] < hi]
        g = analyze.ratio_ci([B["tasks"][t]["mcp_code_finder"]["context_tokens"] for t in sel],
                             [B["tasks"][t]["baseline"]["context_tokens"] for t in sel])
        c = analyze.ratio_ci([B["tasks"][t]["mcp_code_finder"]["cost_usd"] for t in sel],
                             [B["tasks"][t]["baseline"]["cost_usd"] for t in sel])
        s = analyze.diff_ci([B["tasks"][t]["mcp_code_finder"]["score"] - B["tasks"][t]["baseline"]["score"]
                             for t in sel])
        bad = g[0] >= 0.95
        out.append(
            f'<tr class="{"row-bad" if bad else ""}"><td class="name">{label}<span class="sub">{note}</span></td>'
            f'<td class="num">{len(sel)}</td>'
            f'<td class="num hero">{g[0]:.3f}&times;<span class="ci">{g[1]:.3f} – {g[2]:.3f}</span></td>'
            f'<td class="num">{c[0]:.3f}&times;<span class="ci">{c[1]:.3f} – {c[2]:.3f}</span></td>'
            f'<td class="num">{s[0]:+.3f}<span class="ci">{s[1]:+.3f} to {s[2]:+.3f}</span></td></tr>')
    allsel = tasks
    g = analyze.ratio_ci([B["tasks"][t]["mcp_code_finder"]["context_tokens"] for t in allsel],
                         [B["tasks"][t]["baseline"]["context_tokens"] for t in allsel])
    c = analyze.ratio_ci([B["tasks"][t]["mcp_code_finder"]["cost_usd"] for t in allsel],
                         [B["tasks"][t]["baseline"]["cost_usd"] for t in allsel])
    s = analyze.diff_ci([B["tasks"][t]["mcp_code_finder"]["score"] - B["tasks"][t]["baseline"]["score"]
                         for t in allsel])
    out.append(
        f'<tr class="row-total"><td class="name">whole corpus, unselected'
        f'<span class="sub">what you get if you skip the selection rule</span></td>'
        f'<td class="num">{len(allsel)}</td>'
        f'<td class="num hero">{g[0]:.3f}&times;<span class="ci">{g[1]:.3f} – {g[2]:.3f}</span></td>'
        f'<td class="num">{c[0]:.3f}&times;<span class="ci">{c[1]:.3f} – {c[2]:.3f}</span></td>'
        f'<td class="num">{s[0]:+.3f}<span class="ci">{s[1]:+.3f} to {s[2]:+.3f}</span></td></tr>')
    return "\n".join(out)


head_i = contrast("mcp_code_finder", "baseline", "input_tokens")
head_s = score_delta("mcp_code_finder", "baseline")
head_w = contrast("mcp_code_finder", "baseline", "wall_sec")
cache_frac = mean([B["tasks"][t]["baseline"]["cache_read"] for t in b_tasks]) / \
             mean([B["tasks"][t]["baseline"]["context_tokens"] for t in b_tasks])

TEMPLATE = open(os.path.join(HERE, "page_template.html")).read()


def render(template, **fields):
    """Substitute @@name@@ placeholders.

    Deliberately not str.format: the template is a full HTML page whose CSS and
    JS are dense with braces, and every one of them would need escaping.
    """
    out = template
    for key, value in fields.items():
        token = "@@" + key + "@@"
        if token not in out:
            raise KeyError(f"template has no {token}")
        out = out.replace(token, str(value))
    left = [w for w in out.split("@@") if w[:1].isalpha() and w[:40].isidentifier()]
    if left:
        raise KeyError(f"unfilled placeholders: {sorted(set(left))}")
    return out


html = render(
    TEMPLATE,
    points=json.dumps(points, separators=(",", ":")),
    a_rows=a_rows(), arm_means=arm_means(), contrasts=contrast_rows(), band_rows=band_rows(),
    n_a_tasks=len(a_tasks), n_a_runs=A["runs"], n_b_tasks=len(b_tasks), n_b_runs=len(b_tasks) * 2,
    head_i=f"{head_i[0]:.3f}", head_i_ci=f"{head_i[1]:.3f} – {head_i[2]:.3f}",
    head_s=f"{head_s[0]:+.3f}", head_s_ci=f"{head_s[1]:+.3f} to {head_s[2]:+.3f}",
    head_w=f"{head_w[0]:.3f}", head_w_ci=f"{head_w[1]:.3f} – {head_w[2]:.3f}",
    a_slope=f"{a_slope:.3f}", a_r=f"{a_r:+.2f}", b_slope=f"{b_slope:.3f}", b_r=f"{b_r:+.2f}",
    a_fin_lo=f"{min(a_fin):,.0f}", a_fin_hi=f"{max(a_fin):,.0f}",
    a_bas_lo=f"{min(a_bas):,.0f}", a_bas_hi=f"{max(a_bas):,.0f}",
    a_fin_cv=f"{st.stdev(a_fin) / mean(a_fin):.2f}", a_bas_cv=f"{st.stdev(a_bas) / mean(a_bas):.2f}",
    b_fin_lo=f"{min(b_fin):,.0f}", b_fin_hi=f"{max(b_fin):,.0f}",
    b_bas_lo=f"{min(b_bas):,.0f}", b_bas_hi=f"{max(b_bas):,.0f}",
    b_fin_cv=f"{st.stdev(b_fin) / mean(b_fin):.2f}", b_bas_cv=f"{st.stdev(b_bas) / mean(b_bas):.2f}",
    cache_pct=f"{cache_frac:.1%}",
    model_a=A["model"], model_b=B["model"],
)
open(OUT, "w").write(html)
print(f"wrote {OUT}  ({len(html):,} bytes)")
