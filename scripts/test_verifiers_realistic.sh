#!/usr/bin/env bash
# test_verifiers_realistic.sh — Run verifier scripts with realistic agent output
# Tests 10 representative tasks across 3 tiers: good, partial, empty
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BENCH_DIR="$ROOT/benchmarks"
LIB_DIR="$ROOT/lib"
RESULTS_FILE="$ROOT/results/analysis/verifier_testing.md"
TMP_BASE="/tmp/eb_verifier_test_$$"

mkdir -p "$(dirname "$RESULTS_FILE")"
mkdir -p "$TMP_BASE"

TOTAL_RUNS=0
PASS_RUNS=0
FAIL_RUNS=0
CRASH_RUNS=0
INVALID_JSON_RUNS=0
DETAILS=""

# Helper: run a single check script, validate output
run_check() {
    local task_name="$1"
    local check_script="$2"
    local tier="$3"
    local workspace="$4"
    local task_dir="$5"

    TOTAL_RUNS=$((TOTAL_RUNS + 1))
    local check_name
    check_name=$(basename "$check_script" .sh)

    export WORKSPACE="$workspace"
    export TASK_DIR="$task_dir"
    export TASK_ID="$task_name"
    export PYTHONPATH="$LIB_DIR:${PYTHONPATH:-}"

    local output exit_code
    output=$(bash "$check_script" 2>&1)
    exit_code=$?

    # Validate JSON
    local is_valid_json score passed
    if echo "$output" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('score','MISSING'), d.get('passed', d.get('detail','MISSING')))" 2>/dev/null; then
        is_valid_json="yes"
        score=$(echo "$output" | python3 -c "import json,sys; print(json.load(sys.stdin).get('score','MISSING'))" 2>/dev/null)
        passed=$(echo "$output" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('passed', 'N/A'))" 2>/dev/null)
    else
        is_valid_json="NO"
        score="N/A"
        passed="N/A"
        INVALID_JSON_RUNS=$((INVALID_JSON_RUNS + 1))
    fi

    # Determine status
    local status="OK"
    if [ "$exit_code" -gt 1 ]; then
        status="CRASH (exit=$exit_code)"
        CRASH_RUNS=$((CRASH_RUNS + 1))
    elif [ "$is_valid_json" = "NO" ]; then
        status="INVALID_JSON"
    else
        # Validate scoring logic
        case "$tier" in
            good)
                if python3 -c "import sys; sys.exit(0 if float('$score') >= 0.5 else 1)" 2>/dev/null; then
                    PASS_RUNS=$((PASS_RUNS + 1))
                else
                    status="SCORE_LOW (good tier should be >= 0.5)"
                    FAIL_RUNS=$((FAIL_RUNS + 1))
                fi
                ;;
            empty)
                if python3 -c "import sys; sys.exit(0 if float('$score') <= 0.1 else 1)" 2>/dev/null; then
                    PASS_RUNS=$((PASS_RUNS + 1))
                else
                    status="SCORE_HIGH (empty tier should be <= 0.1)"
                    FAIL_RUNS=$((FAIL_RUNS + 1))
                fi
                ;;
            partial)
                PASS_RUNS=$((PASS_RUNS + 1))
                ;;
        esac
    fi

    DETAILS="${DETAILS}| ${task_name} | ${check_name} | ${tier} | ${exit_code} | ${is_valid_json} | ${score} | ${passed} | ${status} |
"
}

