#!/usr/bin/env bash
# Checkpoint 2: Verify agent traced the ObjectMapper configuration chain across repos.
# Repo-specific artifacts only -- generic chain language (ObjectMapper, Module bean,
# REST serialization) is given in the prompt and must not earn credit on its own.
set -euo pipefail

REPORT="${WORKSPACE:-/workspace}/DEPENDENCY_TRACE.md"

if [[ ! -f "$REPORT" ]]; then
  printf '{"score": 0.0, "passed": false, "reason": "DEPENDENCY_TRACE.md not found"}\n'
  exit 0
fi

FOUND=0
TOTAL=4

# 1. Names the specific bean that bridges Spring Boot autoconfiguration into
#    the Spring Framework builder -- not stated anywhere in the prompt.
if grep -qiE 'Jackson2ObjectMapperBuilderCustomizer' "$REPORT"; then FOUND=$((FOUND + 1)); fi

# 2. Names the jackson-databind API that actually attaches a Module to the
#    ObjectMapper -- the prompt only says "module registration", not the method.
if grep -qiE 'registerModule(s)?\(|findModules|ObjectMapper\.registerModule' "$REPORT"; then FOUND=$((FOUND + 1)); fi

# 3. Cites at least two of the three canonical files by basename -- evidence
#    the agent actually crossed repo boundaries rather than describing the
#    chain in the abstract.
FILES_CITED=0
for pat in 'ObjectMapper\.java' 'MappingJackson2HttpMessageConverter\.java' 'JacksonAutoConfiguration\.java'; do
  if grep -qiE "$pat" "$REPORT"; then FILES_CITED=$((FILES_CITED + 1)); fi
done
if [ "$FILES_CITED" -ge 2 ]; then FOUND=$((FOUND + 1)); fi

# 4. Names the conditional annotation that gates the autoconfiguration class --
#    the mechanism, not just the fact that autoconfiguration exists.
if grep -qiE '@ConditionalOnClass|@ConditionalOnMissingBean|@ConditionalOnBean' "$REPORT"; then FOUND=$((FOUND + 1)); fi

SCORE=$(awk "BEGIN {printf \"%.2f\", $FOUND/$TOTAL}")
if [ "$FOUND" -ge 3 ]; then PASSED=true; else PASSED=false; fi

printf '{"score": %s, "passed": %s, "reason": "Matched %d/%d integration path signals"}\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
