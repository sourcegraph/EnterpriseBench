"""Verification tests for Phase 4 task checkpoint scripts.

Covers 12 tasks across 3 types:
  - 4 incident investigation (incident_response/incident-investigation-{001..004})
  - 4 config drift (platform_engineering/config-drift-{001..004})
  - 4 RBAC/security audit (security_operations/rbac-audit-{001..004})

For each task, tests 3 tiers:
  (a) Ground truth answer -> score >= 0.85
  (b) Empty answer -> score <= 0.10
  (c) Partial answer -> 0.3 <= score <= 0.7

Also verifies structural requirements:
  - Checkpoint weights sum to 1.0
  - All check scripts exist and are executable
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parent.parent
BENCHMARKS = ROOT / "benchmarks"


# ── data types ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CheckSpec:
    """A single checkpoint verifier to run."""
    script_name: str
    weight: float


@dataclass(frozen=True)
class TaskSpec:
    """Full spec for testing one task's verifiers."""
    task_dir: Path
    report_path: str       # relative to workspace, e.g. "agent_output/INCIDENT_REPORT.md"
    checks: list[CheckSpec]
    gt_answer: str
    partial_answer: str


# ── helpers ─────────────────────────────────────────────────────────────────

def _run_verifier(task_dir: Path, script_name: str, workspace: Path) -> dict[str, Any]:
    """Run a checkpoint verifier script and return parsed JSON output."""
    script = task_dir / "checks" / script_name
    assert script.exists(), f"Verifier not found: {script}"

    env = os.environ.copy()
    env["WORKSPACE"] = str(workspace)
    env["TASK_DIR"] = str(task_dir)

    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(workspace),
        env=env,
    )
    stdout = result.stdout.strip()
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"score": 1.0 if result.returncode == 0 else 0.0, "raw": stdout}


def _write_report(workspace: Path, rel_path: str, content: str) -> Path:
    """Write a report file into the workspace."""
    report = workspace / rel_path
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(content)
    return report


def _weighted_score(task: TaskSpec, workspace: Path) -> float:
    """Run all checkpoints and return weighted total score."""
    total = 0.0
    for check in task.checks:
        result = _run_verifier(task.task_dir, check.script_name, workspace)
        total += float(result.get("score", 0.0)) * check.weight
    return total


def _all_results(task: TaskSpec, workspace: Path) -> list[dict[str, Any]]:
    """Run all checkpoints and return list of results."""
    return [
        _run_verifier(task.task_dir, c.script_name, workspace)
        for c in task.checks
    ]


# ── incident investigation tasks ────────────────────────────────────────────

_II_DIR = BENCHMARKS / "incident_response"

