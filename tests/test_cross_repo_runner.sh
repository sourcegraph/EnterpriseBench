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

echo "=== Test 1: Missing .verifiers/ directory ==="
# Patch WORKSPACE in runner
PATCHED_RUNNER="$TMPDIR/test.sh"
sed "s|WORKSPACE=\"/workspace\"|WORKSPACE=\"$WORKSPACE\"|g" "$RUNNER" > "$PATCHED_RUNNER"
chmod +x "$PATCHED_RUNNER"

output=$(bash "$PATCHED_RUNNER" 2>/dev/null)
exit_code=$?

if [ "$exit_code" -ne 0 ] && echo "$output" | grep -q '"all_passed": false'; then
    echo "  PASS: Correctly reports failure when no .verifiers/ dir"
else
    echo "  FAIL: Expected non-zero exit and all_passed=false"
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

# Re-patch and run
sed "s|WORKSPACE=\"/workspace\"|WORKSPACE=\"$WORKSPACE\"|g" "$RUNNER" > "$PATCHED_RUNNER"

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
    echo "  FAIL: JSON validation failed"
fi

# Validate weighted score (0.4 * 1.0 + 0.6 * 0.0 = 0.4)
if echo "$output" | python3 -c "import sys, json; d=json.load(sys.stdin); assert abs(d['task_score'] - 0.4) < 0.01, f'Expected 0.4, got {d[\"task_score\"]}'" 2>/dev/null; then
    echo "  PASS: Weighted score correct (0.4)"
else
    echo "  FAIL: Weighted score incorrect"
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
    echo "  FAIL: Per-checkpoint detail strings missing or wrong"
fi

# Validate repos listed
if echo "$output" | python3 -c "import sys, json; d=json.load(sys.stdin); assert 'repo-alpha' in d['repos']; assert 'repo-beta' in d['repos']" 2>/dev/null; then
    echo "  PASS: Both repos listed in output"
else
    echo "  FAIL: Repos not listed correctly"
fi

if [ "$exit_code" -ne 0 ]; then
    echo "  PASS: Non-zero exit code (partial failure)"
else
    echo "  FAIL: Expected non-zero exit code"
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
    echo "  FAIL: Expected all pass with score 1.0"
    echo "$output" | python3 -m json.tool 2>/dev/null || echo "$output"
fi

if [ "$exit_code" -eq 0 ]; then
    echo "  PASS: Zero exit code (all passed)"
else
    echo "  FAIL: Expected zero exit code"
fi

echo ""
echo "=== Test 4: Single checkpoint mode ==="

output=$(bash "$PATCHED_RUNNER" "01-alpha-exists" 2>/dev/null)
exit_code=$?

if [ "$exit_code" -eq 0 ] && echo "$output" | python3 -c "import sys, json; d=json.load(sys.stdin); assert d['passed']==True" 2>/dev/null; then
    echo "  PASS: Single checkpoint mode works"
else
    echo "  FAIL: Single checkpoint mode broken"
    echo "  Exit: $exit_code, Output: $output"
fi

echo ""
echo "=== Test 5: Invalid checkpoint name ==="

output=$(bash "$PATCHED_RUNNER" "../etc/passwd" 2>/dev/null)
exit_code=$?

if [ "$exit_code" -ne 0 ] && echo "$output" | grep -q "Invalid checkpoint name"; then
    echo "  PASS: Rejects path traversal in checkpoint name"
else
    echo "  FAIL: Should reject invalid checkpoint name"
    echo "  Exit: $exit_code, Output: $output"
fi

echo ""
echo "=== Test 6: Verifier with no JSON output (fallback) ==="

cat > "$WORKSPACE/.verifiers/03-plain-exit.sh" << 'VERIFIER'
#!/usr/bin/env bash
echo "all good"
exit 0
VERIFIER
chmod +x "$WORKSPACE/.verifiers/03-plain-exit.sh"

output=$(bash "$PATCHED_RUNNER" "03-plain-exit" 2>/dev/null)
exit_code=$?

if [ "$exit_code" -eq 0 ] && echo "$output" | python3 -c "import sys, json; d=json.load(sys.stdin); assert d['passed']==True; assert d['score']==1.0" 2>/dev/null; then
    echo "  PASS: Plain-text verifier falls back to exit-code scoring"
else
    echo "  FAIL: Fallback scoring broken"
    echo "  Exit: $exit_code, Output: $output"
fi

echo ""
echo "=== All tests complete ==="
