"""Freeze the Code Finder scaling dataset from run artifacts.

Two independent measurements of the same effect:

  arm A  EB retrieval stress set, Codex / gpt-5.6-sol, 10 tasks x 3 arms x 3 reps.
         Token counts come from `tool_usage.total_input_tokens`, which for the
         Codex harness is cumulative input across turns.

  arm B  CSB import corpus, Claude Sonnet 5, 46 tasks x 2 arms x 1 rep.
         Sonnet's `total_input_tokens` EXCLUDES cache reads, so it is not
         comparable to arm A. Context tokens are summed from agent_trace.jsonl
         usage records as input + cache_creation + cache_read, deduped by
         message id (usage is repeated on every content block of a message).

Both are cumulative context processed, which is what an agent's context budget
actually spends and what the Codex harness reports natively.
"""
import json, glob, math, os, statistics as st
from collections import defaultdict

ROOT = "/home/ds/projects/EnterpriseBench"
OUT = os.path.join(ROOT, "experiments/code_finder_scaling/dataset.json")
SCREEN_PAIRED = ("/tmp/claude-1000/-home-ds-projects-EnterpriseBench/"
                 "ce855984-e8ce-4527-bb15-8796b5ed92be/scratchpad/screen_paired.json")
MODES = ["baseline", "mcp_only", "mcp_code_finder"]


def codex_runs():
    by = defaultdict(list)
    for f in glob.glob(f"{ROOT}/results/runs/*/*--cx200[abc]/results.json"):
        d = json.load(open(f))
        tu = d.get("tool_usage") or {}
        if not d.get("success") or (d.get("scores") or {}).get("task_score") is None:
            raise SystemExit(f"invalid run in frozen set: {f} {d.get('failure_class')}")
        by[(d["task_id"], d["config"]["mode"])].append(dict(
            rep=f.split("--cx200")[1][0],
            score=d["scores"]["task_score"],
            input_tokens=tu.get("total_input_tokens", 0),
            output_tokens=tu.get("total_output_tokens", 0),
            tool_calls=(tu.get("provider_activity") or {}).get("tool_uses", 0),
            mcp_calls=tu.get("mcp_tool_calls", 0),
            code_finder_calls=(tu.get("mcp_tool_breakdown") or {}).get("code_finder", 0),
            wall_sec=(d.get("timing") or {}).get("agent", 0)))
    return by


def context_tokens(task, mode):
    """Cumulative context processed, deduped by assistant message id."""
    p = f"{ROOT}/results/runs/{task}/{mode}/agent_trace.jsonl"
    if not os.path.exists(p):
        return None
    seen = {}
    for line in open(p):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        m = d.get("message") or {}
        if m.get("usage") and m.get("id"):
            seen[m["id"]] = m["usage"]
    if not seen:
        return None
    return dict(
        messages=len(seen),
        context_tokens=sum(u.get("input_tokens", 0)
                           + u.get("cache_creation_input_tokens", 0)
                           + u.get("cache_read_input_tokens", 0) for u in seen.values()),
        cache_read=sum(u.get("cache_read_input_tokens", 0) for u in seen.values()),
        cache_creation=sum(u.get("cache_creation_input_tokens", 0) for u in seen.values()),
        output_tokens=sum(u.get("output_tokens", 0) for u in seen.values()))


mean = lambda x: sum(x) / len(x)


def main():
    by = codex_runs()
    arm_a = {}
    for (task, mode), runs in by.items():
        arm_a.setdefault(task, {})[mode] = {
            k: mean([r[k] for r in runs])
            for k in ("score", "input_tokens", "output_tokens", "tool_calls",
                      "mcp_calls", "code_finder_calls", "wall_sec")}
        arm_a[task][mode]["reps"] = len(runs)
    for task, arms in arm_a.items():
        if set(arms) != set(MODES):
            raise SystemExit(f"arm A incomplete for {task}: {sorted(arms)}")

    paired = json.load(open(SCREEN_PAIRED))
    arm_b = {}
    for task, v in paired.items():
        b, f = context_tokens(task, "baseline"), context_tokens(task, "mcp_code_finder")
        if not b or not f:
            continue
        arm_b[task] = {
            "baseline": dict(b, score=v["baseline"]["score"], cost_usd=v["baseline"]["cost"],
                             wall_sec=v["baseline"]["sec"], turns=v["baseline"]["turns"]),
            "mcp_code_finder": dict(f, score=v["mcp_code_finder"]["score"],
                                    cost_usd=v["mcp_code_finder"]["cost"],
                                    wall_sec=v["mcp_code_finder"]["sec"],
                                    turns=v["mcp_code_finder"]["turns"],
                                    code_finder_calls=v["mcp_code_finder"]["cf"])}

    selection = json.load(open(f"{ROOT}/configs/retrieval_stress_set.json"))
    doc = {
        "arm_a": {
            "harness": "codex", "model": "gpt-5.6-sol", "corpus": "EnterpriseBench",
            "selection": {k: v for k, v in selection.items() if k != "tasks"},
            "reps": ["cx200a", "cx200b", "cx200c"], "runs": sum(len(v) for v in by.values()),
            "invalid": 0, "tasks": arm_a},
        "arm_b": {
            "harness": "claude", "model": "claude-sonnet-5",
            "corpus": "CodeScaleBench (converted)", "selection": {"min_repos": 1, "note":
                "stratified across 13 repos and 10 CSB families, >=3 required files; "
                "every task is single-repo and so fails arm A's min_repos>=2"},
            "reps": ["r1"], "tasks": arm_b},
    }
    json.dump(doc, open(OUT, "w"), indent=1, sort_keys=True)
    print(f"wrote {OUT}")
    print(f"  arm A {len(arm_a)} tasks x 3 arms x 3 reps = {doc['arm_a']['runs']} runs")
    print(f"  arm B {len(arm_b)} tasks x 2 arms x 1 rep = {len(arm_b) * 2} runs")


if __name__ == "__main__":
    main()
