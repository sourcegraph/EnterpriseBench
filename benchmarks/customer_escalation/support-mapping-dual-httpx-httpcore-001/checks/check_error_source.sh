#!/usr/bin/env bash
# check_error_source.sh -- file discovery: agent identified the correct source files across both repos

set -euo pipefail

export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"
export GT_FILE="$TASK_DIR/ground_truth.json"

# In the sandbox, run_task.py stages the harness and exports PYTHONPATH. Host-side
# (eb_verify.runner) it does not, so fall back to the in-repo lib/. Test for the
# package itself, not just the directory: from the container's .verifiers/ dir the
# `..` chain clamps at / and finds the system /lib, which is not our harness.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LIB_DIR="$(cd "$SCRIPT_DIR/../../../../lib" 2>/dev/null && pwd || echo "")"
if [[ -n "$LIB_DIR" && -d "$LIB_DIR/eb_verify" ]]; then
    export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$LIB_DIR"
fi

exec python3 -m eb_verify.plugins.file_extraction \
    --keys source_files,files,error_source.files \
    --policy suffix
