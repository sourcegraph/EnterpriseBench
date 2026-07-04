# Audit: silent zeros from the docker-cp package-dir bug

Bead: `EnterpriseBench-hktt` · Fix commit: `16280cf` (2026-07-04 14:28)

## The bug

`_setup_container` copied the `eb_verify` library with
`docker cp <EB_VERIFY_LIB> <container>:/workspace/.eb_verify`. Copying a
directory to a **non-existent** destination copies the source's *contents*,
so the `eb_verify` package directory itself was dropped: `/workspace/.eb_verify/`
held `plugins/`, `__init__.py`, ... directly instead of `/workspace/.eb_verify/eb_verify/`.

Any check script that resolved `eb_verify` through `PYTHONPATH=/workspace/.eb_verify`
then raised `ModuleNotFoundError: No module named 'eb_verify'`, the check exited
non-zero, and `test_runner` recorded a **silent 0.0** with `verifier_infra_error = null`.
Reproduced locally: the broken contents-only layout raises `ModuleNotFoundError`
(exit 1); the fixed layout (package dir preserved) imports and exits 0.

The broken copy line was introduced 2026-03-29 / 04-01 (`7dff937`, `ae43078`)
and persisted until the 07-04 fix, so every run in between was exposed.

## Fingerprint

A checkpoint is a docker-cp silent zero iff its check invokes `eb_verify`
through the container `PYTHONPATH` (`python3 -m eb_verify.plugins.*`, or a
`from eb_verify... import` that falls back to `PYTHONPATH` because the repo
`/lib` is absent in-container) **and** it scored `0.0` with `exit_code == 1`.

`exit_code == 0` with `0.0` is *not* the bug: the check exited cleanly via a
pre-import guard (missing artifact, unparseable answer) = a genuine agent miss.

## Scope 1 — `file_extraction` (the bead's named plugin): nothing to re-score

Two tasks use `file_extraction` via `python3 -m` in `check_error_source.sh`:
`err-provenance-tri-httpx-proxy-001`, `support-mapping-dual-httpx-httpcore-001`.

Both were added in `5a796c4`, **after** every result batch in `results/`
(including the June rescores `aq8e` 405a78c / `uu17` 257e2d7). They appear in
**zero** result files — no `httpx`/`httpcore` string anywhere under `results/`.
No published `file_extraction` number is contaminated.

The only `file_extraction` run that ever hit the bug was the ad-hoc
solve-verification of `err-provenance-tri-httpx-proxy-001` that surfaced it
(rep1 `error_source` = 0.0; the same answer rescored 1.0 under the fixed layout,
per `16280cf`). That correction already happened. **Nothing to re-score here.**

## Scope 2 — same root cause hit `topological_order` (the real contaminated data)

`check_topo_order.sh` (11 refactor-orchestration tasks) imports
`eb_verify.plugins.topological_order`. In-container there is no repo `/lib`
(check scripts land in `/workspace/.verifiers/`, only `.eb_verify` is copied),
so `LIB_DIR` resolves empty and the import falls back to the broken
`PYTHONPATH=/workspace/.eb_verify` → same `ModuleNotFoundError`.

Across all preserved results, `topo_order` appears 12 times, always `0.0`.
Split by exit code:

| classification | exit | count | meaning |
|---|---|---|---|
| docker-cp silent zero | 1 | **5** | agent produced a valid ordering, reached the import, it failed |
| genuine guard zero | 0 | 7 | no `REFACTOR_PLAN.md` / no numbered ordering; guard fired before import |

The 5 confirmed affected runs (all in the June rescore batches that feed
`headline_recompute.json`):

- `rescore_aq8e/refactor-orch-004/baseline`
- `rescore_aq8e/refactor-orch-007/baseline`
- `rescore_uu17/refactor-orch-001/mcp_only`
- `rescore_uu17/refactor-orch-007/mcp_only`
- `rescore_uu17/refactor-orch-008/mcp_only`

The `aq8e`/`uu17` rescores were a judge-vs-grep re-adjudication
(`rescored_under: origin/main@94e0e0d`) and carried these `topo_order` zeros
through untouched — they never re-ran the plugin, so the infra zeros survived.

### Why offline re-scoring is impossible

`check_topo_order.sh` scores the agent's `REFACTOR_PLAN.md`. The rescore dirs
preserve only `results.json`, not the agent artifact, so the affected orderings
cannot be re-graded from disk. They must be **re-run in a post-fix container**.
Tracked in the follow-up bead (see the bead's notes).

### Headline impact

`topo_order` carries weight 1.0 of 3 checkpoints; each affected task is
understated by up to 1.0. The headline is a baseline-vs-mcp delta per task, so
mode coverage matters:

- `refactor-orch-007` — affected in **both** modes → delta cancels.
- `refactor-orch-004` — baseline-only → delta biased pro-mcp.
- `refactor-orch-001`, `-008` — mcp_only-only → delta biased anti-mcp.

Net effect on the n=105 headline mean is small and mixed in direction, but the
per-task scores for these five are wrong and should not be published as-is.

## Artifacts

- `audit_findings.json` — machine-readable classification of all 21
  import-dependent checkpoint instances (file, task, mode, score, exit_code,
  classification).