# ============================================================
# TASK 1: err-provenance-01 (answer.json + ground_truth.json)
# ============================================================
setup_err_provenance() {
    local ws="$TMP_BASE/err-provenance-01/$1"
    local task_dir="$BENCH_DIR/customer_escalation/err-provenance-01"
    mkdir -p "$ws/agent_output"

    case "$1" in
        good)
            cat > "$ws/agent_output/answer.json" << 'EOF'
{
  "source_files": [
    {"path": "pkg/apis/batch/validation/validation.go"},
    {"path": "pkg/apis/batch/validation/validation_test.go"}
  ],
  "error_chain": [
    "User submits Job status update via API",
    "registry strategy validates the update",
    "validation.go checks startTime field",
    "Validation returns Required error with misleading message"
  ],
  "trigger_conditions": [
    "Job has session_type=single and is not suspended",
    "Controller modifies status.startTime",
    "Validation treats any change as removal incorrectly"
  ]
}
EOF
            ;;
        partial)
            cat > "$ws/agent_output/answer.json" << 'EOF'
{
  "source_files": [
    {"path": "pkg/apis/batch/validation/validation.go"}
  ],
  "error_chain": [
    "Some validation occurs in the batch API"
  ],
  "trigger_conditions": []
}
EOF
            ;;
        empty)
            # No answer.json at all
            rm -f "$ws/agent_output/answer.json"
            ;;
    esac

    echo "$ws|$task_dir"
}

# ============================================================
# TASK 2: support-mapping-001 (answer.json + ground_truth.json)
# ============================================================
setup_support_mapping() {
    local ws="$TMP_BASE/support-mapping-001/$1"
    local task_dir="$BENCH_DIR/customer_escalation/support-mapping-001"
    mkdir -p "$ws/agent_output"

    case "$1" in
        good)
            cat > "$ws/agent_output/answer.json" << 'EOF'
{
  "code_paths": [
    {"path": "source/common/http/conn_pool_base.cc"},
    {"path": "source/common/http/conn_pool_base.h"},
    {"path": "source/common/conn_pool/conn_pool_base.cc"},
    {"path": "source/common/upstream/conn_pool_map_impl.h"},
    {"path": "envoy/http/conn_pool.h"},
    {"path": "docs/root/intro/arch_overview/upstream/circuit_breaking.rst"}
  ],
  "ownership": "connection-pool management, upstream, circuit-breaking, overflow, max-connections subsystem",
  "severity": {"level": "high", "rationale": "Connection pool exhaustion causes service outages"},
  "related_issues": [
    {"path": "docs/root/intro/arch_overview/upstream/circuit_breaking.rst"},
    {"path": "envoy/http/conn_pool.h"}
  ]
}
EOF
            ;;
        partial)
            cat > "$ws/agent_output/answer.json" << 'EOF'
{
  "code_paths": [
    {"path": "source/common/http/conn_pool_base.cc"}
  ],
  "ownership": "networking",
  "severity": "medium",
  "related_issues": []
}
EOF
            ;;
        empty)
            rm -f "$ws/agent_output/answer.json"
            ;;
    esac
    echo "$ws|$task_dir"
}

# ============================================================
# TASK 3: monorepo-boundary-001 (IMPACT_REPORT.md)
# ============================================================
setup_monorepo_boundary() {
    local ws="$TMP_BASE/monorepo-boundary-001/$1"
    local task_dir="$BENCH_DIR/feature_delivery/monorepo-boundary-001"
    mkdir -p "$ws/babel"

    case "$1" in
        good)
            cat > "$ws/babel/IMPACT_REPORT.md" << 'EOF'
# Impact Report: TSPropertySignature.initializer Removal

## Affected Packages
- **@babel/parser** - Type definitions reference TSPropertySignature with initializer
- **@babel/generator** - TypeScript generation code emits initializer

## Impact Classification
All changes are **patch** level - internal implementation detail, no public API change.

## Boundary Violations
1. `packages/babel-types/src/definitions/typescript.ts` - Source of the change
2. `packages/babel-parser/src/types.d.ts` - Type definition references initializer
3. `packages/babel-generator/src/generators/typescript.ts` - Generator references initializer
EOF
            ;;
        partial)
            cat > "$ws/babel/IMPACT_REPORT.md" << 'EOF'
