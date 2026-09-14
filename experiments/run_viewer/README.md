# Run browser

A single self-contained HTML page listing every scored run under `results/runs/`:
task, arm, model, score, context tokens, cost, tool calls, and what the session
opened with. Click a row for per-checkpoint scores and the MCP tool breakdown.

```bash
python3 experiments/run_viewer/build.py                 # -> experiments/run_viewer/index.html
python3 experiments/run_viewer/build.py <runs> <out>    # a different run set or destination
python3 experiments/run_viewer/build.py --body-only     # no <html> wrapper, for a hosted publish
```

The run data is inlined at build time, so `index.html` opens from disk, from a
file share, or from GitHub Pages with no server and no network access. Rebuild
it after a sweep; it does not read `results/runs/` at load time.

## Two columns that are not what they look like

**Context tok** is what the model read: uncached input plus cache creation plus
cache reads, summed from `agent_trace.jsonl` and deduped by message id. The
`total_input_tokens` a Claude harness writes into `results.json` *excludes cache
reads* and understates the real figure by two or three orders of magnitude, so
it is not the column; it is in the detail panel, labelled. A few traces were
captured only in part, and for those the reported number is the larger of the
two and gets used instead. Every row names its source.

**Score** is on 0-1. Runs written before the scorer moved to a weighted mean
carry a weighted *sum*, so three checkpoints all passing reads `3.0`. Those are
divided by total checkpoint weight, with the raw value kept in the detail panel.
Averaging the two scales together put the mean score above 1 before this was
handled.

Excluded: `results/runs/_invalidated/`, and any run directory with no
`results.json`, which was never scored.

Tests: `tests/test_run_viewer.py`.
