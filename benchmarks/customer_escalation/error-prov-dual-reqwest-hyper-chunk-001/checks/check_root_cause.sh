#!/usr/bin/env bash
# check_root_cause.sh — Agent's answer.json names the source files on the failure path across hyper and reqwest — including the true fix site (src/proto/h1/decode.rs) — scored as F1 (precision+recall) over ground_truth.json required_files, so a bare-basename dictionary or an over-listed dump falls below pass, which the prompt never names.
#
# Scored as F1 (precision+recall) over the oracle paths in ground_truth.json,
# instruction.md never names. The old grep looked for symptom keywords the
# PROMPT ITSELF supplies, so `cp instruction.md agent_output/answer.json` —
# zero investigation — scored up to 1.00 on this checkpoint (bead jn73.2.7, D1).
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"

export ANSWER_FILE="$WORKSPACE/agent_output/answer.json"
export GT_FILE="$TASK_DIR/ground_truth.json"

exec python3 -m eb_verify.plugins.localization_evidence \
    --keys source_files,files,error_source.files,code_paths,citations