# Impact Report
## Affected
- @babel/parser is affected
## Impact
This is a minor change.
EOF
            ;;
        empty)
            rm -f "$ws/babel/IMPACT_REPORT.md"
            ;;
    esac
    echo "$ws|$task_dir"
}

# ============================================================
# TASK 4: dep-traversal-001 (BLAST_RADIUS.md)
# ============================================================
setup_dep_traversal() {
    local ws="$TMP_BASE/dep-traversal-001/$1"
    local task_dir="$BENCH_DIR/dependency_management/dep-traversal-001"
    mkdir -p "$ws"

    case "$1" in
        good)
            cat > "$ws/BLAST_RADIUS.md" << 'EOF'
# Blast Radius: CVE-2021-23337

## CVE Details
CVE-2021-23337 affects lodash < 4.17.21 (command injection via template).

## Direct Dependents
- **webpack** - uses lodash in its build pipeline
- **jest** - uses lodash via jest-haste-map
- **@babel/traverse** - depends on lodash for utility functions

## Transitive Paths
- lodash -> webpack (direct dependency)
- lodash -> jest-haste-map -> jest (transitive via jest packages)
- lodash -> @babel/traverse -> babel-loader -> webpack (transitive)

## Version Analysis
- webpack: affected (uses lodash < 4.17.21, needs upgrade)
- jest: not affected / patched in recent versions but still vulnerable in older installs
EOF
            ;;
        partial)
            cat > "$ws/BLAST_RADIUS.md" << 'EOF'
# CVE-2021-23337
Affects lodash. webpack uses lodash.
EOF
            ;;
        empty)
            rm -f "$ws/BLAST_RADIUS.md"
            ;;
    esac
    echo "$ws|$task_dir"
}

# ============================================================
# TASK 5: schema-evolution-001 (SCHEMA_IMPACT.md)
# ============================================================
setup_schema_evolution() {
    local ws="$TMP_BASE/schema-evolution-001/$1"
    local task_dir="$BENCH_DIR/feature_delivery/schema-evolution-001"
    mkdir -p "$ws/zulip"

    case "$1" in
        good)
            cat > "$ws/zulip/SCHEMA_IMPACT.md" << 'EOF'
# Schema Impact: NamedUserGroup deactivated field

## Schema Change
Migration `0578_namedusergroup_deactivated` adds `deactivated` BooleanField to
`NamedUserGroup` in `zerver/models/groups.py`.

## Direct References
- `zerver/actions/user_groups.py` - user group CRUD actions
- `zerver/views/user_groups.py` - REST API views
- `zerver/lib/user_groups.py` - helper utilities
- `zerver/views/streams.py` - stream settings referencing groups

## Indirect References
- `zerver/lib/events.py` - event system integration
- `zerver/lib/event_schema.py` - event payload schema
- `zerver/lib/import_realm.py` - realm data import
- `zerver/lib/markdown/__init__.py` - markdown mention rendering
- `zerver/openapi/zulip.yaml` - API documentation schema
- `zerver/models/realm_audit_logs.py` - audit logging

## Test Impact
- `zerver/tests/test_user_groups.py` - core group tests
- `zerver/tests/test_events.py` - event dispatch tests
- `zerver/tests/test_audit_log.py` - audit log tests
- `zerver/tests/test_markdown.py` - markdown rendering tests
EOF
            ;;
        partial)
            cat > "$ws/zulip/SCHEMA_IMPACT.md" << 'EOF'
# Schema Impact
Changes to NamedUserGroup model. Updates needed in zerver/actions/user_groups.py
and zerver/views/user_groups.py. Tests in test_user_groups.
EOF
            ;;
        empty)
            rm -f "$ws/zulip/SCHEMA_IMPACT.md"
            ;;
    esac
    echo "$ws|$task_dir"
}

