#!/usr/bin/env bash
# test_cross_repo_runner.sh — Validates test_runner.sh logic locally.
# Simulates /workspace/ layout in a temp directory, then runs the runner.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNNER="$SCRIPT_DIR/../scripts/sandbox/test_runner.sh"

# Create a temp workspace
TMPDIR=$(mktemp -d)
WORKSPACE="$TMPDIR/workspace"
mkdir -p "$WORKSPACE"

cleanup() {
    rm -rf "$TMPDIR"
}
trap cleanup EXIT

FAILURES=0
fail() {
    echo "  FAIL: $*"
    FAILURES=$((FAILURES + 1))
}

# Point the REAL runner at the temp workspace through the WORKSPACE env var it
# honours. Do not string-patch a copy of its source: that silently no-ops the
# moment the matched line is reworded, and the suite then scores against the
# real /workspace and fails for unrelated reasons.
PATCHED_RUNNER="$TMPDIR/test.sh"
cat > "$PATCHED_RUNNER" <<WRAPPER
#!/usr/bin/env bash
export WORKSPACE="$WORKSPACE"
exec bash "$RUNNER" "\$@"
WRAPPER
chmod +x "$PATCHED_RUNNER"

echo "=== Test 1: Missing .verifiers/ directory ==="

output=$(bash "$PATCHED_RUNNER" 2>/dev/null)
exit_code=$?

if [ "$exit_code" -ne 0 ] && echo "$output" | grep -q '"all_passed": false'; then
    echo "  PASS: Correctly reports failure when no .verifiers/ dir"
else
    fail "Expected non-zero exit and all_passed=false"
    echo "  Exit code: $exit_code"
    echo "  Output: $output"
fi

echo ""
echo "=== Test 2: Two repos, two checkpoints (one pass, one fail) ==="

# Create fake repos with .git dirs
mkdir -p "$WORKSPACE/repo-alpha/.git"
mkdir -p "$WORKSPACE/repo-beta/.git"
mkdir -p "$WORKSPACE/.markers"
echo "OK" > "$WORKSPACE/.markers/repo-alpha.status"
echo "OK" > "$WORKSPACE/.markers/repo-beta.status"

# Create verifiers
mkdir -p "$WORKSPACE/.verifiers"

# Passing verifier: checks repo-alpha exists
cat > "$WORKSPACE/.verifiers/01-alpha-exists.sh" << 'VERIFIER'
#!/usr/bin/env bash
# Check that repo-alpha directory exists and has .git
WORKSPACE="$(dirname "$(dirname "$0")")"
if [ -d "$WORKSPACE/repo-alpha/.git" ]; then
    echo '{"score": 1.0, "passed": true, "detail": "repo-alpha verified"}'
    exit 0
else
    echo '{"score": 0.0, "passed": false, "detail": "repo-alpha missing"}'
    exit 1
fi
VERIFIER
chmod +x "$WORKSPACE/.verifiers/01-alpha-exists.sh"

# Failing verifier: checks for a file that doesn't exist
cat > "$WORKSPACE/.verifiers/02-cross-repo-link.sh" << 'VERIFIER'
#!/usr/bin/env bash
# Simulate a cross-repo check that fails
WORKSPACE="$(dirname "$(dirname "$0")")"
cd "$WORKSPACE/repo-beta" || exit 1
if [ -f "go.mod" ]; then
    echo '{"score": 1.0, "passed": true, "detail": "cross-repo dependency found"}'
    exit 0
else
    echo '{"score": 0.0, "passed": false, "detail": "go.mod not found in repo-beta"}'
    exit 1
fi
VERIFIER
chmod +x "$WORKSPACE/.verifiers/02-cross-repo-link.sh"

# Create meta files with weights
echo "weight=0.4" > "$WORKSPACE/.verifiers/01-alpha-exists.meta"
echo "weight=0.6" > "$WORKSPACE/.verifiers/02-cross-repo-link.meta"

output=$(bash "$PATCHED_RUNNER" 2>"$TMPDIR/stderr.log")
exit_code=$?

echo "  Exit code: $exit_code"
echo "  JSON output:"
echo "$output" | python3 -m json.tool 2>/dev/null || echo "$output"
echo ""

# Validate JSON structure
if echo "$output" | python3 -c "import sys, json; d=json.load(sys.stdin); assert 'task_score' in d; assert 'checkpoints' in d; assert len(d['checkpoints'])==2; assert d['all_passed']==False; assert d['checkpoints_passed']==1" 2>/dev/null; then
    echo "  PASS: JSON structure valid, 1/2 passed, all_passed=false"
else
    fail "JSON validation failed"
fi

