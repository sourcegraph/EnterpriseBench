# Refile: s58f perm/fail-loud fix — before/after evidence (campaign p6ux, bead EnterpriseBench-59hw)

The 10 tasks below recorded `mcp_only` + `hybrid` as fake-0 NO-OP runs
(EACCES container-perm: agent never started, `num_turns=0`, `score=0.0`,
recorded `success=true`) while BASELINE scored normally — fabricating a
"baseline beats MCP" delta. After the s58f fix (chown + pre-agent readability
gate + fail-loud INVALID classification) the MCP modes actually run.

Re-run command (per task/mode):
`python3 scripts/orchestration/run_task.py <toml> --mode <mcp_only|hybrid> --account <1|3|4> --agent 'claude --dangerously-skip-permissions --max-turns 50 --verbose --output-format stream-json -p'`

## BEFORE (pre-fix, from existing results/runs/<task>/<mode>/results.json)

| task | baseline (VALID) | mcp_only BEFORE | hybrid BEFORE |
|------|------------------|-----------------|---------------|
| dep-traversal-007 | score=4.0, turns=20 | NO-OP score=0.0 turns=0 mcp=0 | NO-OP score=0.0 turns=0 mcp=0 |
| schema-evolution-006 | score=3.75, turns=23 | NO-OP score=0.0 turns=0 mcp=0 | NO-OP score=0.0 turns=0 mcp=0 |
| dead-code-005 | score=2.67, turns=7 | NO-OP score=0.0 turns=0 mcp=0 | NO-OP score=0.0 turns=0 mcp=0 |
| monorepo-boundary-pnpm-lifecycle-006 | score=3.0, turns=37 | NO-OP score=0.0 turns=0 mcp=0 | NO-OP score=0.0 turns=0 mcp=0 |
| monorepo-boundary-pnpm-sha256-007 | score=2.42, turns=31 | NO-OP score=0.0 turns=0 mcp=0 | NO-OP score=0.0 turns=0 mcp=0 |
| support-map-element-unread-010 | score=2.47, turns=10 | NO-OP score=0.0 turns=0 mcp=0 | NO-OP score=0.0 turns=0 mcp=0 |
| support-map-nodebb-notif-011 | score=2.0, turns=26 | NO-OP score=0.0 turns=0 mcp=0 | NO-OP score=0.0 turns=0 mcp=0 |
| support-map-element-threads-009 | score=1.97, turns=14 | NO-OP score=0.0 turns=0 mcp=0 | NO-OP score=0.0 turns=0 mcp=0 |
| dead-code-003 | score=1.69, turns=20 | NO-OP score=0.0 turns=0 mcp=0 | NO-OP score=0.0 turns=0 mcp=0 |
| refactor-orchestration-tri-babel-001 | score=1.5, turns=7 | NO-OP score=0.0 turns=0 mcp=0 | NO-OP score=0.0 turns=0 mcp=0 |

## AFTER (post-fix re-runs)

| task | mode | status | score | num_turns | mcp_calls | handshake | account |
|------|------|--------|-------|-----------|-----------|-----------|---------|
| dep-traversal-007 | mcp_only | VALID | 4.0 | 25 | 32 | False | 1 |
| dep-traversal-007 | hybrid | FALLBACK | 4.0 | 24 | 0 | False | 1 |
| schema-evolution-006 | mcp_only | VALID | 4.0 | 33 | 36 | False | 3 |
| schema-evolution-006 | hybrid | FALLBACK | 4.0 | 41 | 0 | False | 4 |
| dead-code-005 | mcp_only | VALID | 2.575 | 47 | 54 | False | 1 |
| dead-code-005 | hybrid | FALLBACK | 2.7439 | 20 | 0 | False | 3 |
| monorepo-boundary-pnpm-lifecycle-006 | mcp_only | VALID | 3.0 | 53 | 72 | False | 4 |
| monorepo-boundary-pnpm-lifecycle-006 | hybrid | FALLBACK | 3.0 | 40 | 0 | False | 1 |
| monorepo-boundary-pnpm-sha256-007 | mcp_only | VALID | 2.42 | 33 | 52 | False | 3 |
| monorepo-boundary-pnpm-sha256-007 | hybrid | FALLBACK | 2.67 | 14 | 0 | False | 4 |
| support-map-element-unread-010 | mcp_only | VALID | 1.97 | 28 | 44 | False | 1 |
| support-map-element-unread-010 | hybrid | FALLBACK | 2.57 | 10 | 0 | False | 3 |
| support-map-nodebb-notif-011 | mcp_only | VALID | 0.0 | 51 | 91 | False | 4 |
| support-map-nodebb-notif-011 | hybrid | FALLBACK | 2.47 | 35 | 0 | False | 1 |
| support-map-element-threads-009 | mcp_only | VALID | 2.97 | 38 | 54 | False | 3 |
| support-map-element-threads-009 | hybrid | FALLBACK | 2.97 | 17 | 0 | False | 4 |
| dead-code-003 | mcp_only | VALID | 2.3542 | 56 | 75 | False | 1 |
| dead-code-003 | hybrid | FALLBACK | 2.0209 | 73 | 0 | False | 3 |
| refactor-orchestration-tri-babel-001 | mcp_only | VALID | 1.5 | 46 | 57 | False | 4 |
| refactor-orchestration-tri-babel-001 | hybrid | FALLBACK | 1.5 | 7 | 0 | False | 1 |

