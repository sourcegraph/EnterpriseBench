"""Coverage half of the support-mapping re-anchor (EnterpriseBench-jn73.2.7.3.1.4).

The echo invariants (test_report_prompt_echo.py, test_structured_prompt_echo.py)
prove a prompt echo earns nothing, but they cannot prove a CORRECT answer still
earns credit — a check whose scoring_evidence is padded with tokens no real
answer contains silences the echo gates by failing every deliverable equally,
turning a leaking task into an un-passable one (see the warning in
test_report_prompt_echo.py's docstring). This test pins the other half for the
re-anchored support-mapping family: a hand-built correct answer — file paths,
symbols and config values taken from ground truth and verified against the
pinned repo revisions — scores FULL credit on every re-anchored check.

It also pins the leak half family-locally (every re-anchored check scores 0.0
on the structured prompt echo), so the property holds even while the tasks sit
in the structured-echo quarantine awaiting integrator dequarantine.

The fixtures are grading-side assets: they live with the tests, never in the
sandbox, and an agent never sees them.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "validation"))
import curated_gate_analyzer as gate  # noqa: E402

CE = "customer_escalation"

# task -> (re-anchored checks, correct answer built from ground truth)
CASES: dict[str, dict] = {
    f"{CE}/support-mapping-001": {
        "rewritten": ["check_severity.sh"],
        "answer": {
            "code_paths": [
                "source/common/http/conn_pool_base.cc",
                "source/common/http/conn_pool_base.h",
                "source/common/conn_pool/conn_pool_base.cc",
                "source/common/upstream/conn_pool_map_impl.h",
                "envoy/http/conn_pool.h",
                "docs/root/intro/arch_overview/upstream/circuit_breaking.rst",
                "envoy/router/router.h",
            ],
            "ownership": (
                "Envoy upstream connection pooling subsystem: "
                "source/common/http/conn_pool_base.cc and conn_pool_base.h, the "
                "generic source/common/conn_pool/conn_pool_base.cc, pools managed "
                "per host via source/common/upstream/conn_pool_map_impl.h"
            ),
            "severity": {
                "level": "high",
                "rationale": (
                    "Peak-traffic requests are refused once the cluster "
                    "circuit-breaker thresholds max_connections / "
                    "max_pending_requests overflow in conn_pool_base; production "
                    "traffic is dropped during load."
                ),
            },
            "related_issues": [
                "docs/root/intro/arch_overview/upstream/circuit_breaking.rst",
                "envoy/http/conn_pool.h",
            ],
        },
    },
    f"{CE}/support-mapping-002": {
        "rewritten": ["check_ownership.sh"],
        "answer": {
            "code_paths": [
                "llvm/lib/CodeGen/SelectionDAG/LegalizeDAG.cpp",
                "llvm/lib/CodeGen/SelectionDAG/LegalizeTypes.cpp",
                "llvm/include/llvm/CodeGen/ValueTypes.h",
                "llvm/lib/CodeGen/SelectionDAG/LegalizeIntegerTypes.cpp",
                "llvm/lib/CodeGen/SelectionDAG/LegalizeFloatTypes.cpp",
                "llvm/lib/CodeGen/SelectionDAG/SelectionDAGISel.cpp",
            ],
            "ownership": (
                "LLVM CodeGen SelectionDAG type-legalization subsystem "
                "(llvm/lib/CodeGen/SelectionDAG): LegalizeDAG.cpp and "
                "LegalizeTypes.cpp own the Cannot-expand assertion path, EVT "
                "definitions in llvm/include/llvm/CodeGen/ValueTypes.h"
            ),
            "severity": (
                "critical - the compiler assertion aborts every build on the "
                "affected target, blocking CI"
            ),
            "related_issues": [
                "llvm/lib/CodeGen/SelectionDAG/LegalizeIntegerTypes.cpp",
                "llvm/lib/CodeGen/SelectionDAG/LegalizeFloatTypes.cpp",
            ],
        },
    },
    f"{CE}/support-mapping-003": {
        "rewritten": ["check_ownership.sh", "check_severity.sh"],
        "answer": {
            "code_paths": [
                "pkg/services/dashboardimport/service/service.go",
                "pkg/services/dashboards/database/database.go",
                "pkg/components/simplejson/simplejson.go",
                "pkg/services/dashboards/service/dashboard_service.go",
                "public/app/features/dashboard/state/DashboardModel.ts",
                "public/app/features/manage-dashboards/DashboardImportPage.tsx",
            ],
            "ownership": (
                "Grafana dashboards backend: pkg/services/dashboardimport "
                "(import service), pkg/services/dashboards (service + database "
                "layer), JSON model handling in pkg/components/simplejson; "
                "frontend schema handling in DashboardModel.ts"
            ),
            "severity": {
                "level": "medium",
                "rationale": (
                    "On import the schemaVersion migration in DashboardModel.ts "
                    "/ DashboardMigrator.ts rewrites panels and drops unmigrated "
                    "fieldConfig overrides (column widths, alignment, "
                    "thresholds); cosmetic loss with a manual workaround, no "
                    "query or data loss."
                ),
            },
            "related_issues": [
                "public/app/features/dashboard/state/DashboardModel.ts",
                "public/app/features/manage-dashboards/DashboardImportPage.tsx",
            ],
        },
    },
    f"{CE}/support-mapping-004": {
        "rewritten": ["check_ownership.sh", "check_severity.sh"],
        "answer": {
            "code_paths": [
                "pkg/services/ngalert/schedule/schedule.go",
                "dispatch/dispatch.go",
                "notify/notify.go",
                "pkg/services/ngalert/eval/eval.go",
                "pkg/services/ngalert/schedule/registry.go",
                "pkg/services/ngalert/models/alert_rule.go",
                "api/v2/api.go",
                "silence/silence.go",
            ],
            "ownership": (
                "Grafana: pkg/services/ngalert/schedule owns evaluation timing "
                "(Scheduler tick loop in schedule.go); Alertmanager: "
                "dispatch/dispatch.go (Dispatcher, routing tree) and "
                "notify/notify.go (notification pipeline, nflog dedup) own "
                "routing and delivery"
            ),
            "severity": {
                "level": "high",
                "rationale": (
                    "Two compounding failure modes: evaluation overrun in "
                    "pkg/services/ngalert/schedule/schedule.go delays firing by "
                    "10-15 minutes, and delivery failures in notify/notify.go "
                    "drop notifications after dispatch - alerts arrive late or "
                    "never."
                ),
            },
            "related_issues": [
                "pkg/services/ngalert/eval/eval.go",
                "pkg/services/ngalert/schedule/registry.go",
                "dispatch/dispatch.go",
                "notify/notify.go",
                "api/v2/api.go",
                "silence/silence.go",
            ],
        },
    },
    f"{CE}/support-mapping-dual-flask-001": {
        "rewritten": [
            "check_ownership.sh", "check_severity.sh", "check_related_issues.sh",
        ],
        "answer": {
            "code_paths": [
                "src/flask/app.py",
                "src/werkzeug/routing/map.py",
                "src/flask/scaffold.py",
                "src/flask/sansio/app.py",
                "src/werkzeug/routing/rules.py",
            ],
            "ownership": (
                "Pallets routing stack: Flask registration in src/flask/app.py "
                "and src/flask/sansio/app.py (Flask.add_url_rule), URL matching "
                "owned by the src/werkzeug/routing package (Map, Rule, "
                "MapAdapter)"
            ),
            "severity": {
                "level": "medium",
                "rationale": (
                    "PUT returns 405 because MapAdapter.match raises "
                    "MethodNotAllowed when Rule.methods no longer carries the "
                    "verb after the Werkzeug 3.0.1 method-handling change; one "
                    "verb broken per endpoint, workaround exists."
                ),
            },
            "related_issues": [
                "src/flask/sansio/app.py",
                "src/werkzeug/routing/rules.py",
                "src/werkzeug/routing/converters.py",
            ],
        },
    },
    f"{CE}/support-mapping-dual-grafana-001": {
        "rewritten": ["check_ownership.sh", "check_severity.sh"],
        "answer": {
            "code_paths": [
                "pkg/api/dashboard.go",
                "promql/engine.go",
                "public/app/plugins/datasource/prometheus/datasource.ts",
                "web/api/v1/api.go",
            ],
            "ownership": (
                "Prometheus PromQL engine (promql/engine.go) enforces the "
                "sample limit; surfaced through the web/api/v1 HTTP API and the "
                "Grafana public/app/plugins/datasource prometheus plugin"
            ),
            "severity": {
                "level": "high",
                "rationale": (
                    "Complex dashboard queries fail during execution when the "
                    "engine maxSamples limit (set by --query.max-samples) is "
                    "exceeded; monitoring visibility is lost for exactly the "
                    "heavy queries operators rely on."
                ),
            },
            "related_issues": [
                "pkg/services/datasources/datasources.go",
                "web/api/v1/api.go",
                "promql/parser/parse.go",
            ],
        },
    },
    f"{CE}/support-mapping-dual-ansible-001": {
        "rewritten": ["check_error_chain.sh", "check_trigger_conditions.sh"],
        "answer": {
            "source_files": [
                "lib/ansible/template/__init__.py",
                "src/jinja2/lexer.py",
            ],
            "error_chain": [
                "lookup(file) in lib/ansible/plugins/lookup/file.py run() reads "
                "raw bytes from /etc/ssl/certs/ca-bundle.crt",
                "Templar.do_template in lib/ansible/template/__init__.py embeds "
                "the binary lookup result into the template string",
                "the template source is handed to Jinja2 Environment.parse for "
                "compilation",
                "Lexer.tokeniter in src/jinja2/lexer.py rejects the non-UTF-8 "
                "byte and raises TemplateSyntaxError (unexpected char)",
                "Ansible catches the exception and wraps it with template "
                "context information",
            ],
            "trigger_conditions": [
                "the lookup plugin (lib/ansible/plugins/lookup/file.py) returns "
                "non-UTF-8 binary content that is embedded into the template "
                "text before Jinja2 parses it",
                "the Jinja2 Lexer.tokeniter validates every source character "
                "during lexing, before any filter such as b64encode runs",
                "binary data is only handled correctly when encoded before "
                "embedding; the b64encode filter is never reached because the "
                "lexer fails first",
            ],
        },
    },
    f"{CE}/support-mapping-dual-httpx-httpcore-001": {
        "rewritten": ["check_trigger_conditions.sh"],
        "answer": {
            "source_files": [
                "httpx/httpx/_config.py",
                "httpx/httpx/_transports/default.py",
                "httpcore/httpcore/_async/connection_pool.py",
                "httpcore/httpcore/_async/http11.py",
            ],
            "error_chain": [
                "httpx.AsyncClient is constructed with a Timeout instance from "
                "httpx/_config.py whose connect, read, write and pool "
                "dimensions accept a number or None",
                "when no timeout argument is passed httpx falls back to "
                "DEFAULT_TIMEOUT_CONFIG which is Timeout(timeout=5.0) defined "
                "in httpx/_config.py and applied uniformly across all four "
                "timeout dimensions",
                "httpx.AsyncClient.stream eventually dispatches the request "
                "through AsyncHTTPTransport.handle_async_request in "
                "httpx/_transports/default.py which constructs an "
                "httpcore.Request and passes the per-request timeout via the "
                "extensions dict not via the ConnectionPool constructor",
                "httpcore.AsyncConnectionPool in "
                "httpcore/_async/connection_pool.py reads timeout values out "
                "of the request extensions and applies the pool dimension when "
                "calling wait_for_connection and the read or write dimension "
                "on the underlying AsyncHTTP11Connection in "
                "httpcore/_async/http11.py during _receive_response_headers "
                "and _receive_response_body",
                "if httpcore enforces a read timeout it raises "
                "httpcore.ReadTimeout which is then re-raised as "
                "httpx.ReadTimeout by the map_httpcore_exceptions context "
                "manager in httpx/_transports/default.py using the "
                "HTTPCORE_EXC_MAP table",
            ],
            "trigger_conditions": [
                "omitting the timeout argument (or passing a bare number) "
                "falls back to DEFAULT_TIMEOUT_CONFIG = Timeout(timeout=5.0) "
                "defined in httpx/_config.py, capping read at 5 seconds",
                "an explicit Timeout(timeout=None) marshals None per dimension "
                "into the httpcore request extensions and disables "
                "per-operation timeouts",
                "map_httpcore_exceptions in httpx/_transports/default.py "
                "re-raises httpcore.ReadTimeout as httpx.ReadTimeout via "
                "HTTPCORE_EXC_MAP, hiding the cross-repo origin",
            ],
        },
    },
}

TASK_IDS = sorted(CASES)


def _correct_answer_scores(task_rel: str) -> dict:
    task_dir = gate.BENCH / task_rel
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        payload = json.dumps(CASES[task_rel]["answer"], indent=2)
        gate.materialize(ws, gate.deliverable_paths(task_dir), payload)
        return gate.run_checks(task_dir, ws)


@pytest.mark.parametrize("task_rel", TASK_IDS)
def test_correct_answer_earns_full_credit_on_reanchored_checks(task_rel: str) -> None:
    """The re-anchor must not zero the right answer along with the echo."""
    scores = _correct_answer_scores(task_rel)
    short = {c: scores.get(c) for c in CASES[task_rel]["rewritten"]}
    assert all(v is not None and v >= 1.0 for v in short.values()), (
        f"{task_rel}: a ground-truth-correct answer no longer earns full credit "
        f"on the re-anchored checks: {short}. Either scoring_evidence drifted "
        f"from the pinned repo reality or a check regressed."
    )


@pytest.mark.parametrize("task_rel", TASK_IDS)
def test_reanchored_checks_score_zero_on_structured_echo(task_rel: str) -> None:
    """Family-local pin of the leak half, live even while quarantined."""
    scores = gate.structured_echo_scores(gate.BENCH / task_rel)
    assert scores, f"{task_rel}: structured vector produced no runs"
    for check in CASES[task_rel]["rewritten"]:
        for variant, per_check in scores.items():
            got = per_check.get(check)
            assert got == 0.0, (
                f"{task_rel}: {check} scored {got!r} on the structured prompt "
                f"echo ({variant}) — it must evaluate the echo and award 0.0"
            )


def test_fixture_answers_do_not_drift_from_ground_truth() -> None:
    """Every scoring_evidence token graded by a re-anchored check appears in the
    fixture answer, so the full-credit test above actually exercises every
    token, and the fixture fails loudly when an integrator edits the tokens."""
    for task_rel, case in CASES.items():
        gt = json.loads((gate.BENCH / task_rel / "ground_truth.json").read_text())
        evidence = gt.get("scoring_evidence") or {}
        graded = [
            t
            for cp, toks in evidence.items()
            if not cp.startswith("_") and isinstance(toks, list)
            for t in toks
        ]
        blob = json.dumps(case["answer"]).lower()
        missing = [t for t in graded if t.lower() not in blob]
        assert not missing, (
            f"{task_rel}: fixture answer no longer contains graded evidence "
            f"tokens {missing}"
        )
