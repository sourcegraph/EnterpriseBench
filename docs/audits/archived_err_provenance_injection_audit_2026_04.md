# Security Audit: Archived err-provenance-{04..010} Injection Pattern

**Bead:** EnterpriseBench-wkm
**Date:** 2026-04-29

## Summary

Seven archived check scripts under `benchmarks/_archived/customer_escalation/err-provenance-{04,05,06,07,08,09,010}/checks/check_error_source.sh` retain the shell-injection-prone pattern that was fixed for the active suite under EnterpriseBench-0rv.23. They are NOT executed by any active runner, but must be patched before any future un-archive.

## Scope

| Path | Pattern present | In active suite? |
|---|---|---|
| `_archived/customer_escalation/err-provenance-04/checks/check_error_source.sh` | yes | no |
| `_archived/customer_escalation/err-provenance-05/checks/check_error_source.sh` | yes | no |
| `_archived/customer_escalation/err-provenance-06/checks/check_error_source.sh` | yes | no |
| `_archived/customer_escalation/err-provenance-07/checks/check_error_source.sh` | yes | no |
| `_archived/customer_escalation/err-provenance-08/checks/check_error_source.sh` | yes | no |
| `_archived/customer_escalation/err-provenance-09/checks/check_error_source.sh` | yes | no |
| `_archived/customer_escalation/err-provenance-010/checks/check_error_source.sh` | yes | no |

Cross-reference confirmation: `grep -r "err-provenance-(04|...)" benchmarks/ scripts/` returns no hits outside `_archived/`. CLAUDE.md and `tests/test_task_output_path_consistency.py` skip `_archived/`. The runner (`scripts/orchestration/run_task.py`) does not enumerate archived tasks.

## Vulnerable pattern

```bash
GT_FILES=$(python3 -c "
import json, os
gt = json.load(open(os.environ['GT_FILE']))
for f in gt.get('required_files', []):
    print(f['path'])
")

# … later …

python3 -c "
import json
gt_files = '''$GT_FILES'''.strip().split('\n')
agent_files = '''$AGENT_FILES'''.strip().split('\n')
…
"
```

The third `python3 -c` block interpolates shell-captured variable bodies directly into Python source via `'''$GT_FILES'''`. If a payload contains `'''` or `\n` followed by Python code, it would escape the literal and execute arbitrary code in the verifier's privileges. Although the inputs are read from a trusted ground truth file and the agent's `answer.json`, the agent is in scope as a partial adversary in a benchmark context — and a corrupted `answer.json` could pivot through this surface.

## Safe template (from 0rv.23)

The active err-provenance tasks (`err-provenance-01`, `err-provenance-02`, `err-provenance-03`, and the dual variants) replaced the inline interpolation with a delegated Python module:

```bash
#!/usr/bin/env bash
set -euo pipefail
export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"
export GT_FILE="$TASK_DIR/ground_truth.json"
exec python3 -m eb_verify.plugins.file_extraction \
    --keys source_files,files,error_source.files \
    --policy suffix
```

Data flows entirely through environment variables and JSON file reads — no shell-substitution into Python source.

## Action items if any of the 7 are un-archived

1. Replace the entire `check_error_source.sh` contents with the safe template above.
2. Adjust `--keys` per the task's expected answer schema and `--policy` per its matching strictness if the task requires non-default behavior.
3. Add the resurrected task path to `tests/test_support_mapping_verifiers.py` or the equivalent suite-level regression test.
4. Run a smoke-execution against a mocked `answer.json` to confirm the new verifier scores correctly before re-publishing.

## Disposition

**No code changes in this bead.** The archived scripts are not on any active execution path; patching them now would dirty the archive without security benefit. This audit is the durable artifact: any future unarchiver MUST consult it before reactivating these tasks.
