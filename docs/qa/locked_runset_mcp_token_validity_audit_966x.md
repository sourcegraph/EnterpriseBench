# Validity Audit — Were Locked mcp_only Runs Collected With an Expired SG Token?

**Bead:** EnterpriseBench-966x (split from c7wb acceptance #3) · **Date:** 2026-06-25
**Mode:** read-only forensic — NO re-runs, NO score changes, token NOT touched
**Outcome:** **CLEAR — 0 affected runs.** The locked N=105 parity headline and the
MCP-lift finding are both clean on the token-expiry axis.

---

## The question

Were any runs in the LOCKED N=105 headline `mcp_only` arm (or the MCP-lift study
arm) collected while the `demo.sourcegraph.com` token was already expired? If so,
`run_task.py` proceeded DEGRADED on MCP pre-flight failure (the c7wb bug: old
behavior at L1183-1186 / L1289-1294 only logged "agent will run but MCP may not
work" and continued), so those `mcp_only` runs would have had NO working MCP —
effectively baseline runs mislabeled as MCP, making the parity headline
(mean −0.093 / median 0.0 / no MCP win) partly an instrumentation artifact.

## Decisive criterion

A `mcp_only` run that **completed N authenticated Sourcegraph MCP tool calls that
returned real data** definitionally had a valid token at collection time. An
expired / rejected (401) token permits **zero** authenticated calls. So the test
for "degraded / token-expired" is: a locked `mcp_only` cell with **0 working MCP
calls** (the exact harm the c7wb bug enabled). The c7wb harm is "ran with no
working MCP yet counted as MCP" — not "the pre-flight handshake check flaked."

## Evidence

### Locked N=105 mcp_only arm

Source of the locked set: `results/analysis/audit_locked_runset.py` /
`aggregate_mcp_clean.py` selection (the generator of the p6ux.28 headline), per-run
dispositions in `docs/qa/locked_runset_dispositions.csv` (`in_locked_aggregate=true`,
`mode=mcp_only` → exactly 105 cells).

| Metric | Result |
|---|---|
| Locked `mcp_only` cells | 105 |
| Cells with **>0** MCP tool calls | **105 / 105** |
| MCP call counts | min **15**, median **53**, max **183** |
| Cells with **0** MCP calls (degraded candidates) | **0** |
| Cells with real auth-failure signature in transcript | **0** (see below) |

- **`mcp_handshake_ok=False` in 24 / 105 cells** — initially alarming, but every
  one of these 24 made **15–91 real MCP calls** (all `status=VALID`, scores
  3.0–4.0 on several). Sampled traces (`dep-traversal-001`,
  `monorepo-boundary-pnpm-lifecycle-006`, `schema-evolution-008`) show 18–20
  successful MCP `tool_result` payloads each, **0 errors, 0 auth errors**. The
  `False` flag is a **pre-flight timing false-negative**: the pre-flight
  `claude mcp list` check did not show `Connected` within its 5-retry window (the
  HTTP transport handshake had not settled), but the agent connected moments
  later and used MCP heavily. The 81 remaining cells predate the s58f
  `mcp_handshake_ok` field (legacy schema) and likewise all made ≥15 calls.

  The 24 handshake-flake tasks: config-drift-consul-serf-port-001,
  config-drift-redis-password-003, config-drift-scdf-rabbitmq-002, dead-code-002,
  dead-code-003, dead-code-005, dep-traversal-001, dep-traversal-002,
  dep-traversal-007, monorepo-boundary-babel-{decorators-002, forof-004,
  metadata-003, ts-init-001}, monorepo-boundary-pnpm-{lifecycle-006, lockfile-005,
  sha256-007}, refactor-orch-005, refactor-orchestration-tri-babel-001,
  schema-evolution-{005, 006, 008}, support-map-element-threads-009,
  support-map-element-unread-010, support-map-nodebb-notif-011.

- **Transcript grep for hard auth signatures** (`Invalid access token`,
  `authentication failed`, `401 Unauthorized`, `invalid_token`) over all 105
  `agent_stdout.log` transcripts: 2 raw hits, **both false positives = task
  source code**, exactly as the bead warned:
  - `api-contract-protobuf-v2-005` — Go source `// 401 Unauthorized -
    UNAUTHENTICATED` (a gRPC↔HTTP status-code mapping table).
  - `schema-evolution-tri-supabase-001` — Haskell source matching the DB string
    `"FATAL:  password authentication failed"`.
  Both tasks made many real MCP calls; neither is an MCP transport auth failure.

### MCP-lift study arm (`results/mcp_lift_study/`, condA = sonnet + Sourcegraph MCP = mcp_only)

`study_aggregates.json:eb_paired` → N=10 pairs. condA (mcp_only) per task:

| Metric | Result |
|---|---|
| condA (mcp_only) tasks | 10 |
| Tasks with **>0** MCP tool calls | **10 / 10** (31–89 calls each) |
| `mean_tool_calls_A` / `mean_search_calls_A` | 47.2 / 22.3 |
| `gt_seen_recall_A` (GT files retrieved by MCP arm) | **0.788** |
| Tasks scoring 0.0 | 3 (ansible-galaxy-tar-regression-prove-001,
  config-drift-tri-kustomize-001, support-map-grafana-import-003) |
| Degraded (0-call) tasks | **0** |

All 10 condA cells show `mcp_handshake_ok=False` (the same pre-flight timing flake)
yet each made 31–89 real MCP calls. The 3 zero-score cells are the critical check
(could a 0.0 hide a degraded run?): they made **75 / 86 / 89** MCP calls, and their
traces show **23 / 23 / 36** successful MCP `tool_result` payloads with **0 auth
errors** — fully working MCP, scoring 0.0 on the merits. `gt_seen_recall_A=0.788`
independently confirms the MCP arm retrieved ground-truth files, which is
impossible without an authenticated token.

## Verdict

**0 affected (token-expired / silently-degraded) runs in either arm.** Every
`mcp_only` cell in the locked N=105 headline (105/105) and the MCP-lift arm (10/10)
completed authenticated Sourcegraph MCP calls returning real data — proof the
`demo.sourcegraph.com` token was valid at the time those runs were collected. The
current 401 expiry is a **later** event, after collection. The parity headline
(mean −0.093 / median 0.0 / no MCP win) and the MCP-lift finding are **clean on
this axis**; this is **not** an instrumentation artifact. No headline-defensibility
escalation is warranted. Recorded as a threats-to-validity clearance.

## Secondary finding — relevant to c7wb (flag for PL)

The pre-flight `claude mcp list` handshake check is **demonstrably flaky**: 34
runs across the two arms (24 locked + 10 lift) recorded `mcp_handshake_ok=False`
while having a fully working, authenticated MCP connection (15–91 real calls each).
The c7wb fix hard-fails a run to the infra-error / re-run channel when
`mcp_handshake_ok` is False. That is **fail-safe** (it routes to re-run, never
scores a degraded run), so c7wb is correct — but on a valid token it will convert
these pre-flight timing flakes into *false-positive* infra-errors and waste
re-runs. The HTTP curl check (`_verify_mcp_endpoint`, which directly tests token
validity) is the more reliable gate; the `claude mcp list` retry window is the
flaky one. A follow-up refinement for c7wb could weight the gate toward the curl
result (token reachability) rather than failing solely on the `claude mcp list`
handshake timing. Not a defect in c7wb — a precision improvement to avoid
unnecessary re-runs. Raised here because this audit is the evidence base for it.

## Reproduce (read-only)

- Locked set + per-run MCP-call counts: `docs/qa/locked_runset_dispositions.csv`
  (filter `mode=mcp_only`, `in_locked_aggregate=true`; column `mcp_tool_calls`).
- `mcp_handshake_ok` per cell: `results/runs/<task>/mcp_only/results.json`.
- MCP-call data vs errors: `results/runs/<task>/mcp_only/agent_trace.jsonl`
  (`tool_result` entries; `is_error` flag).
- MCP-lift arm: `results/mcp_lift_study/eb/<task>/condA/` and
  `results/mcp_lift_study/study_aggregates.json`.
