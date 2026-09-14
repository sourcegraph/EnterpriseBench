# Oracle Curator Decision: EnterpriseBench

> Tracks the cross-rig adoption decision required by codeprobe bead
> [codeprobe-zf3k](../../.beads/issues.jsonl) ("Adopt multi-backend oracle
> curator pattern in EnterpriseBench and CodeScaleBench").

## Decision: NOT-APPLICABLE (with a small provenance shim)

EnterpriseBench's ground truth is **not constructed by running search
backends**, so there is no single-tool oracle bias to mitigate. The
codeprobe-zat9 multi-backend curator algorithm does not transfer.

The shim that *is* needed: emit an `oracle_backends_consensus` field in
`ground_truth.json` so codeprobe's `core/bias_detection.py` can ingest
EnterpriseBench artifacts uniformly with codeprobe-mined and CSB-mined
tasks. The value is derived from the existing per-file `source` field
(`deterministic | curator | both`) — no curator changes, no re-mining.

## Why the curator pattern doesn't apply

EnterpriseBench task families derive ground truth from **real PR /
incident history**, not from running grep, AST walks, or Sourcegraph
queries:

| Task type                     | Ground truth source                                                           |
| ----------------------------- | ----------------------------------------------------------------------------- |
| `api_contract`                | `producer_changed_files` + `consumer_affected_files` from the breaking-change PR |
| `dependency_graph`            | Pinned upstream/downstream commit pairs from real bumps                       |
| `db_schema_evolution`         | `required_files` extracted from migration PRs                                 |
| `incident_investigation`      | `fix_files` from the merged-fix PR; `root_cause.file` is hand-curated         |
| `dead_code_necropsy`          | Hand-authored dead-code lists with architectural evidence per file            |
| `support_code_mapping`        | `required_files` extracted from the supporting PR diff                        |
| `refactor_orchestration`      | Files touched by the historical refactor PR                                   |
| `monorepo_boundary`           | Files crossing the boundary in the historical change                          |
| `error_provenance`            | Error chain authored from real incident postmortems                           |
| `config_drift`                | Config files referenced in the historical drift report                        |

There is no construction-time symbol search whose answer key would change
if you ran `grep` instead of `sg_find_references`. The oracle is the
**actual merged change**.

## Existing provenance signal

`schemas/task.schema.json` already records per-file source:

```json
{
  "required_files": [
    {
      "path": "django/db/models/fields/__init__.py",
      "repo": "django",
      "confidence": 0.95,
      "source": "deterministic"
    }
  ]
}
```

`source` is one of `deterministic | curator | both`:

* `deterministic` — extracted from the PR diff / breaking-change record.
  Equivalent to "no semantic judgement, raw history".
* `curator` — added by a human / LLM reviewer during ground-truth
  curation. Equivalent to "judgement applied".
* `both` — the file was already in the deterministic extract *and*
  confirmed by curator review.

`scripts/validation/enable_llm_curator.py` is orthogonal: it opts a task
into a `verification_modes = ["deterministic", "llm_curator"]` mode at
**scoring time**, where the LLM curator becomes a second judge alongside
the deterministic checks. It does **not** touch construction-time
provenance.

## Cross-rig provenance shim

To keep codeprobe bias detection consistent across rigs, every
`ground_truth.json` should also carry a top-level
`oracle_backends_consensus` list. The mapping from existing per-file
`source` values:

| Per-file `source` set found in `ground_truth.json` | `oracle_backends_consensus`           |
| -------------------------------------------------- | ------------------------------------- |
| only `"deterministic"`                             | `["deterministic"]`                   |
| only `"curator"`                                   | `["curator"]`                         |
| `"both"` (or the union of `deterministic`+`curator`) | `["curator", "deterministic"]`        |
| empty / no provenance                              | field omitted (codeprobe falls back)  |

These labels are **not** Sourcegraph/grep/AST tools — codeprobe's bias
detector matches names structurally, so EnterpriseBench's `deterministic`
and `curator` labels surface as their own non-overlapping backend names.
In practice no codeprobe MCP config will ever match `"deterministic"`,
which is the correct outcome: EnterpriseBench tasks will never trigger
the `backend_overlap` warning, because there is no overlap to flag.

The shim still adds value:

* `no_independent_baseline` is correctly suppressed (the GT backend isn't
  reachable from any agent's tool surface, so no warning fires — matches
  the actual situation).
* If a future EnterpriseBench task type *does* introduce search-derived
  ground truth (e.g. a new symbol-trace family that calls
  `sg_find_references` at construction time), updating its writer to put
  `"sourcegraph"` in the `oracle_backends_consensus` field is a
  one-character change.

## Implementation

`scripts/validation/emit_oracle_consensus.py` (introduced under
codeprobe-zf3k) walks `benchmarks/` and adds the
`oracle_backends_consensus` field to every `ground_truth.json` that has a
typed `source` on its files. The script is idempotent: re-running it
overwrites the field with the same value, and it is a no-op for files
that already match.

Run it as part of the existing validation pass:

```bash
python3 scripts/validation/emit_oracle_consensus.py benchmarks/
python3 scripts/validation/emit_oracle_consensus.py benchmarks/ --dry-run
python3 scripts/validation/emit_oracle_consensus.py benchmarks/ --json
```

## Out of scope

* Porting `mining/oracle_curator.py` from codeprobe. Their `BackendResult`
  shape requires per-tool file lists, which EnterpriseBench has no source
  to populate.
* Changes to `enable_llm_curator.py`. The verification-time curator stays
  as a second judging signal; this bead is about construction-time
  provenance.
* Re-running deterministic extractors on `_archived/` tasks.

## Cross-rig consistency check

After the shim runs, codeprobe ingesting an EnterpriseBench task reports
`oracle_backends_consensus = ["deterministic"]` (or the appropriate
union). Codeprobe `tests/test_bias_detection.py` exercises the reader
path; `tests/test_emit_oracle_consensus.py` (this repo) pins the writer
behavior so any future schema change to `source` surfaces immediately.
