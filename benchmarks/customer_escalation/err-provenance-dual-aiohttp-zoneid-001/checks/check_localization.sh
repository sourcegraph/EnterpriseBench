#!/usr/bin/env bash
# check_localization.sh — Agent's answer.json names the source files on the failure path across yarl and aiohttp — including the true fix site (yarl/_url.py) — scored as recall over ground_truth.json required_files, which the prompt never names.
#
# Scored as recall over the oracle paths in ground_truth.json, which
# instruction.md never names. The old grep looked for symptom keywords the
# PROMPT ITSELF supplies, so `cp instruction.md agent_output/answer.json` —
# zero investigation — scored up to 1.00 on this checkpoint (bead jn73.2.7, D1).
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"

export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"
export GT_FILE="$TASK_DIR/ground_truth.json"

exec python3 -m eb_verify.plugins.file_extraction \
    --keys source_files,files,error_source.files,code_paths,citations
