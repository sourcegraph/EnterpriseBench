#!/usr/bin/env bash
# check_error_source.sh -- file discovery: agent identified the source files across httpx, httpcore, and h11

set -euo pipefail

export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"
export GT_FILE="$TASK_DIR/ground_truth.json"

exec python3 -m eb_verify.plugins.file_extraction \
    --keys source_files,files,error_source.files
