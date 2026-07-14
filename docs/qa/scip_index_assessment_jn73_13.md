# SCIP Index Assessment — gate for a codenav-inclusive MCP arm

Bead: `EnterpriseBench-jn73.13` (epic `EnterpriseBench-jn73`)

## Verdict: NO-GO on a codenav-inclusive (`all` endpoint) MCP arm

NO-GO, but for the opposite reason to the one the epic assumed — and the real
problem is bigger than the question that was asked.

The epic's premise was that codenav might be inert over our mirrors — that
`go_to_definition` returning empty meant SCIP indexes were absent, so an `all`
arm would add nothing. That premise is **refuted**. The mirrors *are* precisely
indexed and codenav works (Finding 1).

But the finding that matters is not about codenav at all. **The MCP arms already
in the study are contaminated**: agents see, and read, source from a *different
pinned revision of the same project* than the one their task pins. 15 runs across
10 tasks, in **both** `mcp_only` and `hybrid` (Finding 2). Codenav accounts for
only ~2% of the leaking calls; ordinary `keyword_search` and `read_file` over a
global index full of near-duplicate mirrors account for the rest — and 8 tasks
ship a preamble that points the agent at the wrong mirror outright.

So: NO-GO on adding a codenav-inclusive arm, because the revision-pinning
guarantee the mirrors exist to provide is **already broken** in the arms we
shipped, and adding a fourth arm on top of an unsound base would compound it.
Fixing the pin leak is the prerequisite, not the codenav decision.

This flips to GO once MCP results are constrained to the authorized mirror set —
server-side scoping, or a harness-side filter — and the mis-pinned preambles are
corrected.

> **Correction.** An earlier revision of this document reported the leak as
> `mcp_only`-only, credited codenav as its driver, and reported `hybrid` as
> clean. All three were artifacts of deriving the authorized mirror set from the
> MCP preamble — which is the very artifact that is mis-pinned. The authorized
> set now comes from the task config. See "What counts as the authorized set".

## How to regenerate every number here

```bash
# Contamination audit + exposure surface over the trace corpus (results/runs is
# gitignored; point --runs-dir at wherever the corpus lives).
python scripts/analysis/audit_mirror_contamination.py --runs-dir results/runs
python scripts/analysis/audit_mirror_contamination.py --runs-dir results/runs --json

# Live precise-index status (needs a valid token; see Finding 4).
python scripts/infra/verify_sg_indexing.py --check-api
```

Every number in this document is emitted by those commands. Nothing here is
hand-counted.

### What counts as the authorized set

**The task's own config, never the rendered MCP preamble.** This distinction is
load-bearing and an earlier draft of this assessment got it wrong.

