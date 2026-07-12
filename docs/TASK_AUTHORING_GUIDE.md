# Task Authoring Guide

How to add a new benchmark task to EnterpriseBench.

## File Structure

Each task lives in `benchmarks/{suite}/{task-id}/` with this layout:

```
benchmarks/feature_delivery/monorepo-boundary-001/
  task.toml          # Task definition (validated against schemas/task.schema.json)
  instruction.md     # Natural-language prompt shown to the agent
  ground_truth.json  # Expected files, symbols, and provenance data
  checks/            # Checkpoint verifier scripts
    check_affected_packages.sh
    check_impact_classification.sh
    check_boundary_violations.sh
```

## Step-by-Step

### 1. Choose Suite and Task Type

Pick from the 7 suites defined in `schemas/task.schema.json`:

| Suite | Example Task Types |
|-------|-------------------|
| dependency_management | dep_traversal, api_contract |
| incident_response | incident_investigation |
| platform_engineering | config_drift |
| security_operations | rbac_audit |
| customer_escalation | error_provenance, support_code_mapping |
| feature_delivery | monorepo_boundary, db_schema_evolution |
| technical_debt | refactor_orchestration, dead_code_necropsy |

### 2. Write task.toml

The task definition follows `schemas/task.schema.json`. Key sections:

```toml
# Header
difficulty_stratum = "dual_repo"  # calibration|large_single|dual_repo|multi_repo|monorepo_cross_package

[task]
id = "api-contract-001"           # Must match pattern: {prefix}-{number}
suite = "dependency_management"
task_type = "api_contract"
difficulty = "medium"             # easy|medium|hard|expert
estimated_duration_minutes = 30
session_type = "single"           # single|chain|event_replay|resume
description = "One-line description of the task"
prompt = """
Multi-line prompt describing what the agent should do.
Reference specific files, repos, and expected output paths.
"""

[[repos]]
url = "github.com/owner/repo"
rev = "v1.2.3"                    # Pin to a specific tag or commit
path = "repo-name"                # Cloned to /workspace/{path}/
role = "primary"                  # primary|dependency|consumer

[metadata]
languages = ["go", "python"]
total_loc = 150_000
dependency_depth = 2
frameworks = ["grpc"]
multi_repo_pattern = "propagate"  # propagate|investigate|enforce|orchestrate

[[checkpoints]]
name = "identify_breaking_change"
weight = 0.40                     # Weights across all checkpoints should sum to ~1.0
verifier = "checks/check_breaking.sh"
description = "Agent identifies the breaking API change"
timeout_seconds = 60

[artifacts]
required = ["answer"]             # What the agent must produce
optional = ["migration_guide"]

[tool_access]
expected_mcp_benefit = "high"     # none|low|medium|high
mcp_benefit_rationale = "Why MCP helps or doesn't for this task"

[ground_truth]
tiers = ["deterministic"]         # deterministic|llm_curator|solve_verification

[[ground_truth.required_files]]
path = "src/api/handler.go"
repo = "repo-name"
confidence = 0.98
source = "deterministic"
```

### 3. Write instruction.md

This is the natural-language prompt shown to the agent. Write it as if you are a senior engineer asking a colleague for help:

- Set the context (what project, what happened, why it matters)
- Describe the specific deliverable (report, patch, config, etc.)
- Specify the output path (e.g., `/workspace/repo/IMPACT_REPORT.md`)
- Do NOT give away the answer or list the exact files to look at

See `benchmarks/feature_delivery/monorepo-boundary-001/instruction.md` for a good example.

**Gate B (leakage classifier)** checks this automatically once `ground_truth.json` /
`task.toml [ground_truth]` exist:

```bash
python3 scripts/validation/leak_classifier.py benchmarks/{suite}/{task-id}
```

It classifies the instruction against `required_files`/`sufficient_files` into a leak
bucket: `exact_path` and `dir` reject (the instruction hands the agent the answer's
location); `behavioral` and `grep_hard` admit.

**There is no waiver.** If Gate B REJECTs, the only remedy is to reword the instruction
to describe the *problem* (symptom, context, desired output) rather than naming the file
or directory that holds the fix. A task that names its own oracle cannot separate
retrieval from grep, which is the contrast this benchmark exists to measure, so the gate
fails closed and stays that way. The same `decide()` call backs both this CLI and the
stratum selector's Gate B, so the answer you get here is the answer the selector gets.

> An earlier revision let a `[[qa.waivers]]` entry admit an `exact_path` leak when the
> named path was a *given entry point* whose real discovery target lay downstream. It was
> withdrawn: the guard meant to verify "there IS something downstream" compared a
> two-segment leak fragment against full oracle paths and so never rejected anything, and
> the underlying claim is untestable with the measurements we have (Gate C's
> turns-to-first-relevant counts the waived file itself as a hit). To restore it, see
> `EnterpriseBench-jn73.2.12`. Do not re-add a waiver field in the meantime — nothing
> reads it.

