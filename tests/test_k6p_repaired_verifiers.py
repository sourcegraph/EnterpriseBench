"""Regression tests for the 8 multi-repo verifiers/GT repaired under EnterpriseBench-k6p.

Follow-up to the y50 soundness triage (results/analysis/soundness_strict_triage_2026_06.md),
which confirmed these 8 tasks were real false-negatives: a hand-constructed *correct*
artifact scored 0 because the verifier was copy-pasted from an unrelated task (Zulip /
Consul tokens + wrong artifact paths), the ground truth was a degenerate placeholder, or
the verifier read the workspace path from a positional arg the runner never supplies.

Each test materialises a hand-constructed correct artifact in a temp workspace and runs the
*real* checkpoint verifier through the production ``CheckpointRunner`` code path, asserting:

  * the correct artifact scores > 0 on every repaired checkpoint (the FN is fixed), and
  * a deterministic-filler / empty artifact still scores 0 (no false-positive introduced),
    excluding the one checkpoint whose default-pass-on-absent-GT is a known P2 item.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eb_verify.runner import CheckpointRunner
from eb_verify.task_parser import parse_task

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCH = REPO_ROOT / "benchmarks"

# Deterministic filler with no domain tokens — mirrors the mutation-test GARBAGE_TEXT.
GARBAGE = (
    "The committee reconvened on a rainy afternoon to discuss the seasonal catering "
    "rota. Several members preferred elderflower cordial, while others argued at length "
    "about the merits of woollen upholstery.\n"
)

# --- Hand-constructed correct artifacts -----------------------------------------------

DJANGO_REPORT = """# Django 5.0 -> Wagtail v5.2 Schema Impact

## Django 5.0 breaking changes (with source file paths)
- New `GeneratedField` in `django/db/models/fields/__init__.py`.
- `Field.contribute_to_class` behaviour changed (db/models/fields/__init__.py).
- Model `Meta` handling changes in `django/db/models/options.py`.

## Affected Wagtail models and their Django dependencies
- `Page` model in wagtail/models/__init__.py.
- `StreamField`, `Orderable`, and `RevisionMixin` depend on changed ORM internals.

## Impact chain
- StreamField in wagtail/fields/streamfield.py breaks admin serialization and queries.
- Migration autodetection changes; admin views raise FieldError.

## Migration coordination requirements
- Coordinate migrations across the django and wagtail repos.
- Affected tests: wagtail test suite and Django model_fields tests.
"""

SENTRY_REPORT = """# Sentry 23.11.0 <-> Relay Schema Impact

## Sentry model changes with file paths
- src/sentry/models/project.py and src/sentry/event_manager.py.
- Data category / event type / project config definitions evolved.

## Corresponding Relay Rust schema definitions
- relay-general/src/protocol/event.rs and relay-server/src/envelope.rs.

## Schema drift points (both-side references)
- Protocol structs and enums differ; Relay validation rejects new event types (drift).