# ============================================================
# TASK 6: api-contract-001 (IMPACT_REPORT.md)
# ============================================================
setup_api_contract() {
    local ws="$TMP_BASE/api-contract-001/$1"
    local task_dir="$BENCH_DIR/dependency_management/api-contract-001"
    mkdir -p "$ws/analysis"

    case "$1" in
        good)
            cat > "$ws/analysis/IMPACT_REPORT.md" << 'EOF'
# API Contract: gRPC-Go Metadata Context Separation

## Breaking Change Source
File: `metadata/metadata.go`
- `metadata.FromContext` -> `metadata.FromIncomingContext`
- `metadata.NewContext` -> `metadata.NewOutgoingContext`

## Direct Consumer Files (etcd)
- `auth/store.go` - uses FromContext for auth token extraction
- `etcdserver/api/v3rpc/interceptor.go` - gRPC interceptor middleware
- `etcdserver/api/v3rpc/grpc.go` - gRPC server setup
- `clientv3/auth.go` - client authentication

## Transitive Impact
The interceptor forwards metadata between incoming and outgoing contexts.
Auth token propagation is affected since credentials are copied between contexts.

## Breakage Classification
- **Compile errors**: All uses of FromContext and NewContext fail to compile
- **Runtime behavior change**: Silent metadata loss if old API was still available as deprecated
EOF
            ;;
        partial)
            cat > "$ws/analysis/IMPACT_REPORT.md" << 'EOF'
# API Contract Change
metadata.go in grpc-go changed. FromContext was renamed to FromIncomingContext.
etcd auth/store.go is affected.
EOF
            ;;
        empty)
            rm -f "$ws/analysis/IMPACT_REPORT.md"
            ;;
    esac
    echo "$ws|$task_dir"
}

# ============================================================
# TASK 7: refactor-orchestration-001 (REFACTOR_PLAN.md)
# ============================================================
setup_refactor_orchestration() {
    local ws="$TMP_BASE/refactor-orchestration-001/$1"
    local task_dir="$BENCH_DIR/technical_debt/refactor-orchestration-001"
    mkdir -p "$ws"

    case "$1" in
        good)
            cat > "$ws/REFACTOR_PLAN.md" << 'EOF'
# Refactor Plan: etcd 3.6 Client Update Cascade

## Repos Requiring Changes
1. etcd-io/etcd
2. kubernetes/kubernetes

## Dependency Graph
kubernetes/kubernetes depends on etcd-io/etcd client libraries.

## Ordering
1. etcd-io/etcd - Release v3.6 client libraries first
2. kubernetes/kubernetes - Update to new etcd client after release

## Parallelization
No steps can be done independently — kubernetes depends on etcd being updated first.
EOF
            ;;
        partial)
            cat > "$ws/REFACTOR_PLAN.md" << 'EOF'
# Refactor Plan
Update etcd and kubernetes.
EOF
            ;;
        empty)
            rm -f "$ws/REFACTOR_PLAN.md"
            ;;
    esac
    echo "$ws|$task_dir"
}

# ============================================================
# TASK 8: dead-code-001 (dead_code_report.json)
# ============================================================
setup_dead_code() {
    local ws="$TMP_BASE/dead-code-001/$1"
    local task_dir="$BENCH_DIR/technical_debt/dead-code-001"
    mkdir -p "$ws/react"

    case "$1" in
        good)
            cat > "$ws/react/dead_code_report.json" << 'EOF'