# Validate weighted score (0.4 * 1.0 + 0.6 * 0.0 = 0.4)
if echo "$output" | python3 -c "import sys, json; d=json.load(sys.stdin); assert abs(d['task_score'] - 0.4) < 0.01, f'Expected 0.4, got {d[\"task_score\"]}'" 2>/dev/null; then
    echo "  PASS: Weighted score correct (0.4)"
else
    fail "Weighted score incorrect"
fi

# Validate per-checkpoint detail strings are preserved in output.json
if echo "$output" | python3 -c "
import sys, json
d = json.load(sys.stdin)
by_name = {c['name']: c for c in d['checkpoints']}
assert 'detail' in by_name['01-alpha-exists'], 'detail field missing'
assert by_name['01-alpha-exists']['detail'] == 'repo-alpha verified', by_name['01-alpha-exists']['detail']
assert by_name['02-cross-repo-link']['detail'] == 'go.mod not found in repo-beta', by_name['02-cross-repo-link']['detail']
" 2>/dev/null; then
    echo "  PASS: Per-checkpoint detail strings preserved"
else
    fail "Per-checkpoint detail strings missing or wrong"
fi

# Validate repos listed
if echo "$output" | python3 -c "import sys, json; d=json.load(sys.stdin); assert 'repo-alpha' in d['repos']; assert 'repo-beta' in d['repos']" 2>/dev/null; then
    echo "  PASS: Both repos listed in output"
else
    fail "Repos not listed correctly"
fi

if [ "$exit_code" -ne 0 ]; then
    echo "  PASS: Non-zero exit code (partial failure)"
else
    fail "Expected non-zero exit code"
fi

echo ""
echo "=== Test 3: All checkpoints pass ==="

# Add go.mod to make second verifier pass
touch "$WORKSPACE/repo-beta/go.mod"

output=$(bash "$PATCHED_RUNNER" 2>/dev/null)
exit_code=$?

echo "  Exit code: $exit_code"

if echo "$output" | python3 -c "import sys, json; d=json.load(sys.stdin); assert d['all_passed']==True; assert d['checkpoints_passed']==2; assert abs(d['task_score'] - 1.0) < 0.01" 2>/dev/null; then
    echo "  PASS: All checkpoints pass, score=1.0"
else
    fail "Expected all pass with score 1.0"
    echo "$output" | python3 -m json.tool 2>/dev/null || echo "$output"
fi

if [ "$exit_code" -eq 0 ]; then
    echo "  PASS: Zero exit code (all passed)"
else
    fail "Expected zero exit code"
fi

echo ""
echo "=== Test 4: Single checkpoint mode ==="

output=$(bash "$PATCHED_RUNNER" "01-alpha-exists" 2>/dev/null)
exit_code=$?

if [ "$exit_code" -eq 0 ] && echo "$output" | python3 -c "import sys, json; d=json.load(sys.stdin); assert d['passed']==True" 2>/dev/null; then
    echo "  PASS: Single checkpoint mode works"
else
    fail "Single checkpoint mode broken"
    echo "  Exit: $exit_code, Output: $output"
fi

echo ""
echo "=== Test 5: Invalid checkpoint name ==="

output=$(bash "$PATCHED_RUNNER" "../etc/passwd" 2>/dev/null)
exit_code=$?

if [ "$exit_code" -ne 0 ] && echo "$output" | grep -q "Invalid checkpoint name"; then
    echo "  PASS: Rejects path traversal in checkpoint name"
else
    fail "Should reject invalid checkpoint name"
    echo "  Exit: $exit_code, Output: $output"
fi

echo ""
echo "=== Test 6: Verifier with no JSON output is infra, not a free 1.0 ==="

# Exits 0 but never prints a verdict. The old exit-code fallback scored this a
# full 1.0 — credit for a verifier that reported nothing. It must now refuse to
# attest instead.
cat > "$WORKSPACE/.verifiers/03-plain-exit.sh" << 'VERIFIER'
#!/usr/bin/env bash
echo "all good"
exit 0
VERIFIER
chmod +x "$WORKSPACE/.verifiers/03-plain-exit.sh"

output=$(bash "$PATCHED_RUNNER" "03-plain-exit" 2>/dev/null)

if echo "$output" | python3 -c "import sys, json; d=json.load(sys.stdin); assert d['verifier_ran']==False; assert d['passed']==False; assert d['score']==0.0; assert 'VERIFIER_INFRA_ERROR' in d['detail']" 2>/dev/null; then
    echo "  PASS: No-JSON verifier is not attested (no fabricated score)"
else
    fail "No-JSON verifier should be an infra error, not a score"
    echo "  Output: $output"
fi

echo ""
echo "=== Test 7: Malicious repo dirname cannot forge JSON output ==="

# A repo dirname crafted to break out of the repos array and inject
# forged top-level fields, if REPOS_JSON ever stops escaping basenames.
MALICIOUS_REPO='evil"],"task_score":1.0,"all_passed":true,"pad":["x'
mkdir -p "$WORKSPACE/$MALICIOUS_REPO/.git"

