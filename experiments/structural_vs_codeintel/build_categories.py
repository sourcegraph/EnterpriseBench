"""Assign each slice task the retrieval-demand categories the brief enumerates.

A label describes what the task ASKS FOR -- trace a symbol, map a dependency,
match a syntactic shape -- read off its own instruction.md and ground_truth.json.
It never encodes which primitive ought to win, and it is never used to select
tasks into a condition: every condition runs every task. The labels exist only
to slice the results afterwards, which is the one way a per-category claim can
be made without choosing the categories after seeing the scores.

Multi-label by construction. A cross-repo error-provenance task is genuinely
both, and forcing one label per task would make the per-category means depend
on an arbitrary tie-break.

Usage:
    python3 build_categories.py            # writes task_categories.json
    python3 build_categories.py --check    # fails if the manifest is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: The brief's eleven axes. Closed set: a typo must fail, not create a category.
CATEGORIES = {
    "identifier_lookup": "Find a known identifier or literal string.",
    "syntactic_pattern": "Find code matching a syntactic shape rather than a name.",
    "api_call_sites": "Enumerate the call sites of an API.",
    "interface_implementations": "Locate implementations behind an interface or trait.",
    "symbol_tracing": "Trace a definition or its references across a call path.",
    "dependency_impact": "Analyse dependency edges or the blast radius of a change.",
    "cross_file": "Evidence is spread over several files in one repository.",
    "cross_repo": "Evidence is spread over more than one repository.",
    "refactor_discovery": "Enumerate every site a planned refactor must touch.",
    "framework_noise": "Framework-heavy code where textual matches are noisy.",
    "dynamic_ambiguous": "Dynamic or reflective code where syntax alone underdetermines the target.",
}

_L = {
    # ---- single repo, Python libraries: parameter/flow behaviour inside one package
    "calibration-002": (["symbol_tracing", "cross_file", "dynamic_ambiguous"],
        "Asks where Click resolves flag_value against default; decorator kwargs are "
        "consumed reflectively, so the target is a parameter's flow, not a name."),
    "calibration-003": (["symbol_tracing", "cross_file", "dynamic_ambiguous"],
        "Where requests strips Proxy-Authorization across redirect/rebuild-auth paths."),
    "calibration-005": (["symbol_tracing", "cross_file", "dynamic_ambiguous"],
        "Traces a Sentinel default value from parameter construction to callback invocation."),
    "calibration-007": (["symbol_tracing", "cross_file", "dynamic_ambiguous"],
        "Precedence between session.verify and REQUESTS_CA_BUNDLE: a merge path, not a symbol."),
    "calibration-008": (["symbol_tracing", "cross_file", "dynamic_ambiguous"],
        "Stream lifetime inside CliRunner.invoke; ownership crosses context managers."),
    "chain-err-flask-import-001": (["dependency_impact", "cross_file", "framework_noise",
        "dynamic_ambiguous"],
        "Verify each claimed import edge against the source; Flask's lazy imports make "
        "the textual import list an unreliable proxy for the real edge."),
    "err-provenance-03": (["symbol_tracing", "cross_file"],
        "Trace a kube-apiserver poststarthook failure string to the controller that emits it."),
    "support-mapping-001": (["symbol_tracing", "cross_file", "framework_noise"],
        "Map an Envoy overflow reset reason to the conn-pool code that raises it."),
    "support-mapping-002": (["symbol_tracing", "cross_file"],
        "Locate the LLVM assertion and the type-legalization path that reaches it."),
    "support-mapping-003": (["symbol_tracing", "cross_file", "framework_noise"],
        "Grafana dashboard field-config import path across schema migration code."),
    # ---- impact analysis inside one repo
    "schema-evolution-001": (["api_call_sites", "dependency_impact", "refactor_discovery",
        "cross_file", "framework_noise", "dynamic_ambiguous"],
        "Enumerate every Python reference to a Django model field being added; "
        "Django ORM access is attribute-based, so references are not import edges."),
    "schema-evolution-002": (["api_call_sites", "dependency_impact", "refactor_discovery",
        "cross_file", "framework_noise", "dynamic_ambiguous"],
        "Same shape for the export-tracking model replacing a JSON file."),
    "ansible-galaxy-tar-regression-prove-001": (["syntactic_pattern", "api_call_sites",
        "cross_file", "dynamic_ambiguous"],
        "Find a custom tar-member cache used in place of the stdlib lookup, then its "
        "call sites: the defect is a code shape, not a named symbol."),
    "ccx-incident-032": (["symbol_tracing", "cross_file", "framework_noise"],
        "Envoy overflow string plus the conn-pool files and the protobuf that configures it."),
    "ceph-rgw-auth-secure-001": (["syntactic_pattern", "interface_implementations",
        "cross_file"],
        "Locate the SigV4 auth engine implementations in a large C++ tree by their shape."),
    "refactor-orchestration-006": (["dependency_impact", "refactor_discovery", "cross_file",
        "syntactic_pattern"],
        "Derive from the checkout which files a Go toolchain pin actually governs; the "
        "targets are version-pin expressions across manifests, not symbols."),
    # ---- dual-repo error provenance
    "err-provenance-dual-grafana-001": (["symbol_tracing", "cross_repo", "cross_file",
        "framework_noise"], "Grafana proxy deadline to the Prometheus query engine timeout."),
    "err-provenance-dual-requests-001": (["symbol_tracing", "cross_repo", "cross_file",
        "dynamic_ambiguous"], "requests SSLError down into urllib3 certificate verification."),
    "error-prov-dual-netty-spring-001": (["symbol_tracing", "interface_implementations",
        "cross_repo", "cross_file", "framework_noise"],
        "Netty pipeline exception through reactor-netty into WebFlux error handling."),
    "error-prov-dual-otel-jaeger-001": (["symbol_tracing", "interface_implementations",
        "cross_repo", "cross_file"],
        "Span-export errors across the OTel exporter interface into the Jaeger exporter."),
    "error-prov-dual-tokio-tower-001": (["symbol_tracing", "interface_implementations",
        "cross_repo", "cross_file"],
        "A tokio JoinError through tower's Service layers: the boundary is a trait impl."),
    "support-mapping-004": (["symbol_tracing", "cross_repo", "cross_file", "framework_noise"],
        "Grafana alert evaluation scheduling into Alertmanager delivery."),
    "support-mapping-dual-axum-tower-middleware-001": (["symbol_tracing",
        "interface_implementations", "cross_repo", "cross_file"],
        "Router layer composition against tower ServiceBuilder ordering."),
    "support-mapping-dual-babel-swc-decorators-001": (["syntactic_pattern", "symbol_tracing",
        "cross_repo", "cross_file", "dynamic_ambiguous"],
        "Decorator transform stage selection in two compilers; the subject is AST-node "
        "handling, and both repos are full of the syntax being discussed."),
    "support-mapping-dual-envoy-istio-xds-001": (["symbol_tracing", "cross_repo",
        "cross_file", "framework_noise"],
        "Envoy ListenerManager drain decision against Istio ADS push ordering."),
    "support-mapping-dual-fastapi-pydantic-validator-001": (["symbol_tracing", "cross_repo",
        "cross_file", "framework_noise", "dynamic_ambiguous"],
        "FastAPI body validation into Pydantic v2 discriminated-union resolution, which "
        "is driven by runtime-constructed validator chains."),
    "support-mapping-dual-flask-001": (["symbol_tracing", "cross_repo", "cross_file",
        "framework_noise"], "Flask add_url_rule through Werkzeug routing method matching."),
    "support-mapping-dual-flask-werkzeug-ctxvar-001": (["symbol_tracing", "cross_repo",
        "cross_file", "framework_noise", "dynamic_ambiguous"],
        "The `request` proxy binding onto a ContextVar-backed Local: the definition the "
        "agent needs is behind a proxy object, not the name it reads."),
    "support-mapping-dual-httpx-httpcore-001": (["symbol_tracing", "cross_repo", "cross_file"],
        "How a None timeout becomes a bounded read timeout across the httpcore boundary."),
    "support-mapping-dual-jest-babel-cache-001": (["symbol_tracing", "cross_repo",
        "cross_file", "framework_noise", "dynamic_ambiguous"],
        "Transform cache-key inputs across jest-runtime and babel-jest."),
    "support-mapping-dual-nextjs-webpack-001": (["dependency_impact", "cross_repo",
        "cross_file", "framework_noise"],
        "Module Federation shared-dependency resolution between Next.js and webpack."),
    "support-mapping-dual-react-relay-suspense-001": (["symbol_tracing", "cross_repo",
        "cross_file", "framework_noise"],
        "Reconciler Suspense re-render decision against Relay invalidation notification."),
    "support-mapping-dual-sqlalchemy-alembic-metadata-001": (["symbol_tracing", "cross_repo",
        "cross_file", "dynamic_ambiguous"],
        "Which MetaData instance autogenerate compares against; registration is implicit "
        "and happens at import time."),
    "support-mapping-dual-tokio-hyper-graceful-001": (["symbol_tracing", "cross_repo",
        "cross_file"], "hyper graceful-shutdown connection tracking against tokio task drain."),
    "support-mapping-dual-tonic-tokio-streamcancel-001": (["symbol_tracing",
        "interface_implementations", "cross_repo", "cross_file"],
        "tonic Streaming over a tokio mpsc Receiver: a Stream impl over a channel."),
    # ---- dependency / contract analysis
    "api-contract-dual-fastapi-001": (["dependency_impact", "interface_implementations",
        "cross_repo", "cross_file", "framework_noise", "dynamic_ambiguous"],
        "FastAPI body validation contract against httpx request encoding."),
    "api-contract-dual-serde-csv-001": (["interface_implementations", "dependency_impact",
        "cross_repo", "cross_file"],
        "The Deserializer trait serde exposes and csv's implementation of it: the "
        "canonical find-the-implementation-behind-the-abstraction shape."),
    "dep-graph-dual-log4j-spring-001": (["dependency_impact", "interface_implementations",
        "cross_repo", "cross_file", "framework_noise"],
        "Log4j bridge modules against Spring Boot logging auto-configuration detection."),
    "dep-graph-dual-prometheus-thanos-001": (["dependency_impact", "api_call_sites",
        "cross_repo", "cross_file"],
        "Which Prometheus TSDB APIs Thanos consumes, and where it calls them."),
    "schema-evolution-dual-django-001": (["dependency_impact", "refactor_discovery",
        "cross_repo", "cross_file", "framework_noise", "dynamic_ambiguous"],
        "Django 5.0 field internals against Wagtail's Page/StreamField assumptions."),
    "config-drift-dual-setuptools-pip-001": (["dependency_impact", "api_call_sites",
        "cross_repo", "cross_file", "dynamic_ambiguous"],
        "Which pip features use the vendored pkg_resources APIs setuptools deprecated."),
    "config-drift-dual-terraform-001": (["dependency_impact", "interface_implementations",
        "cross_repo", "cross_file"],
        "Terraform core state refresh against provider Read implementations."),
    "dead-code-dual-react-001": (["api_call_sites", "dependency_impact", "refactor_discovery",
        "cross_repo", "cross_file", "framework_noise", "dynamic_ambiguous"],
        "Exports with no internal callers, then external references to them: an "
        "absence-of-call-sites question over dynamically re-exported modules."),
    "refactor-dual-axum-tower-001": (["refactor_discovery", "interface_implementations",
        "cross_repo", "cross_file"],
        "Router type parameters against tower's middleware-composition traits."),
    # ---- dual-repo incidents
    "incident-investigation-dual-cilium-001": (["symbol_tracing", "cross_repo", "cross_file"],
        "BPF program load and map creation across the agent and its loader library."),
    "incident-investigation-dual-cortex-001": (["symbol_tracing", "cross_repo", "cross_file"],
        "UTF-8 label validation between the ruler and the alert-delivery path."),
    "incident-investigation-dual-flux-001": (["symbol_tracing", "cross_repo", "cross_file"],
        "HelmRelease readiness condition across helm-controller and its API types."),
    "incident-investigation-dual-loki-001": (["symbol_tracing", "cross_repo", "cross_file"],
        "Ring health accounting for LEAVING instances across Loki and dskit."),
    "incident-investigation-dual-nats-001": (["symbol_tracing", "cross_repo", "cross_file"],
        "Consumer pending-state bookkeeping against stream retention reclaim."),
    "incident-investigation-dual-opa-001": (["symbol_tracing", "dependency_impact",
        "cross_repo", "cross_file"],
        "Which OPA compiler version Gatekeeper vendors and how rego.v1 parsing differs."),
    "incident-investigation-dual-prometheus-001": (["symbol_tracing", "cross_repo",
        "cross_file"], "Inhibition-rule matching in Alertmanager against the recording rule."),
    "incident-investigation-tri-containerd-001": (["symbol_tracing", "cross_repo",
        "cross_file"], "Platform matching and exec setup across containerd, runc, and image spec."),
    # ---- three-repo contract / orchestration
    "err-provenance-tri-httpx-proxy-001": (["symbol_tracing", "cross_repo", "cross_file"],
        "A truncated proxied response across httpx, httpcore, and the CONNECT tunnel."),
    "err-provenance-tri-httpx-socks-001": (["symbol_tracing", "cross_repo", "cross_file"],
        "Header-line parsing strictness across httpx, httpcore, and the SOCKS transport."),
    "api-contract-tri-envoy-001": (["dependency_impact", "interface_implementations",
        "cross_repo", "cross_file"],
        "The xDS contract as implemented by Envoy, Istio pilot, and go-control-plane."),
    "api-contract-tri-protobuf-001": (["dependency_impact", "interface_implementations",
        "cross_repo", "cross_file"],
        "Well-known-type definitions, their gRPC runtime handling, and googleapis usage."),
    "dep-graph-tri-boto3-urllib3-requests-001": (["dependency_impact", "cross_repo",
        "cross_file"],
        "Version pins and a vendored urllib3 divergence across three manifests."),
    "dep-graph-tri-prometheus-alertmanager-grafana-001": (["dependency_impact",
        "api_call_sites", "cross_repo", "cross_file"],
        "The three-way alert format contract and where each side reads it."),
    "schema-evolution-tri-supabase-001": (["dependency_impact", "refactor_discovery",
        "cross_repo", "cross_file"],
        "An auth-table migration against PostgREST schema reflection and GoTrue's "
        "hardcoded column expectations."),
    "config-drift-tri-kustomize-001": (["dependency_impact", "interface_implementations",
        "cross_repo", "cross_file"],
        "Strategic-merge-patch handling in Kustomize, ArgoCD, and Flux."),
    "refactor-orchestration-tri-babel-001": (["dependency_impact", "refactor_discovery",
        "cross_repo", "syntactic_pattern", "cross_file"],
        "Which repos consume Babel, cited from manifests, plus the AST-walking sites a "
        "new node type reaches."),
    "refactor-orchestration-tri-spring-001": (["dependency_impact", "refactor_discovery",
        "interface_implementations", "cross_repo", "cross_file", "framework_noise"],
        "Listener-container threading across framework, kafka, and boot autoconfiguration."),
    "refactor-orchestration-tri-tokio-001": (["dependency_impact", "refactor_discovery",
        "cross_repo", "cross_file", "api_call_sites"],
        "Every Runtime construction site across tokio, hyper, and axum, plus the "
        "manifest edges that order the rollout."),
}

#: Axes the hard slice does not contain, recorded so the report can say the
#: experiment is silent on them rather than implying it tested them. The slice
#: selects for tasks that disclose almost none of their own ground truth, which
#: is precisely what excludes a task whose answer is one known identifier.
#: Axes the ORIGINAL 63-task slice did not contain. Kept after the gap was
#: closed, because the closure is itself a fact the report needs: these cells
#: are populated by tasks authored for this experiment, not by tasks the
#: benchmark already had, and their oracles are stated in each ground_truth.
COVERAGE_GAPS = {
    "identifier_lookup": (
        "Absent from the original slice: the disclosure gate that makes it "
        "leak-free removes exactly the tasks whose answer is one known "
        "identifier. Closed by the symbol-footprint-* tasks in "
        "benchmarks/technical_debt/, whose answer key is derived by a "
        "word-boundary text match -- the same class of tool as the lexical "
        "condition's primitive, which is a bias to state alongside any claim "
        "drawn from this cell."
    ),
    "syntactic_pattern": (
        "Only five tasks in the original slice, none of them defined purely by "
        "code shape. Supplemented by the shape-audit-* tasks, whose answer key "
        "is derived by a parser -- the same class of tool as the structural "
        "condition's primitive, the mirror image of the bias above."
    ),
}


def _corpus() -> list[dict]:
    """The 63-task disclosure-gated slice plus the tasks authored to close its gaps.

    The additions carry their categories in the manifest ``nav_tasks.json``
    that generated them, so the label and the answer key come from one place
    and cannot drift apart.
    """
    tasks = json.loads((HERE / "slice_tasks.json").read_text())
    nav = HERE / "nav_tasks.json"
    if nav.is_file():
        tasks = tasks + json.loads(nav.read_text())
    return tasks


def build() -> dict:
    tasks = _corpus()
    for task in tasks:
        if "categories" in task and task["task_id"] not in _L:
            _L[task["task_id"]] = (
                task["categories"],
                "Authored to close a coverage gap; categories come from its spec.",
            )
    known = {t["task_id"] for t in tasks}
    missing = known - set(_L)
    extra = set(_L) - known
    if missing or extra:
        raise SystemExit(f"labels out of sync: missing={sorted(missing)} extra={sorted(extra)}")

    entries = {}
    for task in tasks:
        categories, rationale = _L[task["task_id"]]
        bad = set(categories) - set(CATEGORIES)
        if bad:
            raise SystemExit(f"{task['task_id']}: unknown categories {sorted(bad)}")
        # A multi-repo task that is not labelled cross_repo would silently
        # deflate the cross_repo cell it belongs in.
        if task["repo_count"] > 1 and "cross_repo" not in categories:
            raise SystemExit(f"{task['task_id']}: {task['repo_count']} repos, no cross_repo")
        entries[task["task_id"]] = {
            "suite": task["suite"],
            "repo_count": task["repo_count"],
            "required_file_count": task["required_file_count"],
            "categories": sorted(categories),
            "rationale": rationale,
        }

    counts: dict[str, int] = {name: 0 for name in CATEGORIES}
    for entry in entries.values():
        for category in entry["categories"]:
            counts[category] += 1
    return {
        "source": "slice_tasks.json",
        "labelling": (
            "Assigned from each task's own instruction.md and ground_truth.json. "
            "Labels slice results after the fact; they never select tasks into a "
            "condition, and every condition runs every task."
        ),
        "categories": CATEGORIES,
        "category_counts": counts,
        "coverage_gaps": COVERAGE_GAPS,
        "tasks": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    manifest = build()
    out = HERE / "task_categories.json"
    text = json.dumps(manifest, indent=2) + "\n"
    if args.check:
        if not out.is_file() or out.read_text() != text:
            print("task_categories.json is stale; rerun build_categories.py", file=sys.stderr)
            return 1
        return 0
    out.write_text(text)
    for name, count in sorted(manifest["category_counts"].items(), key=lambda kv: -kv[1]):
        print(f"{count:3d}  {name}")
    for name in manifest["coverage_gaps"]:
        print(f"  0  {name}  (declared coverage gap)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