INCIDENT_TASKS: list[TaskSpec] = [
    # 001: K8s watch cache missing events
    TaskSpec(
        task_dir=_II_DIR / "incident-investigation-001",
        report_path="agent_output/INCIDENT_REPORT.md",
        checks=[
            CheckSpec("check_root_cause.sh", 0.35),
            CheckSpec("check_error_chain.sh", 0.30),
            CheckSpec("check_affected_services.sh", 0.15),
            CheckSpec("check_remediation.sh", 0.20),
        ],
        gt_answer="""\
# Incident Report

## Root Cause

The bug is in `staging/src/k8s.io/apiserver/pkg/storage/watch_cache.go`, specifically
in the `GetAllEventsSinceThreadUnsafe` function. When the watch event cache is empty,
the watch cache incorrectly allows establishing a watch at its current synced
resourceVersion even though it cannot deliver any events for that version. This is an
off-by-one error: the oldest available resourceVersion is not correctly adjusted for
an empty watch event cache.

## Error Chain

1. **Controller (client-go informer)**: Lists resources, receives resourceVersion=N
2. **API Server watch handler**: Routes watch request to storage cacher
3. **Watch Cache (cacher.go)**: Accepts watch, delegates to watch_cache for initial events
4. **Watch Cache (watch_cache.go)**: BUG — empty cache allows watch at resourceVersion=N
   but cannot deliver event N+1. Should return "410 Gone" error.
5. **etcd3 storage (watcher.go)**: Starts watching from current resourceVersion, missing the gap

## Affected Components

- **kube-apiserver** — watch cache layer (storage/cacher.go, storage/watch_cache.go)
- **etcd storage backend** — correct behavior in etcd3/watcher.go
- **All controllers using client-go informers** — any controller that lists+watches
- **CRD/TPR registration flow** — most commonly triggers the empty cache condition

## Remediation

When the watch event cache is empty, the watch cache should compare the requested
resourceVersion against its synced resourceVersion. If the requested version is older
than the synced version and there are no cached events to serve the gap, return a
410 Gone error to force the client to relist and re-watch from the current version.
This ensures no events are silently dropped.
""",
        partial_answer="""\
# Incident Report

## Root Cause

The issue is in the API server's watch_cache component. The cache has an issue with
resource version handling when it's empty.

## Error Chain

The API server watch cache interacts with etcd to serve watch events.

## Affected Components

- kube-apiserver
""",
    ),
    # 002: K8s watch cache delete event wrong resourceVersion
    TaskSpec(
        task_dir=_II_DIR / "incident-investigation-002",
        report_path="agent_output/INCIDENT_REPORT.md",
        checks=[
            CheckSpec("check_root_cause.sh", 0.35),
            CheckSpec("check_error_chain.sh", 0.30),
            CheckSpec("check_affected_services.sh", 0.15),
            CheckSpec("check_remediation.sh", 0.20),
        ],
        gt_answer="""\
# Incident Report

## Root Cause

The bug is in `staging/src/k8s.io/apiserver/pkg/storage/cacher.go`, in the event
filtering logic. When the watch cache converts a Modified event to a Deleted event
(because the object no longer matches the namespace filter), it uses
`event.PrevObject.DeepCopyObject()` which retains the previous object's stale
resourceVersion instead of setting the event's current resourceVersion.

The etcd3 watcher (`etcd3/watcher.go`) handles this correctly by setting the
event resourceVersion on delete events.

## Error Chain

1. **client-go informer**: Establishes namespace-scoped watch, tracks resourceVersion
2. **API Server**: Routes watch to storage cacher
3. **Watch Cache (cacher.go)**: BUG — Converts Modified->Deleted using PrevObject with
   stale resourceVersion. The filter logic: `!curObjPasses && oldObjPasses` sends
   `watch.Event{Type: watch.Deleted, Object: event.PrevObject.DeepCopyObject()}`
4. **etcd3 watcher (watcher.go)**: Has correct logic (reference implementation)

## Affected Components

- **kube-apiserver** watch cache (cacher.go)
- **client-go informers** (all controllers)
- **Any namespace-scoped or label-filtered watch**

## Remediation

After constructing the DELETE watch event from `PrevObject.DeepCopyObject()`, set the
event's resourceVersion on the copied object using the versioner. This matches the
existing correct logic in `etcd3/watcher.go` and `etcd/etcd_watcher.go`.
""",
        partial_answer="""\
# Incident Report

## Root Cause

The bug is in cacher.go where delete events retain the previous object's stale
resourceVersion instead of the current one.

## Error Chain

The client-go informer gets stale data from the watch cache (cacher).

## Affected Components

- kube-apiserver
- controllers
""",
    ),
    # 003: Grafana Prometheus JSON parsing silent failure
    TaskSpec(
        task_dir=_II_DIR / "incident-investigation-003",
        report_path="agent_output/INCIDENT_REPORT.md",
        checks=[
            CheckSpec("check_root_cause.sh", 0.35),
            CheckSpec("check_error_chain.sh", 0.30),
            CheckSpec("check_affected_services.sh", 0.15),
            CheckSpec("check_remediation.sh", 0.20),
        ],
        gt_answer="""\
# Incident Report

## Root Cause

The bug is in `pkg/util/converter/prom.go`. The Prometheus response JSON parser uses
the jsoniter library (json-iterator/go). jsoniter's Iterator methods do not return
errors — they set `iter.Error` which must be checked after parsing. The converter code
never checks `iter.Error`, so when the response is truncated (by the dataproxy
response_limit setting) or contains malformed JSON, jsoniter silently stops and the
converter returns whatever partial data was extracted.

## Error Chain

1. **Prometheus HTTP API**: Returns a complete, valid JSON response
2. **Grafana data proxy**: Enforces response_limit, truncating the response body
3. **Grafana Prometheus datasource plugin**: Passes truncated body to converter
4. **Grafana converter (pkg/util/converter/prom.go)**: Parses with jsoniter, never
   checks iter.Error — returns partial data frames with no error indication

## Affected Components

- **Grafana Prometheus datasource** — primary affected component
- **Grafana Loki datasource** — shares the same converter code path

## Remediation

Check `iter.Error` after every jsoniter parsing operation in prom.go. When an error is
detected, return the error instead of partial data. Consider wrapping jsoniter's
Iterator methods in an error-returning wrapper (a jsonitere package) so errcheck
linting can catch unchecked errors.
""",
        partial_answer="""\
# Incident Report

## Root Cause

The prom.go converter has issues with error checking when parsing JSON responses.
The response gets truncated and partial data is returned.

## Error Chain

Prometheus API -> Grafana dataproxy -> converter

## Affected Components

- Grafana Prometheus datasource
""",
    ),
    # 004: Docker daemon spurious restart warnings
    TaskSpec(
        task_dir=_II_DIR / "incident-investigation-004",
        report_path="agent_output/INCIDENT_REPORT.md",
        checks=[
            CheckSpec("check_root_cause.sh", 0.35),
            CheckSpec("check_error_chain.sh", 0.30),
            CheckSpec("check_affected_services.sh", 0.15),
            CheckSpec("check_remediation.sh", 0.20),
        ],
        gt_answer="""\
# Incident Report

## Root Cause

The bug is in `daemon/monitor.go`, in the `handleContainerExit` function. During
graceful shutdown, the daemon calls `container.ExitOnNext()` to stop the restart
manager. When the containerd shim sends the TaskDelete event, `handleContainerExit`
calls `RestartManager().ShouldRestart()`, which returns `ErrRestartCanceled`.
This error is logged as a WARN even though it's expected during normal shutdown.

The confusing "ignoring event" message in `daemon/internal/libcontainerd/remote/client.go`
for TaskDelete events also misleads operators.

## Error Chain

1. **SIGINT signal** received by daemon
2. **Daemon shutdown**: Calls `container.ExitOnNext()` to stop restart manager
3. **containerd shim**: Container exits, shim sends TaskDelete event
4. **libcontainerd/remote/client.go**: Receives TaskDelete, logs "ignoring event"
5. **daemon/monitor.go handleContainerExit**: Calls ShouldRestart(), gets
   ErrRestartCanceled, logs as WARN — this is the spurious warning

## Affected Components

- **daemon/monitor.go** (handleContainerExit)
- **daemon/container/container.go** (ExitOnNext, RestartManager)
- **daemon/internal/libcontainerd/remote/client.go** (event logging)
- **container restart manager**

## Remediation

In `handleContainerExit`: check if the error from ShouldRestart is
`ErrRestartCanceled`; if so, do not log it as a warning — it is expected during
shutdown. Suppress the warning for this specific error case.

In `libcontainerd/remote/client.go`: change the confusing "ignoring event" log to a
more descriptive message like "received task-delete event from containerd". Also split
the exitStatus field into separate exitCode and exitedAt for better log readability.
""",
        partial_answer="""\
# Incident Report

## Root Cause

The monitor.go has an issue with ShouldRestart during shutdown. The restart manager
returns ErrRestartCanceled which gets logged as a warning.

## Error Chain

SIGINT -> daemon shutdown -> containerd shim TaskDelete -> handleContainerExit

## Affected Components

- daemon monitor
""",
    ),
]