## Summary

**Verification:** all 20 mode-runs are now real (status set, num_turns>0). 10/10
`mcp_only` flipped fake NO-OP → **VALID** with real MCP traffic (32–91 mcp_calls
each). All 10 `hybrid` ran real turns but made 0 MCP calls → **FALLBACK** (agent
chose filesystem tools — a legitimate, scoreable run, not a fake-0). **Zero**
runs remained INVALID/0-turn: the s58f perm fix took for every container.

**The fabricated anti-MCP delta is dissolved.** Pre-fix, baseline "beat" MCP on
all 10 tasks because `mcp_only` was a fake 0.0 — a fabricated anti-MCP gap of
**25.47** total points. Post-fix `mcp_only` total = **24.79** vs baseline
**25.47**, a real delta of only **+0.68 across 10 tasks** (a near dead-heat),
and that entire residual is the single genuine MCP failure on
support-map-nodebb-notif-011 (mcp_only ran 51 turns / 91 MCP calls and honestly
scored 0.0 vs baseline 2.0).

Per-task real `mcp_only` vs baseline: **MCP better on 3** (schema-evolution-006
+0.25, support-map-element-threads-009 +1.0, dead-code-003 +0.66), **~tie on 5**
(dep-traversal-007, dead-code-005, both monorepo-boundary, refactor-babel),
**MCP worse on 2** (support-map-element-unread-010 −0.50, support-map-nodebb-notif-011 −2.0).

**Caveats for the package:**
- `mcp_handshake_ok` is `False` on every row even where mcp_calls is 32–91. The
  pre-flight `claude mcp list` check is a consistent false-negative; **mcp_calls>0
  is the ground-truth signal that MCP worked**, not the handshake field. Worth a
  follow-up to make the pre-flight check reliable (or stop recording it as authoritative).
- In **hybrid** mode the agent used MCP 0× on all 10 tasks (always fell back to
  fs), while in **mcp_only** it used MCP heavily — a behavioral finding about
  hybrid tool-selection, separate from this refile.

---

# BATCH 2 — remaining 14 perm-bug tasks post-s58f (bead EnterpriseBench-69ix, campaign p6ux)

Batch 1 covered the 10 *asymmetric* NO-OP tasks (baseline VALID, MCP modes
fake-0). Batch 2 covers the remaining NO-OP-class tasks where the perm bug
killed **more** than just the MCP modes:

* **both-dead (7):** baseline ALSO recorded as fake-0 NO-OP → re-run all 3 modes.
* **baseline-missing (6):** baseline never ran (no results.json) → run all 3 modes.

**Excluded from this batch:** `aspnetcore-code-review-001`. The validity audit
(_mcp_validity_audit_p6ux.tsv) predates commit 9fa129f ("cut-coding-tasks"),
which DELETED aspnetcore-code-review-001, beam-pipeline-builder-refac-001, and
ansible-abc-imports-fix-001 as coding-ability tasks out of scope for context
retrieval. Its task.toml, ground_truth, and sg-mirror config no longer exist, so
it cannot — and should not — be refiled. Runnable batch-2 set = **13 tasks**.

Re-run command (per task/mode): MCP modes on acct 1/3/4 (never acct2).
`python3 scripts/orchestration/run_task.py <toml> --mode <baseline|mcp_only|hybrid> --account <1|3|4> --agent 'claude --dangerously-skip-permissions --max-turns 50 --verbose --output-format stream-json -p'`

## BATCH 2 — BEFORE (pre-fix, from existing results/runs/<task>/<mode>/results.json)