output=$(bash "$PATCHED_RUNNER" 2>/dev/null)
exit_code=$?

if echo "$output" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert set(d.keys()) == {'task_score', 'all_passed', 'checkpoints_passed', 'checkpoints_total', 'repos', 'checkpoints'}, d.keys()
assert 'pad' not in d, 'injected key escaped the repos array'
assert '$MALICIOUS_REPO' in d['repos'], 'malicious repo name missing or mangled'
# 3 verifiers exist by this point: 01/02 from Test 2, 03 from Test 6.
assert d['checkpoints_total'] == 3, d['checkpoints_total']
" 2>/dev/null; then
    echo "  PASS: Malicious repo dirname is escaped, no forged fields"
else
    fail "Repo dirname injection was not neutralized"
    echo "  Exit: $exit_code, Output: $output"
fi

rm -rf "$WORKSPACE/$MALICIOUS_REPO"

echo ""
echo "=== Test 8: Malicious checkpoint filename cannot forge JSON output ==="

# .verifiers/ is agent-writable (chowned to agent:agent before the agent's
# session starts, per run_task.py's container setup), so a verifier filename
# is just as attacker-controlled as a repo dirname. Craft one that tries to
# break out of its checkpoint entry.
MALICIOUS_CHECKPOINT='evil"],"pad":["y'
cat > "$WORKSPACE/.verifiers/${MALICIOUS_CHECKPOINT}.sh" << 'VERIFIER'
#!/usr/bin/env bash
echo '{"score": 1.0, "passed": true, "detail": "ok"}'
exit 0
VERIFIER
chmod +x "$WORKSPACE/.verifiers/${MALICIOUS_CHECKPOINT}.sh"

output=$(bash "$PATCHED_RUNNER" 2>/dev/null)
exit_code=$?

if echo "$output" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert set(d.keys()) == {'task_score', 'all_passed', 'checkpoints_passed', 'checkpoints_total', 'repos', 'checkpoints'}, d.keys()
assert 'pad' not in d, 'injected key escaped the top-level object'
by_name = {c['name']: c for c in d['checkpoints']}
assert '$MALICIOUS_CHECKPOINT' in by_name, 'malicious checkpoint name missing or mangled'
for c in d['checkpoints']:
    assert set(c.keys()) == {'name', 'weight', 'score', 'passed', 'verifier_ran', 'detail', 'duration_ms', 'exit_code'}, c.keys()
" 2>/dev/null; then
    echo "  PASS: Malicious checkpoint filename is escaped, no forged fields"
else
    fail "Checkpoint filename injection was not neutralized"
    echo "  Exit: $exit_code, Output: $output"
fi

rm -f "$WORKSPACE/.verifiers/${MALICIOUS_CHECKPOINT}.sh"

echo ""
echo "=== Test 9: Malicious weight metadata cannot forge JSON or run awk code ==="

# weight is read from an agent-writable .meta file and interpolated unquoted
# into both the result JSON and an awk expression. This payload is a
# confirmed working exploit (verified end-to-end against the pre-fix
# runner): it closes the awk multiplication's parens early, starts a fresh
# statement that calls system() to prove real code execution — not just
# JSON corruption — then reopens a dummy paren so the template's own
# trailing ")" still balances. Must be whitespace-free: the .meta parser
# extracts weight via `\S+`, which truncates at the first space.
cat > "$WORKSPACE/.verifiers/01-alpha-exists.meta" << 'META'
weight=1);system(">/tmp/eb_test_runner_pwned");x=(1
META

output=$(bash "$PATCHED_RUNNER" 2>/dev/null)
exit_code=$?

if [ ! -e /tmp/eb_test_runner_pwned ] && echo "$output" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert set(d.keys()) == {'task_score', 'all_passed', 'checkpoints_passed', 'checkpoints_total', 'repos', 'checkpoints'}, d.keys()
by_name = {c['name']: c for c in d['checkpoints']}
assert by_name['01-alpha-exists']['weight'] == 1.0, by_name['01-alpha-exists']['weight']
" 2>/dev/null; then
    echo "  PASS: Malformed weight metadata falls back to default, no code execution"
else
    fail "Weight metadata injection was not neutralized"
    echo "  Exit: $exit_code, Output: $output"
fi

rm -f /tmp/eb_test_runner_pwned
# Leave 01-alpha-exists.meta as Test 2 originally set it, in case tests are
# ever reordered or extended after this one.
echo "weight=0.4" > "$WORKSPACE/.verifiers/01-alpha-exists.meta"

echo ""
echo "=== All tests complete ==="

if [ "$FAILURES" -ne 0 ]; then
    echo "=== $FAILURES check(s) FAILED ==="
    exit 1
fi