[
  {"file": "packages/react-dom/src/legacy-events/EventPluginRegistry.js", "symbol": "default", "kind": "file", "confidence": "high", "evidence": "Entire file is dead — zero callers found across entire codebase"},
  {"file": "packages/react-dom/src/legacy-events/EventPluginUtils.js", "symbol": "default", "kind": "file", "confidence": "high", "evidence": "Entire file is dead — no callers, unused by modern event system"},
  {"file": "packages/react-dom/src/legacy-events/accumulateInto.js", "symbol": "default", "kind": "file", "confidence": "high", "evidence": "Entire file is dead — accumulateInto never called"},
  {"file": "packages/react-dom/src/legacy-events/forEachAccumulated.js", "symbol": "default", "kind": "file", "confidence": "high", "evidence": "Entire file is dead — not imported anywhere"},
  {"file": "packages/react-dom/src/client/ReactDOMClientInjection.js", "symbol": "default", "kind": "file", "confidence": "high", "evidence": "Entire file is dead — no importers"},
  {"file": "packages/react-dom/src/events/SyntheticEvent.js", "symbol": "getPooled", "kind": "function", "confidence": "high", "evidence": "Event pooling removed — getPooled is never called"},
  {"file": "packages/react-dom/src/events/SyntheticEvent.js", "symbol": "destructor", "kind": "function", "confidence": "high", "evidence": "Event pooling removed — destructor is never called"},
  {"file": "packages/react-dom/src/events/SyntheticEvent.js", "symbol": "addEventPoolingTo", "kind": "function", "confidence": "high", "evidence": "Event pooling removed — pool setup function is never called"},
  {"file": "packages/react-dom/src/events/ReactSyntheticEventType.js", "symbol": "DispatchConfig", "kind": "export", "confidence": "high", "evidence": "DispatchConfig type only used by legacy event system, zero callers"},
  {"file": "packages/react-dom/src/events/TopLevelEventTypes.js", "symbol": "getRawEventName", "kind": "function", "confidence": "high", "evidence": "Helper only used by legacy event registration, no callers"},
  {"file": "packages/react-dom/src/test-utils/ReactTestUtils.js", "symbol": "accumulateInto", "kind": "function", "confidence": "high", "evidence": "Imported from dead legacy-events module, unused in test utils"},
  {"file": "packages/react-dom/src/test-utils/ReactTestUtils.js", "symbol": "forEachAccumulated", "kind": "function", "confidence": "high", "evidence": "Imported from dead legacy-events module, unused"},
  {"file": "packages/react-dom/src/client/ReactDOM.js", "symbol": "enableDeprecatedFlareAPI branch", "kind": "branch", "confidence": "high", "evidence": "enableDeprecatedFlareAPI is permanently off — all code guarded by this flag is dead"}
]
EOF
            ;;
        partial)
            cat > "$ws/react/dead_code_report.json" << 'EOF'
[
  {"file": "packages/react-dom/src/legacy-events/EventPluginRegistry.js", "symbol": "default", "kind": "file", "confidence": "high", "evidence": "Unused file"},
  {"file": "packages/react-dom/src/events/SyntheticEvent.js", "symbol": "getPooled", "kind": "function", "confidence": "medium", "evidence": "Possibly unused"}
]
EOF
            ;;
        empty)
            rm -f "$ws/react/dead_code_report.json"
            ;;
    esac
    echo "$ws|$task_dir"
}

# ============================================================
# TASK 9: incident-investigation-001 (INCIDENT_REPORT.md)
# ============================================================
setup_incident_investigation() {
    local ws="$TMP_BASE/incident-investigation-001/$1"
    local task_dir="$BENCH_DIR/incident_response/incident-investigation-001"
    mkdir -p "$ws/kubernetes"

    case "$1" in
        good)
            cat > "$ws/kubernetes/INCIDENT_REPORT.md" << 'EOF'
# Incident Report: Missing Watch Events

## Root Cause
The bug is in `watch_cache.go`, specifically in the `GetAllEventsSince` function.
When the watch event cache is empty, there is an off-by-one error where the
oldest resourceVersion is not properly checked, allowing watches that the cache
cannot serve.

## Error Chain
1. **kube-apiserver** watch handler receives watch request from controller
2. **cacher.go** accepts the watch and delegates to the watch cache
3. **watch_cache.go** (BUG) - empty cache allows watch at resourceVersion but cannot deliver events
4. **etcd3 watcher.go** starts watching from current version, missing the gap

