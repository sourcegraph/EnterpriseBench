# Benchmark Design Constraints

This file is the canonical, machine-checked statement of structural constraints
on the EnterpriseBench task corpus. It does **not** restate the full PRD — it
records the values that automation enforces so the source of truth is
unambiguous and discoverable.

For motivation, full requirements, and the debate that produced these targets,
see:

- [`docs/internal/PRD.md`](internal/PRD.md)
- [`docs/internal/prd_task_mix_realignment.md`](internal/prd_task_mix_realignment.md)
- [`docs/CONVERGENCE_REPORT_TASK_MIX.md`](CONVERGENCE_REPORT_TASK_MIX.md)

## Task-mix targets

Enforced by [`scripts/validation/task_mix_validator.py`](../scripts/validation/task_mix_validator.py)
(also: `make verify-mix`). Failure surfaces as a non-zero exit and a list of
failed targets.

| Target | Value | Rationale |
| --- | --- | --- |
| Strict multi-repo share | `>= 45%` of all active tasks | Strict multi-repo (`dual_repo`, `tri_repo`, `multi_repo`) is the benchmark's measurement differentiator. |
| Multi-repo variants per task type | `>= 2` for each of the 10 task types | Avoids coverage holes where a task type cannot be measured in the multi-repo regime. |
| Single-ecosystem cap | `<= 40%` of multi-repo tasks for any single ecosystem label | Prevents one ecosystem (currently Go) from dominating multi-repo signal. |

The "ecosystem" label is the union of `metadata.languages` and
`metadata.frameworks` in each task's `task.toml`. A task tagged
`languages = ["go"]` and `frameworks = ["k8s"]` contributes one count to each
of the `go` and `k8s` ecosystem buckets.

## Authoring-time ecosystem balance gate

Enforced by [`scripts/validation/ecosystem_cap_gate.py`](../scripts/validation/ecosystem_cap_gate.py)
(also: `make verify-ecosystem-gate`, and a CI job in `.github/workflows/ci.yml`
on every push to `main`).

The static cap above is descriptive — it tells you whether the corpus is in
balance right now. The gate is **prescriptive** — it blocks commits that would
make a capped ecosystem worse than the baseline (HEAD on local, HEAD~1 in CI).

### Cap values

| Ecosystem | Cap | Source of truth |
| --- | --- | --- |
| `go` | `0.40` | `DEFAULT_CAPS` in `scripts/validation/ecosystem_cap_gate.py`; mirrored by `MAX_SINGLE_ECOSYSTEM_PCT` in `scripts/validation/task_mix_validator.py`. |

Other ecosystems are not currently capped. Add them to `DEFAULT_CAPS` if
imbalance shows up there too. (Out-of-scope today, per the originating bead
EnterpriseBench-8pp.)

### Block / pass logic

For each capped ecosystem `E` with cap `C`:

| Baseline share | Candidate share | Outcome |
| --- | --- | --- |
| `<= C` | `<= C` | **pass** — within cap on both sides. |
| `<= C` | `> C` (and rising) | **block** — this commit pushes `E` over cap. |
| `> C` | `<= C` | **pass** — this commit fixes the imbalance. |
| `> C` | `> C` and `share decreased` | **pass** — balance-correction work. |
| `> C` | `> C` and `share unchanged` | **pass** — legacy state; commit is neutral. |
| `> C` | `> C` and `share increased` | **block** — Go-heavy burst during balance correction. |

The gate keys on **share**, not raw count, so a balance-correction batch that
adds a few Go tasks alongside many non-Go tasks (lowering Go% even as the Go
count creeps up) does not get blocked. This is the scenario the gate was built
to allow.

### Why this gate exists

On 2026-04-28, eleven Go-tagged dual-repo incident-investigation tasks
(`incident-investigation-dual-cilium-001` through similar) landed during an
active Go-balance correction effort, pushing multi-repo Go% from 40.7% to
46.2% and undoing the work in progress. The audit
([`docs/audits/go_balance_audit_2026_04.md`](audits/go_balance_audit_2026_04.md))
confirmed that none of the 11 tags were defensibly removable — they were all
genuinely Go-essential — so balance can only be restored by adding non-Go
multi-repo tasks. The gate prevents this scenario from recurring: adding more
Go-tagged tasks while Go% is over cap will fail CI, regardless of how
defensible the individual task is.

### Bypassing the gate

The gate is enforced in CI; there is no documented bypass. If you need to land
a Go-tagged multi-repo task while Go% is over cap, either:

1. Land it in the same commit as one or more non-Go multi-repo tasks that keep
   Go's share from rising, or
2. Wait until follow-up authoring brings Go% back under 40%, or
3. Drop the `go` tag if the task is only incidentally Go (rare — the audit
   found 0 of 11 candidates qualified).

The gate is intentionally simple and does not understand "this task is more
important than balance." That is a judgment call for the authoring lane, not
the gate.

## Test coverage

- Unit tests for the gate live in
  [`tests/test_ecosystem_cap_gate.py`](../tests/test_ecosystem_cap_gate.py).
- Smoke tests verify the bead's acceptance criteria: a 12th Go task fires the
  gate; adding a non-Go task passes.
