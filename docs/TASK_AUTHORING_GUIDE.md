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

#### Scoring-integrity rules for check scripts

The runner refuses to invent a verdict a verifier never gave: a score is recorded
only if the verifier actually reached one. Absence of a verdict routes the task to
`verifier_infra_error` (a re-run), never to a 0.0 — a false zero is
indistinguishable from the agent genuinely getting the answer wrong, which
silently corrupts results. Three rules follow:

**Always print JSON.** A check script that exits without printing a JSON verdict
is treated as an infra failure, not as a score. Do not rely on the exit code to
mean pass or fail — the runner no longer reads it that way.

The one exception is the agent's own artifact. Most checks read `answer.json`
through a command substitution, so under `set -euo pipefail` an answer they
cannot parse aborts them before their final `printf` — no verdict, and from the
outside indistinguishable from a verifier that never ran. Treating that as infra
would hand the agent a re-run for emitting garbage, so the runner checks the
artifact itself: if `agent_output/answer.json` is not a JSON object, a check that
dies with a nonzero exit is scored 0.0 and told why. Everything else — a missing
command, a nonexistent interpreter, a check that exits 0 in silence — is still an
infra error. An answer that *is* a JSON object is never short-circuited: your
check does the judging, however wrong the contents.

You do not need to defend against a malformed answer, then. You do still owe the
reader a verdict for every failure you can describe better than the runner can:

```bash
if [[ ! -f "$ANSWER_FILE" ]]; then
  printf '{"score": 0.0, "passed": false, "detail": "no answer.json"}\n'
  exit 1
fi
```

**Probe optional tools with `command -v`, never by running them.** The runner
installs a `command_not_found_handle` in every verifier shell, so *any* command
bash cannot find marks the checkpoint as never-run — even one whose failure your
script recovers from. `command -v` is a bash builtin and does not trip it.

```bash
if command -v helm >/dev/null 2>&1; then   # correct
  ...
else
  ...                                       # fall back
fi

if helm version >/dev/null 2>&1; then      # WRONG: a missing helm now
  ...                                       # fails the whole checkpoint as infra
fi
```

**Never swallow an interpreter failure.** `if python3 ... 2>/dev/null` turns "the
image has no python3" into a clean-looking `{"score": 0.0}`. The runner preflights
the interpreters your scripts reference and refuses to score when one is missing,
but write the check as though it will run — do not paper over a broken image with
a fallback branch that scores the agent.

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
- [ ] `ground_truth.json` has provenance data with confidence scores
- [ ] Each checkpoint has a verifier script in `checks/`
- [ ] Verifier scripts output valid JSON with score/passed/reason
- [ ] Checkpoint weights sum to approximately 1.0
- [ ] Repo URLs point to real OSS repos with pinned revisions
- [ ] `tool_access.expected_mcp_benefit` is set with rationale
- [ ] All verifier scripts use `set -euo pipefail`
- [ ] No hardcoded secrets or credentials