The `symbol` bucket exists in `classify()` but is **dormant**: `decide()` passes
`oracle_symbols=None` because EB's ground truth records files, not symbols, so no task
can currently land in it. Extracting symbols heuristically from an instruction would be
exactly the semantic guessing this gate is built to avoid. It is wired the day the corpus
carries symbol-level ground truth, not before.

### 4. Write ground_truth.json

Provide the provenance data that verifiers use to score the agent's output:

```json
{
  "candidate_id": 1,
  "repo": "owner/repo",
  "pr_refs": ["#1234"],
  "description": "What this ground truth captures",
  "affected_dependents": [
    {
      "name": "package-name",
      "path": "packages/package-name",
      "semver_bump": "patch",
      "affected_files": ["path/to/file.ts"],
      "confidence": 0.98
    }
  ]
}
```

### 5. Write Checkpoint Verifier Scripts

Each checkpoint has a shell script in `checks/` that produces JSON output. The verifier pattern:

```bash
#!/usr/bin/env bash
# Checkpoint: verify agent identified affected packages
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/repo/IMPACT_REPORT.md"

# Check if required output exists
if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "IMPACT_REPORT.md not found"}\n'
  exit 0
fi

# Score based on content
FOUND=0
TOTAL=2
if grep -qiE 'package-a|@scope/package-a' "$REPORT"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'package-b|@scope/package-b' "$REPORT"; then FOUND=$((FOUND + 1)); fi

# Compute score as proper decimal
SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge "$TOTAL" ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d items"}\n' \
  "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
```

**Verifier output format** (must be valid JSON on stdout):
```json
{"score": 0.75, "passed": true, "reason": "Found 3/4 affected packages"}
```

- `score`: float 0.0-1.0
- `passed`: boolean
- `reason`: human-readable explanation
- Exit code is always 0 (errors are reported via the JSON)

### 6. Validate

Run the schema validator to check your task.toml:

```bash
python3 -m lib.eb_verify validate benchmarks/{suite}/{task-id}/task.toml
```

Run the test suite to ensure nothing is broken:

```bash
python3 -m pytest tests/ -q
```

## Security Patterns

When writing verifier scripts, follow these security practices:

1. **Use `os.environ` in Python, not shell interpolation** -- avoids command injection:
   ```python
   # GOOD
   workspace = os.environ.get("WORKSPACE", "/workspace")

   # BAD - shell injection risk
   workspace = subprocess.run(f"echo $WORKSPACE", ...)
   ```

2. **Use heredocs for multi-line content in shell scripts** -- avoids quoting issues:
   ```bash
   # GOOD
   cat <<'EOF' > /tmp/expected.json
   {"key": "value"}
   EOF

   # BAD - variable expansion and quoting issues
   echo '{"key": "value"}' > /tmp/expected.json
   ```

3. **Always quote variables in shell scripts**:
   ```bash
   # GOOD
   if [[ -f "$REPORT" ]]; then

   # BAD
   if [[ -f $REPORT ]]; then
   ```

4. **Use `set -euo pipefail`** at the top of every verifier script.

5. **Validate paths** -- never follow symlinks outside the workspace:
   ```bash
   REPORT=$(realpath -m "${WORKSPACE:-/workspace}/repo/output.md")
   if [[ "$REPORT" != "${WORKSPACE:-/workspace}"* ]]; then
     printf '{"score": 0.0, "passed": false, "reason": "Path traversal detected"}\n'
     exit 0
   fi
   ```

## Checklist

Before submitting a new task:

- [ ] `task.toml` passes schema validation
- [ ] `instruction.md` reads naturally, does not give away the answer
- [ ] `python3 scripts/validation/leak_classifier.py benchmarks/{suite}/{task-id}` reports
      `ADMIT` (Gate B) — or, if it REJECTs a genuine given entry point, a
      `[[qa.waivers]]` entry is recorded with rationale, not a reworded-away task
- [ ] `ground_truth.json` has provenance data with confidence scores
- [ ] Each checkpoint has a verifier script in `checks/`
- [ ] Verifier scripts output valid JSON with score/passed/reason
- [ ] Checkpoint weights sum to approximately 1.0
- [ ] Repo URLs point to real OSS repos with pinned revisions
- [ ] `tool_access.expected_mcp_benefit` is set with rationale
- [ ] All verifier scripts use `set -euo pipefail`
- [ ] No hardcoded secrets or credentials