`configs/sg_mirrors/<task>.json` (or, failing that, the task's `[[repos]]`) is the
pin of record; `derive_mirror_name` maps it to the mirror name on the instance.
The preamble is a *rendered artifact* and is itself mis-pinned for 8 tasks. An
audit that reads the authorized set off the preamble asks only "did the agent
obey its instructions?" — so when the instruction is the thing at fault, a real
pin violation scores AUTHORIZED and the auditor certifies the contamination it
exists to catch. Correcting this moved the headline (see Finding 2).

Corpus as audited: 408 traces, **261 scored**. A run is scorable only if it was
MCP-armed *and* its pin resolves. Excluded, loudly rather than silently:

- **147 unscored** — no MCP preamble (baseline, `ablate-*`). Structurally
  incapable of this leak; not "clean".
- **12 MCP runs (6 tasks) with no resolvable pin** — reported as
  `PIN UNRESOLVED`, never as clean. `ansible-abc-imports-fix-001`,
  `beam-pipeline-builder-refac-001`, and the three `dep-graph-tri-*` tasks have
  no task config on disk. `bustub-hyperloglog-impl-001` has one, but it is the
  placeholder `unknown/repo@HEAD` — and `HEAD` is the absence of a pin, not a
  pin; deriving `sg-evals/repo--HEAD` from it would score every real mirror the
  agent touched as a violation.
- **12 runs under `_invalidated/`** — already withdrawn from analysis.

Replicates (`<task>/<mode>/repN/`) are counted under the mode they repeat.

## Finding 1 — the mirrors ARE precisely indexed (SCIP present)

We cannot ask the instance directly (Finding 4), but the traces answer it. The
discriminator is the SCIP moniker rule: **local monikers cannot cross repos;
package monikers can.** So a module-local symbol must stay inside its own repo,
while an exported symbol may resolve anywhere the package is known.

From `results/runs/dead-code-003/mcp_only`, all five `find_references` calls
scoped to `sg-evals/react--ab18f33d`:

| Symbol | Kind | Repos in result | Crossed? |
|---|---|---|---|
| `retryErrors` | module-local | `react--ab18f33d` only | no |
| `CompilerMode` | exported | `react--56408a5b` only | yes |
| `validateNoUntransformedReferences` | exported | `--ab18f33d` + `--56408a5b` | yes |
| `transformProgram` | exported | `--ab18f33d` + `--56408a5b` | yes |
| `pruneAllReactiveScopes` | exported | `--ab18f33d` + `--56408a5b` + `facebook/react` | yes |

A name-based search fallback would have matched `retryErrors` in the other two
copies as well — the identifier sits at an identical path in all three. It did
not. Exactly the exported symbols crossed. That local-vs-exported asymmetry is
the signature of precise indexes, not of text search.

Corollary: **"`go_to_definition` empty ⇒ SCIP absent" is refuted.** Across the
corpus `go_to_definition` returned results on every call; it was never silently
empty. (Sample is thin — n=5 calls corpus-wide — so this refutes the epic's
premise rather than establishing a rate.) The codenav null the epic set out to
explain has a different cause, and `find_references`' 5/16 Cloudflare transport
failures (filed separately) are a more likely contributor than indexing.

## Finding 2 — cross-revision contamination, in BOTH MCP-armed arms

Because ~22 projects sit on the instance at several pinned revisions, precise
linking walks between them. Per-mode, over the scored runs:

| Mode | Runs | Wrong-rev in results | Wrong-rev ACTIVELY READ | Foreign-repo bleed |
|---|---|---|---|---|
| `mcp_only` | 130 | 23 | **13** | 23 |
| `hybrid` | 130 | 3 | **2** | 0 |
| `baseline` + `ablate-*` | 147 | n/a — unscored | n/a | n/a |
| no mode dir | 1 | 0 | 0 | 0 |

**15 runs across 10 distinct tasks** pulled wrong-revision source into context:
`ansible-galaxy-tar-regression-prove-001` (×`mcp_only`+`hybrid`),
`ccx-dep-trace-106` (×`mcp_only`+`hybrid`), `api-contract-gocp-module-007`,
`ceph-rgw-auth-secure-001`, `dead-code-003`, `schema-evolution-002`,
`schema-evolution-005`, `incident-investigation-quad-containerd-001`,
`error-trace-k8s-nftables-sync-001` (2 replicates), and
`incident-inv-docker-shutdown-004` (3 replicates).

### "MCP-arm-only" is retracted

An earlier draft of this document reported `hybrid` as **0 actively-read** and
titled this finding "and it is MCP-arm-only". Both claims were artifacts of
deriving the authorized set from the MCP preamble. `hybrid` actively read
wrong-revision source in **2 runs**. The contamination is not confined to the
`mcp_only` arm, and any rescore decision must cover `hybrid` too.

Concretely, `ccx-dep-trace-106` pins `gcc@releases/gcc-14.2.0`, but its
`instruction_mcp.md` tells the agent to filter on `sg-evals/gcc--96dfb333` — a
mirror that appears nowhere in the registry. Its `hybrid` run duly addressed
`gcc--96dfb333` and read from it. Under the old preamble-derived audit that
scored as **AUTHORIZED**, because the preamble was both the instruction and the
answer key.

### Two mechanisms, not one

The pin-violating leaks divide by cause, and the distinction decides the fix:

- **Mis-pinned instruction (14 runs, 7 tasks).** The preamble names a mirror the
  task does not pin; the agent obeys and reads the wrong revision. This is a
  **task-authoring bug**, not a codenav failure — it would happen with codenav
  disabled. Affected: `ansible-galaxy-tar-regression-prove-001`,
  `camel-routing-arch-001`, `ccx-compliance-052`, `ccx-compliance-053`,
  `ccx-dep-trace-106`, `ccx-incident-032`, `ceph-rgw-auth-secure-001` (each
  ×`mcp_only`+`hybrid`). The auditor prints a `MIS-PINNED PREAMBLE` block naming
  each. Filed as its own bead.
- **Index-mediated drift.** Results cite a sibling mirror the agent never asked
  for, and the agent then follows the reference. `dead-code-003` is the clean
  illustration: the task pins `react--ab18f33d`, `find_references` returned hits
  in `react--56408a5b` (`dead-code-002`'s pin), and the agent issued `read_file`
  **against `react--56408a5b`**.

Baseline remains **unscored, not clean**: no MCP preamble, no authorized set,
nothing to violate. It has `/workspace` at the pinned revision and no cross-repo
index to traverse, so it is *structurally* incapable of this leak. Counting it as
"0 violations" would imply it was tested and passed.

### Codenav does not drive the leak

Leaks by tool (calls whose results carried a wrong-revision repo):

| Tool | Leaking calls |
|---|---|
| `read_file` | 95 |
| `keyword_search` | 93 |
| `find_references` (codenav) | **4** |
| `commit_search` | 1 |
| `Agent` (subagent report) | 1 |

Codenav is **4 of 194 leaking calls — about 2%**. An earlier draft asserted that
"codenav drives" the pin-violating mode while publishing a table that said
otherwise; that claim is withdrawn as self-contradictory.

This *strengthens* the NO-GO rather than weakening it. The leak is not a property
of the one endpoint we were deciding whether to add — it is already present in
the MCP arms as shipped, and it is driven by ordinary search and read calls over
a global index that contains near-duplicate mirrors. **Remediation scoped to
codenav would miss ~98% of it.**

Two escape modes remain worth keeping apart, because their severity differs:

- **Pin-violating** — a different revision of the *same* project. Breaks the
  revision pin. The acute mode.
- **Global-index bleed** — an unrelated third-party repo. Widens context and adds
  noise, but does not violate the pin.

Collapsing them would overstate the harm.

### Exposure surface

Emitted by the auditor (`--- Exposure surface ---`), computed from the mirror
registry rather than counted by hand:

| | |
|---|---|
| Mirrors | 133 |
| Distinct projects | 71 |
| Projects at **multiple** revisions | **22** |
| Mirrors belonging to a multi-rev project | **84 (63%)** |

Worst offenders: `grpc-go` ×12, `kubernetes` ×10, `etcd` ×9. Any MCP-arm task on
those projects is exposed. A project pinned at a single revision cannot be
escaped into a sibling, so the 49 mirrors outside a multi-rev project are not at
risk from this mechanism.

## Finding 3 — the preamble promises scoping that codenav cannot honor

Every MCP preamble says:

> **Always scope your MCP searches to these repos:**
> `repo:^github.com/sg-evals/react--ab18f33d$`

Agents comply. In `dead-code-003` every single call carried the correct
`repo:` filter — and the leak happened anyway. For codenav the `repo` argument is
a **seed position, not a filter**: it says where to start resolving, not where
results may come from. There is no client-side way to honor the instruction the
preamble gives. This is a harness bug, not agent misbehavior (bead filed).

## Finding 4 — live per-repo status is UNKNOWN, and the acceptance criterion is not dischargeable

The bead asks for "per-repo SCIP index status for all stratum repos." That half
cannot be discharged now, and this document will not paper over it with a table
of UNKNOWNs:

- **The token is dead.** The `SOURCEGRAPH_ACCESS_TOKEN` in the environment
  (value redacted) returns **HTTP 401 on both** `demo.sourcegraph.com` (the arm
  target) and `sourcegraph.com`. Verified, not inherited.
- **The stratum is undefined.** Bead `.2` (difficulty stratum) is still open, so
  "stratum repos" has no referent yet.

What ships instead is the *instrument*: `verify_sg_indexing.py --check-api` now
performs a real GraphQL query per mirror and reports one of
`PRECISE | NONE | ABSENT | UNKNOWN`. Its load-bearing invariant is that a
failure to determine status yields **UNKNOWN, never NONE or ABSENT** — reporting
"not indexed" when we mean "could not tell" would fabricate the very finding the
tool exists to establish. Enforced for all of:

- auth failure (401/403), transport error, timeout;
- a body that is not JSON, and a body that *is* valid JSON but the wrong shape
  (`{"data": []}`, a string where an object belongs, a top-level list or null) —
  only an explicit `repository: null`, the instance answering "no such repo", is
  allowed to mean `ABSENT`;
- GraphQL schema drift (a renamed field).

Failures are also contained *per mirror*: one malformed response degrades that
mirror to UNKNOWN instead of aborting the run and taking the other 132 statuses
with it. With no token the report is inconclusive and exits non-zero rather than
printing a table of zeroes. Run it when a valid token exists; the answer today is
UNKNOWN for all 133 mirrors.

It keys on `repos[].sg_name` (the mirror). The per-suite view carries only
*upstream* names, and querying those would ask about `github.com/ansible/ansible`
— indexed on any public instance — and return a **false GREEN** for a mirror that
may not exist at all.

### `_indexed` is a placeholder and cannot corroborate anything

`configs/sg_indexing_list.json` reports `_indexed: false` for all 133 mirrors.
This is **not evidence**. `generate_sg_index.py` writes the field as a hardcoded
literal `False` for every entry; reading "0/133 indexed" back out is the tool
reporting its own placeholder. It is circular and it is cited nowhere in this
assessment. The literal is now annotated at its source, and the inventory output
labels it as an unverified placeholder rather than printing a bare "Indexed: 0"
that reads like a finding.

(The field is left in place: `validate_tasks_preflight.py` gates on its presence.
Replacing it with real status is filed as a separate bead rather than smuggled
into this one.)

## What would flip this to GO

The blocker is scope leakage, not index absence. Any one of these fixes it:

1. **Server-side scoping** — codenav honors a repo filter, so results cannot
   escape the authorized set.
2. **Harness-side guard** — the MCP layer drops or flags results from any repo
   outside the run's authorized mirror set. Cheapest option; the authorized set
   is already in the preamble and this audit already parses it.
3. **De-duplicate the corpus** — one revision per project on the instance. Kills
   the sibling-mirror class outright, but not upstream-HEAD leaks, and it costs
   the multi-revision tasks that several suites depend on.

Until one lands, an `all`-endpoint arm would amplify a known contamination path.

## Consequences for the current 3-arm study

Cross-revision contamination is a confound in the **MCP-vs-baseline headline**:
it affects both MCP arms (`mcp_only` and `hybrid`) while leaving `baseline`
untouched, so it is asymmetric across arms, and it is the same class of problem
as the `docker-cp`/`pt0n` contamination already tracked. Fifteen runs across ten
tasks demonstrably read wrong-revision source — the earlier "`mcp_only`-only"
framing is retracted above (it was an artifact of the preamble-derived audit;
`hybrid` read wrong-revision source in 2 runs). Whether to rescore or exclude
them is filed as its own bead — it is a scoring-integrity decision, not an
indexing one, and it should not be settled inside this assessment.
