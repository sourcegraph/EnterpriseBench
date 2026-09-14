#!/usr/bin/env bash
# Checkpoint 1: Verify agent identified all repos requiring changes
set -euo pipefail

ANSWER="${WORKSPACE:-/workspace}/REFACTOR_PLAN.md"
if [[ ! -f "$ANSWER" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "REFACTOR_PLAN.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=3
# Repo-specific artifacts only — the repo names themselves are given in the
# prompt's environment list, so a correct answer must additionally name the
# concrete class/file each repo's change lands in (per expected_solution.json's
# required_files) to earn credit.
if grep -qiE 'ConcurrentMessageListenerContainer|spring-kafka/src/main/java/org/springframework/kafka/listener' "$ANSWER"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'MessageHandlerMethodFactory|spring-messaging/src/main/java/org/springframework/messaging' "$ANSWER"; then FOUND=$((FOUND + 1)); fi
if grep -qiE 'KafkaAutoConfiguration|KafkaProperties|spring-boot-autoconfigure.*kafka' "$ANSWER"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge "$TOTAL" ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Found %d/%d repo-specific artifacts in plan"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