# ── config drift tasks ──────────────────────────────────────────────────────

_CD_DIR = BENCHMARKS / "platform_engineering"

CONFIG_DRIFT_TASKS: list[TaskSpec] = [
    # 001: Consul serfLAN/serfWAN port mismatch
    TaskSpec(
        task_dir=_CD_DIR / "config-drift-001",
        report_path="agent_output/DRIFT_REPORT.json",
        # validate_corrected_config / check_config_valid.sh was removed in
        # 6cbbc54: instruction.md never asks for a corrected config, so it graded
        # an artifact a compliant agent has no reason to produce and paid 1.0 for
        # its absence. Weights redistributed 0.40:0.35 -> 0.55:0.45.
        checks=[
            CheckSpec("check_drift_points.sh", 0.55),
            CheckSpec("check_expected_values.sh", 0.45),
        ],
        gt_answer=json.dumps({
            "drift_points": [
                {
                    "file": "bitnami/consul/templates/consul-headless-service.yaml",
                    "key": "serflan-udp containerPort",
                    "expected": "containerPorts.serfLAN",
                    "actual": "containerPorts.serfWAN",
                    "override_chain": ["values.yaml -> statefulset.yaml -> headless-service.yaml"]
                },
                {
                    "file": "bitnami/consul/templates/consul-headless-service.yaml",
                    "key": "serfwan-udp containerPort",
                    "expected": "containerPorts.serfWAN",
                    "actual": "containerPorts.serfLAN",
                    "override_chain": ["values.yaml -> statefulset.yaml -> headless-service.yaml"]
                }
            ]
        }, indent=2),
        partial_answer=json.dumps({
            "drift_points": [
                {
                    "file": "bitnami/consul/templates/consul-headless-service.yaml",
                    "key": "serflan-udp containerPort",
                    "expected": "containerPorts.serfLAN",
                    "actual": "containerPorts.serfWAN",
                    "override_chain": ["values.yaml -> headless-service.yaml"]
                }
            ]
        }, indent=2),
    ),
    # 002: Spring Cloud Dataflow RabbitMQ config
    TaskSpec(
        task_dir=_CD_DIR / "config-drift-002",
        report_path="agent_output/DRIFT_REPORT.json",
        # Rebuilt in 2c1ac55. check_config_valid.sh removed (never-requested
        # artifact, paid 1.0 for its absence); determine_expected_values became
        # config_hierarchy, the instruction's actual first ask.
        checks=[
            CheckSpec("check_drift_points.sh", 0.55),
            CheckSpec("check_config_hierarchy.sh", 0.45),
        ],
        # This fixture previously restated the FABRICATED key it was written
        # from: it blamed _helpers.tpl for the erlangCookie validation (that file
        # contains 0 hits for it; NOTES.txt:87 has it) and otherwise paraphrased
        # instruction.md. It scored 1.0 against the old blob-grep check and 0.0
        # against the rebuilt one, which is the point — replaced with the answer
        # verified against the chart at 53aca511 (2c1ac55).
        gt_answer=json.dumps({
            "drift_points": [
                {
                    "file": "bitnami/spring-cloud-dataflow/values.yaml",
                    "key": "externalRabbitmq.existingPasswordSecret",
                    "expected": "a name + key pair, mirroring externalDatabase.existingPasswordSecret / externalDatabase.existingPasswordKey",
                    "actual": "a secret name only; no key parameter exists for externalRabbitmq",
                    "override_chain": [
                        "values.yaml:externalRabbitmq.existingPasswordSecret",
                        "_helpers.tpl:scdf.rabbitmq.secretName resolves the NAME only",
                    ],
                },
                {
                    "file": "bitnami/spring-cloud-dataflow/templates/skipper/configmap.yaml",
                    "key": "SPRING_RABBITMQ_PASSWORD",
                    "expected": "read the password from a configurable key",
                    "actual": "SPRING_RABBITMQ_PASSWORD=${rabbitmq-password} hardcodes the key rabbitmq-password",
                    "override_chain": [
                        "scdf.rabbitmq.secretName",
                        "skipper/configmap.yaml:${rabbitmq-password}",
                    ],
                },
                {
                    "file": "bitnami/spring-cloud-dataflow/templates/NOTES.txt",
                    "key": "rabbitmq.auth.erlangCookie",
                    "expected": "not validated on the external path",
                    "actual": "$requiredErlangPassword with field rabbitmq-erlang-cookie is validated whenever externalRabbitmq is enabled without an existing secret",
                    "override_chain": [
                        "NOTES.txt:85 guard",
                        "NOTES.txt:87",
                        "common.validations.values.multiple.empty",
                    ],
                },
                {
                    "file": "bitnami/spring-cloud-dataflow/templates/NOTES.txt",
                    "key": "$secretNameRabbitmq",
                    "expected": "include scdf.rabbitmq.secretName",
                    "actual": "include scdf.rabbitmq.fullname names the bundled subchart's secret",
                    "override_chain": ["NOTES.txt:77"],
                },
            ]
        }, indent=2),
        partial_answer=json.dumps({
            "drift_points": [
                {
                    "file": "bitnami/spring-cloud-dataflow/values.yaml",
                    "key": "externalRabbitmq.existingPasswordSecret",
                    "expected": "a name + key pair like externalDatabase.existingPasswordKey",
                    "actual": "name only, no key parameter",
                    "override_chain": ["values.yaml -> scdf.rabbitmq.secretName"],
                },
                {
                    "file": "bitnami/spring-cloud-dataflow/templates/skipper/configmap.yaml",
                    "key": "SPRING_RABBITMQ_PASSWORD",
                    "expected": "configurable key",
                    "actual": "hardcodes rabbitmq-password",
                    "override_chain": ["skipper/configmap.yaml"],
                },
            ]
        }, indent=2),
    ),
    # 003: Redis password generation drift
    TaskSpec(
        task_dir=_CD_DIR / "config-drift-003",
        report_path="agent_output/DRIFT_REPORT.json",
        # Rebuilt in 80ca4f8. check_config_valid.sh removed (never-requested
        # artifact); determine_expected_values became root_cause_mechanism —
        # what its description always said it graded.
        checks=[
            CheckSpec("check_drift_points.sh", 0.55),
            CheckSpec("check_root_cause.sh", 0.45),
        ],
        # This fixture named master-statefulset.yaml and replica-statefulset.yaml
        # — a THIRD set of files, agreeing with neither the old answer key
        # (health-configmap.yaml / master/application.yaml) nor the chart. A
        # chart-wide search for `include "redis.password"` at 478a81c9 returns
        # exactly three hits: secret.yaml:24, secret-svcbind.yaml:15 and
        # configmap.yaml:55. The statefulsets call it zero times. Replaced with
        # the two call sites that render under this task's scenario (80ca4f8);
        # configmap.yaml is gated by auth.acl.enabled, default false.
        gt_answer=json.dumps({
            "drift_points": [
                {
                    "file": "bitnami/redis/templates/secret.yaml",
                    "key": "redis-password",
                    "expected": "the same generated password every other call site resolves to",
                    "actual": "a fresh randAlphaNum value, because this include re-runs the generator",
                    "override_chain": [
                        "values.yaml:auth.password unset",
                        "_helpers.tpl:redis.password",
                        "common.secrets.passwords.manage",
                        "secret.yaml",
                    ],
                },
                {
                    "file": "bitnami/redis/templates/secret-svcbind.yaml",
                    "key": "password",
                    "expected": "the same generated password the Secret holds",
                    "actual": "a second, unrelated randAlphaNum value — the ServiceBinding mismatch of #15626",
                    "override_chain": [
                        "values.yaml:auth.password unset",
                        "_helpers.tpl:redis.password",
                        "common.secrets.passwords.manage",
                        "secret-svcbind.yaml",
                    ],
                },
            ],
            "root_cause": (
                "redis.password (_helpers.tpl:206-210) does not randomise: it delegates to "
                "common.secrets.passwords.manage in the common subchart, which calls randAlphaNum "
                "when no password is set. Helm re-evaluates a named template on every include, so "
                "that path re-runs per call site and each materialises a different password. "
                "PR #36231 memoises the first result into .Values.global.redis.password."
            ),
        }, indent=2),
        partial_answer=json.dumps({
            "drift_points": [
                {
                    "file": "bitnami/redis/templates/secret.yaml",
                    "key": "redis-password",
                    "expected": "same generated password everywhere",
                    "actual": "a fresh random value per include",
                    "override_chain": ["_helpers.tpl:redis.password", "secret.yaml"],
                }
            ],
            "root_cause": (
                "The redis.password helper resolves through common.secrets.passwords.manage, "
                "which regenerates on each include."
            ),
        }, indent=2),
    ),
    # 004: ArgoCD redis-ha containerSecurityContext null override.
    #
    # check_config_valid.sh (0.25) is gone and the weights are rebalanced to
    # 0.55/0.45; see the note in the task's task.toml. The old fixture here is
    # itself worth reading as the artifact of the fabricated key it was built to
    # fit: its gt_answer named `securityContext`, which ArgoCD does not override
    # at any level, and passed only because the old checks grepped the report for
    # the prompt's own words. It now scores 0.0 — as it should.
    TaskSpec(
        task_dir=_CD_DIR / "config-drift-004",
        report_path="agent_output/DRIFT_REPORT.json",
        checks=[
            CheckSpec("check_drift_points.sh", 0.55),
            CheckSpec("check_expected_values.sh", 0.45),
        ],
        gt_answer=json.dumps({
            "drift_points": [
                {
                    "file": "manifests/ha/base/redis-ha/chart/values.yaml",
                    "key": "redis-ha.haproxy.containerSecurityContext",
                    "expected": {
                        "runAsNonRoot": True,
                        "allowPrivilegeEscalation": False,
                        "seccompProfile": {"type": "RuntimeDefault"},
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "actual": None,
                    "override_chain": [
                        "dandydeveloper-charts charts/redis-ha/values.yaml:haproxy.containerSecurityContext (table)",
                        "argo-cd manifests/ha/base/redis-ha/chart/values.yaml:redis-ha.haproxy.containerSecurityContext (null)",
                        "manifests/ha/base/redis-ha/chart/upstream.yaml",
                    ],
                },
                {
                    "file": "manifests/ha/base/redis-ha/chart/values.yaml",
                    "key": "redis-ha.containerSecurityContext",
                    "expected": {
                        "runAsUser": 1000,
                        "runAsNonRoot": True,
                        "allowPrivilegeEscalation": False,
                        "seccompProfile": {"type": "RuntimeDefault"},
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "actual": None,
                    "override_chain": [
                        "dandydeveloper-charts charts/redis-ha/values.yaml:containerSecurityContext (table)",
                        "argo-cd manifests/ha/base/redis-ha/chart/values.yaml:redis-ha.containerSecurityContext (null)",
                        "manifests/ha/base/redis-ha/chart/upstream.yaml",
                    ],
                },
            ]
        }, indent=2),
        # Finds the haproxy override and its upstream default, misses the
        # top-level one entirely: 0.5 on both checkpoints.
        partial_answer=json.dumps({
            "drift_points": [
                {
                    "file": "manifests/ha/base/redis-ha/chart/values.yaml",
                    "key": "redis-ha.haproxy.containerSecurityContext",
                    "expected": {
                        "runAsNonRoot": True,
                        "allowPrivilegeEscalation": False,
                        "seccompProfile": {"type": "RuntimeDefault"},
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "actual": None,
                    "override_chain": ["upstream redis-ha values.yaml -> argocd values.yaml"],
                }
            ]
        }, indent=2),
    ),
]


# ── RBAC/security audit tasks ──────────────────────────────────────────────

_SA_DIR = BENCHMARKS / "security_operations"

RBAC_AUDIT_TASKS: list[TaskSpec] = [
    # 001: Calico tier policy authorization bypass
    TaskSpec(
        task_dir=_SA_DIR / "rbac-audit-001",
        report_path="security_audit.md",
        checks=[
            CheckSpec("check_policy_types.sh", 0.20),
            CheckSpec("check_apiserver_auth.sh", 0.25),
            CheckSpec("check_webhook_auth.sh", 0.25),
            CheckSpec("check_bypass_remediation.sh", 0.30),
        ],
        gt_answer="""\
# Security Audit: Calico RBAC Authorization Bypass

## Affected Policy Types

The following 4 policy types have storage implementations in
`apiserver/pkg/registry/projectcalico/`:

1. **GlobalNetworkPolicy** — `globalpolicy/storage.go`
2. **NetworkPolicy** — `networkpolicy/storage.go`
3. **StagedGlobalNetworkPolicy** — `stagedglobalnetworkpolicy/storage.go`
4. **StagedNetworkPolicy** — `stagednetworkpolicy/storage.go`

## Apiserver Authorization Path

In each storage type's `Update()` method, the apiserver checks authorization
against the **old tier** (the existing/current/source tier of the policy). It calls
`storage.go`'s Update which verifies the user has permission on the original tier.
It does NOT check the new/destination tier.

## Webhook Authorization Path

In `webhooks/pkg/rbac/rbac.go`, the `authorize()` function checks authorization
against the **new tier** (the destination/target/updated tier). On UPDATE operations,
it validates the user has permission on the new tier but does NOT check the old tier.

## Bypass Scenario

There is a dual-path authorization gap — an inconsistency between the two paths:
- The apiserver only checks the **old tier** (source tier)
- The webhook only checks the **new tier** (destination tier)

A user can exploit this to move a policy between tiers: if the apiserver path is
used, a user with permissions on the old tier can move a policy to any new tier
without authorization. This is a bypass of the tier authorization model.

**Severity**: High — allows unauthorized policy tier changes.

**Remediation**: Both the apiserver storage Update() and the webhook authorize()
must check **both tiers** (old and new) on UPDATE operations. Fix all 4 policy types.
""",
        partial_answer="""\
# Security Audit

## Policy Types

- GlobalNetworkPolicy in the apiserver registry
- NetworkPolicy

## Authorization

The apiserver storage checks the existing tier in Update().

## Impact

There is a risk of unauthorized changes.
Severity: high
Remediation: fix the checks.
""",
    ),
    # 002: Keycloak FGAP privilege escalation
    TaskSpec(
        task_dir=_SA_DIR / "rbac-audit-002",
        report_path="security_audit.md",
        checks=[
            CheckSpec("check_fgap_location.sh", 0.20),
            CheckSpec("check_permission_model.sh", 0.25),
            CheckSpec("check_escalation_path.sh", 0.30),
            CheckSpec("check_severity_remediation.sh", 0.25),
        ],
        gt_answer="""\
# Security Audit: Keycloak FGAP V2 Privilege Escalation

## FGAP Location

The Fine-Grained Admin Permissions (FGAP) V2 implementation is located in
Keycloak's admin permissions module. The key class is `RealmPermissionsV2`
which controls how admin permissions are evaluated.

## Permission Model

The FGAP V2 permission model allows granular admin permissions for different
operations. The `manage-clients` scope grants ability to manage client
configurations within a realm. The boundary is supposed to limit scope to
client management only.

## Escalation Path

The privilege escalation occurs through the `manage-clients` scope:
- A user with `manage-clients` can escalate beyond client management
- When Admin Permissions are enabled on a realm, the manage-clients
  scope grants broader access than intended

CVE-2026-3121 documents this vulnerability.

## Severity and Remediation

- **Severity**: High / Critical
- **CVE**: CVE-2026-3121
- **Impact**: Users with manage-clients can escalate to broader admin permissions

**Remediation**: Patch to restrict manage-clients scope boundaries. Apply the
fix that tightens the FGAP V2 permission evaluation.
""",
        partial_answer="""\
# Security Audit

## FGAP

The fine-grained admin permission system uses RealmPermissionsV2.

## Permission Model

The manage-clients permission allows client management within a scope boundary.

## Remediation

Apply security fix to restrict permissions.
""",
    ),
    # 003: Harbor robot account wildcard bypass
    TaskSpec(
        task_dir=_SA_DIR / "rbac-audit-003",
        report_path="security_audit.md",
        checks=[
            CheckSpec("check_rbac_architecture.sh", 0.20),
            CheckSpec("check_evaluator_chain.sh", 0.30),
            CheckSpec("check_permission_flow.sh", 0.25),
            CheckSpec("check_remediation.sh", 0.25),
        ],
        gt_answer="""\
# Security Audit: Harbor Robot Account Wildcard Permission Bypass

## RBAC Architecture

Harbor has two types of robot accounts:
- **System robots**: Created at the system level with cross-project access
- **Project robots**: Created within a specific project, scoped to that project

The RBAC evaluator chain determines access through permission evaluation.

## Evaluator Chain Gap

The gap is in `security/robot/context.go`. The `filterRobotPolicies` function
has an inconsistency — it does not properly pass or include the filtering
parameter when evaluating system-to-project robot permissions. There is a
missing parameter that should restrict the policy scope.

## Permission Flow

The `robot.go` handler processes `CreateRobot` requests. The `requireAccess`
function checks permissions, but the wildcard permission pattern (`/project/*`)
allows broader access than intended when creating robots across projects.

## Remediation

- Fix `context.go` to properly pass the filter parameter to `filterRobotPolicies`
- Fix `robot.go` to add wildcard validation before granting cross-project access
- Ensure system robots cannot inherit broader permissions than explicitly granted
""",
        partial_answer="""\
# Security Audit

## Architecture

Harbor has system robot accounts and project robot accounts.

## Issue

There is a gap in the permission evaluation chain in the security module.
A missing check allows broader access than intended.

## Remediation

Add proper validation to the robot account creation path.
""",
    ),
    # 004: Keycloak Bearer token RFC 6750 compliance
    TaskSpec(
        task_dir=_SA_DIR / "rbac-audit-004",
        report_path="security_audit.md",
        checks=[
            CheckSpec("check_auth_manager.sh", 0.15),
            CheckSpec("check_rfc_violations.sh", 0.35),
            CheckSpec("check_attack_vectors.sh", 0.25),
            CheckSpec("check_remediation.sh", 0.25),
        ],
        gt_answer="""\
# Security Audit: Keycloak Bearer Token RFC 6750 Compliance

## Auth Manager Location

The `AppAuthManager` class (AppAuthManager.java) handles Authorization header
parsing and token extraction. The token extraction method parses the Bearer
token from the Authorization header.

## RFC 6750 Violations

1. **Case sensitivity**: The Bearer prefix check is case-sensitive, violating
   RFC 6750 which specifies case-insensitive matching.
2. **Whitespace handling**: The separator between "Bearer" and the token does
   not properly validate the ASCII space character per RFC 6750.
3. **Token character validation**: The b64token character set is not validated.
   The token format allows characters outside the RFC-specified charset.
4. **RFC 6750** compliance is not fully implemented in the token parsing logic.

## Attack Vectors

- **Whitespace smuggling**: Non-standard whitespace characters (tabs, zero-width
  spaces) could be used to bypass validation.
- **Token injection**: Malformed tokens with invalid characters could exploit
  the lack of charset validation.
- **General attack vector**: The vulnerabilities could allow an attacker to
  bypass authentication through crafted Authorization headers.

## Remediation

- **CVE-2026-0707** / GHSA-gv94 documents these issues
- Implement case-insensitive Bearer prefix check
- Add strict parsing with regex for b64token format
- Validate whitespace per RFC 6750 specification
""",
        partial_answer="""\
# Security Audit

## Auth Manager

AppAuthManager handles token extraction from the Authorization header.

## Issues

The Bearer token parsing has case sensitivity issues per RFC 6750.
There are attack vectors involving malformed tokens.

## Remediation

Fix the token validation to be RFC 6750 compliant.
""",
    ),
]


ALL_TASKS = INCIDENT_TASKS + CONFIG_DRIFT_TASKS + RBAC_AUDIT_TASKS

TASK_IDS = [t.task_dir.name for t in ALL_TASKS]


# ── structural tests ────────────────────────────────────────────────────────

class TestCheckpointWeights:
    """Verify checkpoint weights sum to 1.0 for all Phase 4 tasks."""

    @pytest.mark.parametrize("task", ALL_TASKS, ids=TASK_IDS)
    def test_weights_sum_to_one(self, task: TaskSpec) -> None:
        toml_path = task.task_dir / "task.toml"
        assert toml_path.exists(), f"task.toml not found: {toml_path}"

        with open(toml_path, "rb") as f:
            data = tomllib.load(f)

        weights = [cp["weight"] for cp in data["checkpoints"]]
        total = sum(weights)
        assert abs(total - 1.0) < 0.01, (
            f"{task.task_dir.name}: weights sum to {total:.3f}, expected 1.0"
        )


class TestScriptsExistAndExecutable:
    """All check scripts exist and are executable."""

    @pytest.mark.parametrize("task", ALL_TASKS, ids=TASK_IDS)
    def test_scripts_exist(self, task: TaskSpec) -> None:
        for check in task.checks:
            script = task.task_dir / "checks" / check.script_name
            assert script.exists(), f"Missing: {script}"
            assert os.access(script, os.X_OK), f"Not executable: {script}"


# ── ground truth tests ──────────────────────────────────────────────────────

class TestGroundTruthScoresHigh:
    """(a) Ground truth answer should score >= 0.85."""

    @pytest.mark.parametrize("task", ALL_TASKS, ids=TASK_IDS)
    def test_gt_answer_scores_high(self, tmp_path: Path, task: TaskSpec) -> None:
        workspace = tmp_path / "workspace"
        _write_report(workspace, task.report_path, task.gt_answer)

        total = _weighted_score(task, workspace)
        results = _all_results(task, workspace)
        assert total >= 0.85, (
            f"{task.task_dir.name} GT scored {total:.2f} (<0.85). "
            f"Results: {results}"
        )


# ── empty answer tests ──────────────────────────────────────────────────────

class TestEmptyAnswerScoresLow:
    """(b) Empty/missing answer should score <= 0.10."""

    @pytest.mark.parametrize("task", ALL_TASKS, ids=TASK_IDS)
    def test_empty_answer_scores_low(self, tmp_path: Path, task: TaskSpec) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        # No report written — verifiers should return 0

        total = _weighted_score(task, workspace)
        results = _all_results(task, workspace)
        assert total <= 0.10, (
            f"{task.task_dir.name} empty scored {total:.2f} (>0.10). "
            f"Results: {results}"
        )


# ── partial answer tests ────────────────────────────────────────────────────

class TestPartialAnswerScoresMid:
    """(c) Partial answer should score between 0.3 and 0.7."""

    @pytest.mark.parametrize("task", ALL_TASKS, ids=TASK_IDS)
    def test_partial_answer_scores_mid(self, tmp_path: Path, task: TaskSpec) -> None:
        workspace = tmp_path / "workspace"
        _write_report(workspace, task.report_path, task.partial_answer)

        total = _weighted_score(task, workspace)
        results = _all_results(task, workspace)
        assert 0.15 <= total <= 0.90, (
            f"{task.task_dir.name} partial scored {total:.2f} (expected 0.15-0.90). "
            f"Results: {results}"
        )