## Impact on event ingestion pipeline
- The event ingestion pipeline rejects envelopes; affected tests across sentry and relay.
"""

ARGOCD_REPORT = {
    "drift_points": [
        {
            "config_key": "controller.operation.processors",
            "argocd_default": "10",
            "argocd_source_file": "cmd/argocd-application-controller/commands/argocd_application_controller.go",
            "helm_override": "20",
            "helm_source_path": "charts/argo-cd/values.yaml: configs.params.controller.operation.processors",
            "impact": "Higher processor count raises API server load and causes sync timeouts under load.",
            "override_chain": ["controller default -> values.yaml -> argocd-cmd-params-cm"],
        },
        {
            "config_key": "controller.self.heal.timeout.seconds",
            "argocd_default": "5",
            "argocd_source_file": "controller/appcontroller.go",
            "helm_override": "2",
            "helm_source_path": "charts/argo-cd/values.yaml: configs.params.controller.self.heal.timeout.seconds",
            "impact": "Shorter self-heal timeout triggers premature reconciliation retries and resource churn.",
            "override_chain": ["util/settings/settings.go -> values.yaml"],
        },
    ]
}

PROMETHEUS_REPORT = {
    "drift_points": [
        {
            "config_key": "storage.tsdb.min-block-duration",
            "prometheus_default": "2h",
            "prometheus_source_file": "tsdb/db.go",
            "thanos_expectation": "min and max block duration must be equal (2h) for the sidecar to upload blocks",
            "thanos_source_file": "cmd/thanos/sidecar.go",
            "failure_mode": "If min != max block duration the Thanos sidecar refuses to upload blocks to object storage.",
            "override_chain": ["cmd/prometheus/main.go flag default -> tsdb options"],
        },
        {
            "config_key": "storage.tsdb.retention.time",
            "prometheus_default": "15d",
            "prometheus_source_file": "cmd/prometheus/main.go",
            "thanos_expectation": "retention should exceed the shipper upload interval; external labels required",
            "thanos_source_file": "pkg/shipper/shipper.go",
            "failure_mode": "Short retention plus missing external labels causes WAL gaps and failed uploads.",
            "override_chain": ["main.go -> tsdb/db.go"],
        },
    ]
}

REACT_REPORT = [
    {
        "react_module": "packages/react/src/ReactSharedInternals.js",
        "react_symbol": "__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED",
        "cra_references": ["packages/react-scripts/config/webpack.config.js"],
        "kind": "deprecated_api",
        "confidence": "high",
        "evidence": "Deprecated internals escape hatch; no longer load-bearing after react/jsx-runtime; no callers in CRA build path.",
        "load_bearing": False,
    }
]

K8S_REPORT = [
    {
        "api_group": "flowcontrol.apiserver.k8s.io/v1beta2",
        "status": "deprecated",
        "k8s_evidence_file": "staging/src/k8s.io/api/flowcontrol/v1beta2/types.go",
        "client_go_files": [
            "kubernetes/typed/flowcontrol/v1beta2/flowcontrol_client.go",
            "informers/flowcontrol/v1beta2/interface.go",
        ],
        "kind": "typed_client",
        "confidence": "high",
        "evidence": "v1beta2 deprecated in 1.26, removed in 1.29; typed client is dead in 1.28 as the API server steers to v1beta3.",
        "safe_to_remove": False,
    }
]

EVENT_ACTIONS = "\n".join(
    json.dumps(a)
    for a in [
        {"timestamp_ms": 95000, "action_type": "investigate", "target": "click/src/click/utils.py",
         "description": "Trace the AttributeError to click.utils use of markupsafe.soft_str"},
        {"timestamp_ms": 125000, "action_type": "triage", "target": "click",
         "description": "CI-only impact, priority medium"},
        {"timestamp_ms": 130000, "action_type": "communicate", "target": "#pallets-dev",
         "description": "Post root cause: markupsafe removed soft_str; click.utils needs updating"},
        {"timestamp_ms": 140000, "action_type": "remediate", "target": "click/src/click/utils.py",
         "description": "Replace markupsafe.soft_str and pin markupsafe in requirements"},
    ]
)


def _write(workspace: Path, rel: str, content: str) -> None:
    target = workspace / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


# (task_dir, artifact_rel_path, correct_content, [repaired checkpoint names],
#  [checkpoints exempt from the no-false-positive assertion])
CASES = [
    ("feature_delivery/schema-evolution-dual-django-001", "SCHEMA_IMPACT.md", DJANGO_REPORT,
     ["schema_change", "direct_refs", "indirect_refs", "test_impact"], []),
    ("feature_delivery/schema-evolution-dual-sentry-001", "SCHEMA_IMPACT.md", SENTRY_REPORT,
     ["schema_change", "direct_refs", "indirect_refs", "test_impact"], []),
    ("platform_engineering/config-drift-dual-argocd-001", "DRIFT_REPORT.json", json.dumps(ARGOCD_REPORT),
     ["drift_points", "expected_values", "config_valid"], []),
    ("platform_engineering/config-drift-dual-prometheus-001", "DRIFT_REPORT.json", json.dumps(PROMETHEUS_REPORT),
     ["drift_points", "expected_values", "config_valid"], []),
    ("technical_debt/dead-code-dual-react-001", "dead_code_report.json", json.dumps(REACT_REPORT),
     ["dead_code", "evidence", "feature_flags"], ["feature_flags"]),
    ("technical_debt/dead-code-dual-k8s-001", "dead_code_report.json", json.dumps(K8S_REPORT),
     ["dead_code", "evidence", "feature_flags"], ["feature_flags"]),
    ("incident_response/event-replay-click-ci-001", "actions.jsonl", EVENT_ACTIONS,
     ["identify_root_cause", "triage_severity", "communication", "remediation"], []),
]


def _runner(task_dir: Path, workspace: Path) -> CheckpointRunner:
    task = parse_task(task_dir / "task.toml")
    return CheckpointRunner(task, task_dir=task_dir, workspace=workspace)


@pytest.mark.parametrize("rel, artifact, content, checkpoints, _exempt", CASES,
                         ids=[c[0].split("/")[-1] for c in CASES])
def test_correct_artifact_scores_above_zero(tmp_path, rel, artifact, content, checkpoints, _exempt):
    """A hand-constructed correct artifact must score > 0 on every repaired checkpoint."""
    task_dir = BENCH / rel
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write(workspace, artifact, content)
    runner = _runner(task_dir, workspace)
    for name in checkpoints:
        result = runner.run_single(name)
        assert result.score > 0.0, f"{rel}:{name} scored {result.score} on a correct artifact: {result.detail}"


@pytest.mark.parametrize("rel, artifact, content, checkpoints, exempt", CASES,
                         ids=[c[0].split("/")[-1] for c in CASES])
def test_filler_and_empty_artifacts_score_zero(tmp_path, rel, artifact, content, checkpoints, exempt):
    """Deterministic-filler and empty artifacts must score 0 (no false positive introduced).

    ``exempt`` lists checkpoints whose default-pass-on-absent-ground-truth is a known P2
    inflation item (deferred per y50 recommendation #2), not repaired here.
    """
    task_dir = BENCH / rel
    empty_value = "[]" if artifact.endswith(".json") and content.lstrip().startswith("[") else GARBAGE
    for variant, payload in (("filler", GARBAGE), ("empty", empty_value)):
        workspace = tmp_path / variant
        workspace.mkdir()
        _write(workspace, artifact, payload)
        runner = _runner(task_dir, workspace)
        for name in checkpoints:
            if name in exempt:
                continue
            result = runner.run_single(name)
            assert result.score == 0.0, (
                f"{rel}:{name} scored {result.score} on a {variant} artifact "
                f"(false positive): {result.detail}"
            )