## Affected Services
- kube-apiserver (watch cache layer and storage)
- etcd storage backend
- Controllers using informers and client-go
- CRD registration flow

## Remediation
Return **410 Gone** error when the watch cache is empty and cannot serve events
for the requested resourceVersion. Check if the resourceVersion bounds are
within the empty cache's range and reject the watch to force the client to relist.
EOF
            ;;
        partial)
            cat > "$ws/kubernetes/INCIDENT_REPORT.md" << 'EOF'
# Incident Report
## Root Cause
Issue in the watch_cache component affecting the API server.
## Affected
The apiserver and etcd are affected.
EOF
            ;;
        empty)
            rm -f "$ws/kubernetes/INCIDENT_REPORT.md"
            ;;
    esac
    echo "$ws|$task_dir"
}

# ============================================================
# TASK 10: config-drift-001 (DRIFT_REPORT.json)
# ============================================================
setup_config_drift() {
    local ws="$TMP_BASE/config-drift-001/$1"
    local task_dir="$BENCH_DIR/platform_engineering/config-drift-001"
    mkdir -p "$ws/charts"

    case "$1" in
        good)
            cat > "$ws/charts/DRIFT_REPORT.json" << 'EOF'
{
  "drift_points": [
    {
      "file": "bitnami/consul/templates/consul-headless-service.yaml",
      "key": "serflan-udp port containerPort",
      "expected": "containerPorts.serfLAN",
      "actual": "containerPorts.serfWAN",
      "override_chain": ["values.yaml -> templates/consul-headless-service.yaml"]
    },
    {
      "file": "bitnami/consul/templates/consul-headless-service.yaml",
      "key": "serfwan-udp port containerPort",
      "expected": "containerPorts.serfWAN",
      "actual": "containerPorts.serfLAN",
      "override_chain": ["values.yaml -> templates/consul-headless-service.yaml"]
    }
  ]
}
EOF
            ;;
        partial)
            cat > "$ws/charts/DRIFT_REPORT.json" << 'EOF'
{
  "drift_points": [
    {
      "file": "bitnami/consul/templates/consul-headless-service.yaml",
      "key": "serflan-udp port value",
      "expected": "containerPorts.serfLAN",
      "actual": "containerPorts.serfWAN",
      "override_chain": []
    }
  ]
}
EOF
            ;;
        empty)
            rm -f "$ws/charts/DRIFT_REPORT.json"
            ;;
    esac
    echo "$ws|$task_dir"
}

# ============================================================
# Main: Run all checks for all tasks, all tiers
# ============================================================
echo "Starting verifier testing across 10 tasks x 3 tiers..."
echo ""

TASKS="err-provenance-01 support-mapping-001 monorepo-boundary-001 dep-traversal-001 schema-evolution-001 api-contract-001 refactor-orchestration-001 dead-code-001 incident-investigation-001 config-drift-001"

for tier in good partial empty; do
    for task in $TASKS; do
        # Setup workspace
        case "$task" in
            err-provenance-01) result=$(setup_err_provenance "$tier") ;;
            support-mapping-001) result=$(setup_support_mapping "$tier") ;;
            monorepo-boundary-001) result=$(setup_monorepo_boundary "$tier") ;;
            dep-traversal-001) result=$(setup_dep_traversal "$tier") ;;
            schema-evolution-001) result=$(setup_schema_evolution "$tier") ;;
            api-contract-001) result=$(setup_api_contract "$tier") ;;
            refactor-orchestration-001) result=$(setup_refactor_orchestration "$tier") ;;
            dead-code-001) result=$(setup_dead_code "$tier") ;;
            incident-investigation-001) result=$(setup_incident_investigation "$tier") ;;
            config-drift-001) result=$(setup_config_drift "$tier") ;;
        esac

        ws="${result%%|*}"
        task_dir="${result##*|}"

        # Run each check script
        for check_script in "$task_dir"/checks/check_*.sh; do
            [ -f "$check_script" ] || continue
            run_check "$task" "$check_script" "$tier" "$ws" "$task_dir"
        done
    done
