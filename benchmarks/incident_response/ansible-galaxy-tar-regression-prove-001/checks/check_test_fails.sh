#!/usr/bin/env bash
# check_test_fails.sh — verify regression test fails on buggy code, for the
# RIGHT reason (tied to the Galaxy collection tar-extraction fragility, not a
# stub assertion or an unrelated import/collection error).
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"

TEST_FILE="$WORKSPACE/regression_test.py"
if [[ ! -f "$TEST_FILE" ]]; then
    printf '{"score": 0.0, "passed": false, "detail": "regression_test.py not found"}\n'
    exit 0
fi

# The test must actually reference the real Galaxy collection tar-extraction
# code under test. A garbage/stub file that merely asserts False (which would
# also make pytest exit non-zero) does not.
if ! grep -qE 'ansible\.galaxy\.collection|_ansible_normalized_cache|_extract_tar_dir|install_artifact|concrete_artifact_manager|_get_tar_file_member' "$TEST_FILE"; then
    printf '{"score": 0.0, "passed": false, "detail": "regression_test.py does not reference the Galaxy collection install/tar-extraction code under test"}\n'
    exit 0
fi

# Run the test — it should FAIL on the buggy code.
cd "$WORKSPACE"
EXIT_CODE=0
OUTPUT=$(python3 -m pytest --timeout=60 "$TEST_FILE" -q 2>&1) || EXIT_CODE=$?

if [ "$EXIT_CODE" -eq 0 ]; then
    printf '{"score": 0.0, "passed": false, "detail": "Test passed on buggy code — it should fail"}\n'
    exit 0
fi

# The failure must be tied to the Galaxy collection tar-extraction path, not
# an unrelated collection/import/syntax error (a stub `assert False` or a
# ModuleNotFoundError also exits non-zero but proves nothing about the bug).
if echo "$OUTPUT" | grep -qE 'galaxy/collection/(__init__|concrete_artifact_manager)\.py|_ansible_normalized_cache|_extract_tar_dir|install_artifact'; then
    printf '{"score": 1.0, "passed": true, "detail": "Test correctly fails on buggy code, tied to the Galaxy collection tar extraction path"}\n'
else
    printf '{"score": 0.0, "passed": false, "detail": "Test failed, but the failure output does not tie back to the Galaxy collection tar extraction code"}\n'
fi