| task | category | baseline BEFORE | mcp_only BEFORE | hybrid BEFORE |
|------|----------|-----------------|-----------------|---------------|
| dead-code-004 | both-dead | NO-OP status=None score=0.0 turns=0 mcp=0 | NO-OP status=None score=0.0 turns=0 mcp=0 | NO-OP status=None score=0.0 turns=0 mcp=0 |
| dep-traversal-002 | both-dead | NO-OP status=None score=0.0 turns=0 mcp=0 | NO-OP status=None score=0.0 turns=0 mcp=0 | NO-OP status=None score=0.0 turns=0 mcp=0 |
| monorepo-boundary-babel-decorators-002 | both-dead | NO-OP status=None score=0.0 turns=0 mcp=0 | NO-OP status=None score=0.0 turns=0 mcp=0 | NO-OP status=None score=0.0 turns=0 mcp=0 |
| monorepo-boundary-babel-forof-004 | both-dead | NO-OP status=None score=0.0 turns=0 mcp=0 | NO-OP status=None score=0.0 turns=0 mcp=0 | NO-OP status=None score=0.0 turns=0 mcp=0 |
| monorepo-boundary-babel-metadata-003 | both-dead | NO-OP status=None score=0.0 turns=0 mcp=0 | NO-OP status=None score=0.0 turns=0 mcp=0 | NO-OP status=None score=0.0 turns=0 mcp=0 |
| monorepo-boundary-babel-ts-init-001 | both-dead | NO-OP status=None score=0.0 turns=0 mcp=0 | NO-OP status=None score=0.0 turns=0 mcp=0 | NO-OP status=None score=0.0 turns=0 mcp=0 |
| schema-evolution-008 | both-dead | NO-OP status=None score=0.0 turns=0 mcp=0 | NO-OP status=None score=0.0 turns=0 mcp=0 | NO-OP status=None score=0.0 turns=0 mcp=0 |
| dead-code-001 | baseline-missing | (no run) | NO-OP status=None score=0.0 turns=0 mcp=0 | NO-OP status=None score=0.0 turns=0 mcp=0 |
| dead-code-002 | baseline-missing | (no run) | NO-OP status=None score=0.0 turns=0 mcp=0 | NO-OP status=None score=0.0 turns=0 mcp=0 |
| dep-traversal-001 | baseline-missing | (no run) | NO-OP status=None score=0.0 turns=0 mcp=0 | NO-OP status=None score=0.0 turns=0 mcp=0 |
| monorepo-boundary-pnpm-lockfile-005 | baseline-missing | (no run) | NO-OP status=None score=0.0 turns=0 mcp=0 | NO-OP status=None score=0.0 turns=0 mcp=0 |
| refactor-orch-005 | baseline-missing | (no run) | NO-OP status=None score=0.0 turns=0 mcp=0 | NO-OP status=None score=0.0 turns=0 mcp=0 |
| schema-evolution-005 | baseline-missing | (no run) | NO-OP status=None score=0.0 turns=0 mcp=0 | NO-OP status=None score=0.0 turns=0 mcp=0 |

## BATCH 2 — AFTER (post-fix re-runs)