done

# ============================================================
# Generate report
# ============================================================
cat > "$RESULTS_FILE" << EOF
# Verifier Testing Report

Generated: $(date -u '+%Y-%m-%d %H:%M:%S UTC')

## Summary

| Metric | Count |
|--------|-------|
| Total check runs | $TOTAL_RUNS |
| Passed | $PASS_RUNS |
| Failed (wrong score) | $FAIL_RUNS |
| Crashed (exit > 1) | $CRASH_RUNS |
| Invalid JSON output | $INVALID_JSON_RUNS |

## Detailed Results

| Task | Check | Tier | Exit | Valid JSON | Score | Passed | Status |
|------|-------|------|------|------------|-------|--------|--------|
${DETAILS}

## Methodology

For each of the 10 task types, one representative task was tested:
- **err-provenance-01** (error_provenance)
- **support-mapping-001** (support_code_mapping)
- **monorepo-boundary-001** (monorepo_boundary)
- **dep-traversal-001** (dependency_graph)
- **schema-evolution-001** (db_schema_evolution)
- **api-contract-001** (api_contract)
- **refactor-orchestration-001** (refactor_orchestration)
- **dead-code-001** (dead_code_necropsy)
- **incident-investigation-001** (incident_investigation)
- **config-drift-001** (config_drift)

Three tiers of agent output were tested:
1. **good** — Ground-truth-matching output with all expected fields
2. **partial** — Half-right output with incomplete data
3. **empty** — No output files at all

Scoring expectations:
- Good tier: score >= 0.5
- Empty tier: score <= 0.1
- Partial tier: any score (informational)

## Issues Found

EOF

# Summarize issues
if [ "$CRASH_RUNS" -gt 0 ]; then
    echo "### Crashes" >> "$RESULTS_FILE"
    echo "$DETAILS" | grep "CRASH" | while IFS='|' read -r _ task check tier exit rest; do
        echo "- **${task}** / ${check} (${tier}): exit code ${exit}" >> "$RESULTS_FILE"
    done
    echo "" >> "$RESULTS_FILE"
fi

if [ "$INVALID_JSON_RUNS" -gt 0 ]; then
    echo "### Invalid JSON Output" >> "$RESULTS_FILE"
    echo "$DETAILS" | grep "INVALID_JSON" | while IFS='|' read -r _ task check tier rest; do
        echo "- **${task}** / ${check} (${tier})" >> "$RESULTS_FILE"
    done
    echo "" >> "$RESULTS_FILE"
fi

if [ "$FAIL_RUNS" -gt 0 ]; then
    echo "### Incorrect Scoring" >> "$RESULTS_FILE"
    echo "$DETAILS" | grep -E "SCORE_LOW|SCORE_HIGH" | while IFS='|' read -r _ task check tier exit valid score passed status; do
        echo "- **${task}** / ${check} (${tier}): score=${score}, ${status}" >> "$RESULTS_FILE"
    done
    echo "" >> "$RESULTS_FILE"
fi

if [ "$CRASH_RUNS" -eq 0 ] && [ "$INVALID_JSON_RUNS" -eq 0 ] && [ "$FAIL_RUNS" -eq 0 ]; then
    echo "No issues found. All verifier scripts produce valid JSON, correct exit codes, and reasonable scores." >> "$RESULTS_FILE"
fi

echo ""
echo "=== VERIFIER TESTING COMPLETE ==="
echo "Total: $TOTAL_RUNS | Pass: $PASS_RUNS | Fail: $FAIL_RUNS | Crash: $CRASH_RUNS | InvalidJSON: $INVALID_JSON_RUNS"
echo "Report: $RESULTS_FILE"

# Cleanup
rm -rf "$TMP_BASE"