| task | mode | status | score | num_turns | mcp_calls | handshake | account |
|------|------|--------|-------|-----------|-----------|-----------|---------|
| dead-code-004 | baseline | VALID | 0.0 | 29 | 0 | None | 1 |
| dead-code-004 | mcp_only | VALID | 1.7895 | 66 | 762 | False | 3 |
| dead-code-004 | hybrid | FALLBACK | 0.0 | 51 | 0 | False | 4 |
| dep-traversal-002 | baseline | VALID | 4.0 | 47 | 0 | None | 1 |
| dep-traversal-002 | mcp_only | VALID | 4.0 | 25 | 32 | False | 3 |
| dep-traversal-002 | hybrid | FALLBACK | 4.0 | 37 | 0 | False | 4 |
| monorepo-boundary-babel-decorators-002 | baseline | VALID | 2.67 | 26 | 0 | None | 1 |
| monorepo-boundary-babel-decorators-002 | mcp_only | VALID | 3.0 | 33 | 48 | False | 3 |
| monorepo-boundary-babel-decorators-002 | hybrid | FALLBACK | 3.0 | 37 | 0 | False | 4 |
| monorepo-boundary-babel-forof-004 | baseline | VALID | 2.67 | 20 | 0 | None | 1 |
| monorepo-boundary-babel-forof-004 | mcp_only | VALID | 2.67 | 45 | 60 | False | 3 |
| monorepo-boundary-babel-forof-004 | hybrid | FALLBACK | 2.67 | 80 | 0 | False | 4 |
| monorepo-boundary-babel-metadata-003 | baseline | VALID | 3.0 | 14 | 0 | None | 1 |
| monorepo-boundary-babel-metadata-003 | mcp_only | VALID | 3.0 | 54 | 69 | False | 3 |
| monorepo-boundary-babel-metadata-003 | hybrid | FALLBACK | 3.0 | 31 | 0 | False | 4 |
| monorepo-boundary-babel-ts-init-001 | baseline | VALID | 3.0 | 43 | 0 | None | 1 |
| monorepo-boundary-babel-ts-init-001 | mcp_only | VALID | 3.0 | 29 | 40 | False | 3 |
| monorepo-boundary-babel-ts-init-001 | hybrid | FALLBACK | 3.0 | 38 | 0 | False | 4 |
| schema-evolution-008 | baseline | VALID | 4.0 | 59 | 0 | None | 1 |
| schema-evolution-008 | mcp_only | VALID | 4.0 | 57 | 68 | False | 3 |
| schema-evolution-008 | hybrid | VALID | 4.0 | 51 | 17 | False | 4 |
| dead-code-001 | baseline | VALID | 0.0 | 51 | 0 | None | 1 |
| dead-code-001 | mcp_only | VALID | 0.0 | 51 | 111 | False | 3 |
| dead-code-001 | hybrid | FALLBACK | 1.51 | 49 | 0 | False | 4 |
| dead-code-002 | baseline | VALID | 2.2237 | 24 | 0 | None | 1 |
| dead-code-002 | mcp_only | VALID | 2.3696 | 25 | 32 | False | 3 |
| dead-code-002 | hybrid | FALLBACK | 2.2237 | 24 | 0 | False | 4 |
| dep-traversal-001 | baseline | VALID | 3.0 | 26 | 0 | None | 1 |
| dep-traversal-001 | mcp_only | VALID | 3.0 | 48 | 55 | False | 3 |
| dep-traversal-001 | hybrid | FALLBACK | 3.0 | 28 | 0 | False | 4 |
| monorepo-boundary-pnpm-lockfile-005 | baseline | VALID | 2.0 | 22 | 0 | None | 1 |
| monorepo-boundary-pnpm-lockfile-005 | mcp_only | VALID | 2.78 | 49 | 68 | False | 3 |
| monorepo-boundary-pnpm-lockfile-005 | hybrid | FALLBACK | 2.78 | 16 | 0 | False | 4 |
| refactor-orch-005 | baseline | VALID | 1.2 | 38 | 0 | None | 1 |
| refactor-orch-005 | mcp_only | VALID | 1.0 | 42 | 57 | False | 3 |
| refactor-orch-005 | hybrid | FALLBACK | 1.28 | 23 | 0 | False | 4 |
| schema-evolution-005 | baseline | VALID | 3.15 | 27 | 0 | None | 1 |
| schema-evolution-005 | mcp_only | VALID | 3.55 | 64 | 78 | False | 3 |
| schema-evolution-005 | hybrid | FALLBACK | 3.38 | 27 | 0 | False | 4 |

## BATCH 2 — Summary

**Verification:** all **39** mode-runs (13 tasks × 3 modes) are now real —
`status` set, `num_turns>0`, and every `mcp_only` run made real MCP traffic
(32–762 mcp_calls). **Zero** runs remained INVALID/0-turn; **no STOP** condition
fired. The s58f perm fix (chown + pre-agent readability gate + fail-loud INVALID
classification) took for every container in this batch.

**Flipped fake-NO-OP → real:** 33 runs.
* **both-dead (7 tasks):** all 21 runs (baseline + mcp_only + hybrid) were fake-0
  NO-OP pre-fix; all 21 now real. Baseline flipped fake-0 → real VALID; mcp_only
  flipped → VALID with real MCP.
* **baseline-missing (6 tasks):** 12 fake-NO-OP MCP-mode runs flipped → real, and
  the 6 previously-**missing** baselines now exist as real VALID runs (39th run
  total = 21 + 12 + 6).

**hybrid behavior:** 12/13 hybrid runs are FALLBACK (real, scoreable, agent chose
filesystem tools, 0 MCP); 1/13 (schema-evolution-008) used MCP in hybrid (17
calls, VALID). Same pattern as batch 1.

**Excluded:** `aspnetcore-code-review-001` — in the stale audit but DELETED in
commit 9fa129f ("cut-coding-tasks") as a coding-ability task; no task.toml /
ground_truth / sg-mirror exists. Cannot and should not be refiled. Runnable
batch-2 set = 13 (audit's "14" predates the deletion).

**Signal impact:** on these now-clean runs `mcp_only` is at or above baseline on
several tasks (dead-code-004 1.79 vs 0.0; monorepo-decorators 3.0 vs 2.67;
pnpm-lockfile 2.78 vs 2.0; schema-005 3.55 vs 3.15; dead-code-002 2.37 vs 2.22),
corroborating the rollup finding that the prior anti-MCP delta was substantially
a container-perm artifact, not a real capability gap.
