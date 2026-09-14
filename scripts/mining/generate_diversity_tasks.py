#!/usr/bin/env python3
"""Generate multi-repo benchmark tasks for ecosystem diversification.

Creates 24 tasks across 4 non-K8s ecosystems:
- rust-async-ecosystem (6 tasks)
- python-packaging-ecosystem (6 tasks)
- java-enterprise-ecosystem (6 tasks)
- cncf-observability-ecosystem (6 tasks)

Each task gets a complete directory with task.toml, instruction.md,
ground_truth.json, and checkpoint verifier scripts.

Usage:
    python scripts/mining/generate_diversity_tasks.py
    python scripts/mining/generate_diversity_tasks.py --dry-run
"""

from __future__ import annotations

import json
import os
import stat
import textwrap
from dataclasses import dataclass
from pathlib import Path

BENCHMARKS_DIR = Path(__file__).resolve().parent.parent.parent / "benchmarks"


@dataclass(frozen=True)
class RepoSpec:
    url: str
    rev: str
    path: str
    role: str


@dataclass(frozen=True)
class FileRef:
    path: str
    repo: str
    confidence: float
    source: str = "deterministic"


@dataclass(frozen=True)
class Checkpoint:
    name: str
    weight: float
    description: str
    grep_patterns: tuple[str, ...]  # patterns for verifier script


@dataclass(frozen=True)
class TaskDef:
    task_id: str
    suite: str
    task_type: str
    difficulty: str
    difficulty_stratum: str
    repo_set_id: str
    estimated_minutes: int
    description: str
    prompt: str
    repos: tuple[RepoSpec, ...]
    languages: tuple[str, ...]
    frameworks: tuple[str, ...]
    multi_repo_pattern: str
    checkpoints: tuple[Checkpoint, ...]
    required_files: tuple[FileRef, ...]
    sufficient_files: tuple[FileRef, ...]
    artifacts_required: tuple[str, ...]
    mcp_benefit: str
    mcp_rationale: str
    ground_truth_extra: dict  # additional fields for ground_truth.json


# ---------------------------------------------------------------------------
# Task definitions — 24 tasks across 4 ecosystems
# ---------------------------------------------------------------------------

TASKS: list[TaskDef] = [
    # ── Rust async ecosystem (6) ─────────────────────────────────────
    TaskDef(
        task_id="dep-graph-dual-tokio-hyper-001",
        suite="dependency_management",
        task_type="dependency_graph",
        difficulty="hard",
        difficulty_stratum="dual_repo",
        repo_set_id="rust-async-ecosystem",
        estimated_minutes=35,
        description="Trace tokio 1.0 runtime API changes affecting hyper's server implementation",
        prompt=textwrap.dedent("""\
            The tokio async runtime released v1.0, renaming feature flags from
            `rt-core`/`rt-threaded` to `rt`/`rt-multi-thread` and changing
            `Runtime::new()` behavior. The hyper HTTP library depends on tokio
            for its server implementation.

            Your task:
            1. Identify where hyper depends on tokio runtime features in Cargo.toml
            2. Find all files in hyper that create or reference tokio runtimes
            3. Trace the dependency chain from hyper's server module to tokio's
               runtime builder API
            4. Document which tokio API changes affect hyper and how

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/tokio-rs/tokio", "tokio-1.0.0", "tokio", "dependency"),
            RepoSpec("https://github.com/hyperium/hyper", "v0.14.0", "hyper", "primary"),
        ),
        languages=("rust",), frameworks=("tokio", "hyper"),
        multi_repo_pattern="propagate",
        checkpoints=(
            Checkpoint("identify_runtime_deps", 0.30, "Agent identifies tokio runtime dependencies in hyper",
                       ("runtime", "rt-core|rt-threaded|rt-multi-thread", "tokio")),
            Checkpoint("trace_server_chain", 0.40, "Agent traces hyper server → tokio runtime chain",
                       ("server", "tcp", "Runtime|Builder", "spawn")),
            Checkpoint("document_impact", 0.30, "Agent documents API changes and migration path",
                       ("feature.*flag|Cargo.toml", "migration|update|change")),
        ),
        required_files=(
            FileRef("hyper/Cargo.toml", "hyper", 0.95),
            FileRef("tokio/tokio/Cargo.toml", "tokio", 0.90),
        ),
        sufficient_files=(
            FileRef("hyper/src/server/tcp.rs", "hyper", 0.85),
            FileRef("tokio/tokio/src/runtime/builder.rs", "tokio", 0.80),
        ),
        artifacts_required=("answer",), mcp_benefit="medium",
        mcp_rationale="Cross-repo Cargo.toml feature search useful but repos are navigable",
        ground_truth_extra={"dependency_type": "cargo_feature_flags"},
    ),
    TaskDef(
        task_id="api-contract-dual-hyper-reqwest-001",
        suite="dependency_management",
        task_type="api_contract",
        difficulty="hard",
        difficulty_stratum="dual_repo",
        repo_set_id="rust-async-ecosystem",
        estimated_minutes=40,
        description="Trace hyper 1.0 Body type split affecting reqwest's HTTP client internals",
        prompt=textwrap.dedent("""\
            hyper 1.0 split the monolithic `Body` type into `hyper::body::Incoming`
            (for servers) and requires clients to use `http-body-util` adapters.
            The reqwest HTTP client depends heavily on hyper's Body type.

            Your task:
            1. Find where hyper defines Body-related types and traits
            2. Find where reqwest uses hyper's Body type in its client implementation
            3. Identify the contract mismatch between hyper 1.0's new body types
               and reqwest's existing usage
            4. Document the migration path for each affected reqwest module

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/hyperium/hyper", "v1.0.0", "hyper", "dependency"),
            RepoSpec("https://github.com/seanmonstar/reqwest", "v0.11.22", "reqwest", "primary"),
        ),
        languages=("rust",), frameworks=("hyper", "reqwest", "http-body"),
        multi_repo_pattern="propagate",
        checkpoints=(
            Checkpoint("identify_body_types", 0.35, "Agent identifies hyper Body type changes",
                       ("Body|Incoming", "http.body", "hyper")),
            Checkpoint("trace_reqwest_usage", 0.35, "Agent finds reqwest's hyper Body usage",
                       ("async_impl|client", "body", "connect")),
            Checkpoint("migration_analysis", 0.30, "Agent documents migration path",
                       ("http-body-util|adapter", "migration|compat")),
        ),
        required_files=(
            FileRef("hyper/src/body/mod.rs", "hyper", 0.90),
            FileRef("reqwest/src/async_impl/client.rs", "reqwest", 0.90),
        ),
        sufficient_files=(
            FileRef("reqwest/src/async_impl/body.rs", "reqwest", 0.85),
            FileRef("hyper/src/body/incoming.rs", "hyper", 0.80),
        ),
        artifacts_required=("answer",), mcp_benefit="medium",
        mcp_rationale="Type-level cross-repo search helps trace Body usage patterns",
        ground_truth_extra={"api_contract_type": "rust_trait_api"},
    ),
    TaskDef(
        task_id="dep-graph-tri-tokio-hyper-tonic-001",
        suite="dependency_management",
        task_type="dependency_graph",
        difficulty="expert",
        difficulty_stratum="tri_repo",
        repo_set_id="rust-async-ecosystem",
        estimated_minutes=60,
        description="Trace three-layer async runtime dependency: tokio → hyper → tonic gRPC stack",
        prompt=textwrap.dedent("""\
            The Rust gRPC stack uses tonic (gRPC) → hyper (HTTP/2) → tokio (async
            runtime). A tokio version bump can cascade through hyper into tonic.

            Your task:
            1. Map the full dependency chain: tonic → hyper → tokio with exact
               version constraints from each Cargo.toml
            2. Identify which tokio APIs hyper uses that tonic depends on transitively
            3. Find any version pinning conflicts across the three repos
            4. Document the upgrade coordination strategy

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/tokio-rs/tokio", "tokio-1.0.0", "tokio", "dependency"),
            RepoSpec("https://github.com/hyperium/hyper", "v0.14.0", "hyper", "intermediary"),
            RepoSpec("https://github.com/hyperium/tonic", "v0.6.0", "tonic", "primary"),
        ),
        languages=("rust",), frameworks=("tokio", "hyper", "tonic"),
        multi_repo_pattern="propagate",
        checkpoints=(
            Checkpoint("map_dep_chain", 0.30, "Agent maps full dependency chain with versions",
                       ("Cargo.toml", "tokio.*=|hyper.*=", "version")),
            Checkpoint("identify_transitive", 0.40, "Agent identifies transitive tokio APIs used by tonic",
                       ("runtime|spawn|JoinHandle", "transport|channel", "Server")),
            Checkpoint("upgrade_strategy", 0.30, "Agent documents coordination strategy",
                       ("upgrade|version|compat|pin", "breaking|semver")),
        ),
        required_files=(
            FileRef("tonic/tonic/Cargo.toml", "tonic", 0.95),
            FileRef("hyper/Cargo.toml", "hyper", 0.90),
            FileRef("tokio/tokio/Cargo.toml", "tokio", 0.90),
        ),
        sufficient_files=(
            FileRef("tonic/tonic/src/transport/server.rs", "tonic", 0.85),
            FileRef("hyper/src/server/tcp.rs", "hyper", 0.80),
        ),
        artifacts_required=("answer",), mcp_benefit="high",
        mcp_rationale="Three-repo Cargo.toml dependency tracing benefits from cross-repo search",
        ground_truth_extra={"dependency_depth": 3},
    ),
    TaskDef(
        task_id="refactor-dual-axum-tower-001",
        suite="technical_debt",
        task_type="refactor_orchestration",
        difficulty="hard",
        difficulty_stratum="dual_repo",
        repo_set_id="rust-async-ecosystem",
        estimated_minutes=40,
        description="Trace axum 0.7 Router generic changes requiring tower Service adaptation",
        prompt=textwrap.dedent("""\
            axum 0.7 changed Router to remove its generic body parameter and
            switched to a new body type constraint model. This breaks tower
            middleware that wraps axum routers.

            Your task:
            1. Find where axum defines the Router type and its generic parameters
            2. Find where tower defines the Service trait and middleware patterns
            3. Identify how the Router generic change affects tower middleware composition
            4. Document refactoring steps for tower middleware to work with axum 0.7

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/tokio-rs/axum", "axum-v0.7.0", "axum", "primary"),
            RepoSpec("https://github.com/tower-rs/tower", "tower-0.4.13", "tower", "dependency"),
        ),
        languages=("rust",), frameworks=("axum", "tower"),
        multi_repo_pattern="propagate",
        checkpoints=(
            Checkpoint("router_generics", 0.35, "Agent identifies Router generic changes",
                       ("Router", "generic|Body|BoxBody", "impl")),
            Checkpoint("tower_service", 0.35, "Agent traces tower Service trait interaction",
                       ("Service", "Layer|middleware", "tower")),
            Checkpoint("refactor_plan", 0.30, "Agent documents refactoring steps",
                       ("refactor|adapt|migration", "middleware")),
        ),
        required_files=(
            FileRef("axum/axum/src/routing/mod.rs", "axum", 0.90),
            FileRef("tower/tower/src/lib.rs", "tower", 0.85),
        ),
        sufficient_files=(
            FileRef("axum/axum/src/middleware/mod.rs", "axum", 0.80),
            FileRef("tower/tower-layer/src/lib.rs", "tower", 0.80),
        ),
        artifacts_required=("answer",), mcp_benefit="medium",
        mcp_rationale="Trait-level search across repos helps trace Service impl boundaries",
        ground_truth_extra={"refactor_type": "generic_parameter_change"},
    ),
    TaskDef(
        task_id="error-prov-dual-tokio-tower-001",
        suite="customer_escalation",
        task_type="error_provenance",
        difficulty="expert",
        difficulty_stratum="dual_repo",
        repo_set_id="rust-async-ecosystem",
        estimated_minutes=50,
        description="Trace tokio task panic propagation through tower service layers",
        prompt=textwrap.dedent("""\
            A production gRPC service is panicking inside a tokio::spawn'd task
            within a tower service layer. The panic message surfaces as a generic
            "task panicked" error, losing the original context.

            Your task:
            1. Find how tokio handles panics in spawned tasks (JoinError, panic hook)
            2. Find how tower Service layers propagate errors from inner services
            3. Trace the error path from a tokio task panic through tower layers
            4. Identify where error context is lost and document a fix

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/tokio-rs/tokio", "tokio-1.35.0", "tokio", "dependency"),
            RepoSpec("https://github.com/tower-rs/tower", "tower-0.4.13", "tower", "primary"),
        ),
        languages=("rust",), frameworks=("tokio", "tower"),
        multi_repo_pattern="investigate",
        checkpoints=(
            Checkpoint("tokio_panic_handling", 0.30, "Agent identifies tokio panic propagation",
                       ("JoinError|panic", "spawn|JoinHandle", "catch_unwind")),
            Checkpoint("tower_error_chain", 0.40, "Agent traces tower error propagation",
                       ("Service.*Error|BoxError", "Layer|ServiceBuilder", "poll_ready|call")),
            Checkpoint("context_loss", 0.30, "Agent identifies where error context is lost",
                       ("context|source|downcast", "into|from.*error")),
        ),
        required_files=(
            FileRef("tokio/tokio/src/task/join.rs", "tokio", 0.90),
            FileRef("tower/tower/src/util/mod.rs", "tower", 0.85),
        ),
        sufficient_files=(
            FileRef("tokio/tokio/src/runtime/task/error.rs", "tokio", 0.80),
            FileRef("tower/tower/src/buffer/service.rs", "tower", 0.80),
        ),
        artifacts_required=("answer",), mcp_benefit="high",
        mcp_rationale="Error type tracing across repos requires deep cross-crate search",
        ground_truth_extra={"error_type": "panic_propagation"},
    ),
    TaskDef(
        task_id="api-contract-dual-serde-csv-001",
        suite="dependency_management",
        task_type="api_contract",
        difficulty="hard",
        difficulty_stratum="dual_repo",
        repo_set_id="rust-async-ecosystem",
        estimated_minutes=30,
        description="Trace serde Deserialize trait changes affecting csv crate's record parsing",
        prompt=textwrap.dedent("""\
            The csv crate uses serde for deserializing CSV records into Rust structs.
            A change in how serde handles newtype struct deserialization affects
            csv's StringRecord parsing behavior.

            Your task:
            1. Find where serde defines Deserialize trait and newtype handling
            2. Find where csv implements serde deserialization for StringRecord
            3. Identify the API contract between serde's Deserializer trait and
               csv's implementation
            4. Document the behavioral difference and affected use cases

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/serde-rs/serde", "v1.0.193", "serde", "dependency"),
            RepoSpec("https://github.com/BurntSushi/rust-csv", "1.3.0", "csv", "primary"),
        ),
        languages=("rust",), frameworks=("serde", "csv"),
        multi_repo_pattern="investigate",
        checkpoints=(
            Checkpoint("serde_deserialize", 0.35, "Agent identifies serde Deserialize newtype handling",
                       ("Deserialize|Deserializer", "newtype|visit_newtype", "serde")),
            Checkpoint("csv_serde_impl", 0.35, "Agent finds csv's serde integration",
                       ("StringRecord|DeserializeRecordIn", "deserialize", "csv")),
            Checkpoint("contract_drift", 0.30, "Agent documents behavioral difference",
                       ("newtype|wrapping", "difference|mismatch|behavior")),
        ),
        required_files=(
            FileRef("serde/serde/src/de/mod.rs", "serde", 0.90),
            FileRef("csv/src/deserializer.rs", "csv", 0.90),
        ),
        sufficient_files=(
            FileRef("serde/serde_derive/src/de.rs", "serde", 0.80),
            FileRef("csv/src/reader.rs", "csv", 0.80),
        ),
        artifacts_required=("answer",), mcp_benefit="medium",
        mcp_rationale="Trait-level search helps find Deserialize impls across crate boundaries",
        ground_truth_extra={"api_contract_type": "rust_trait_api"},
    ),

    # ── Python packaging ecosystem (6) ───────────────────────────────
    TaskDef(
        task_id="api-contract-dual-pydantic-fastapi-001",
        suite="dependency_management",
        task_type="api_contract",
        difficulty="hard",
        difficulty_stratum="dual_repo",
        repo_set_id="python-packaging-ecosystem",
        estimated_minutes=35,
        description="Trace pydantic v2 BaseModel API rewrites breaking FastAPI model handling",
        prompt=textwrap.dedent("""\
            Pydantic v2 rewrote BaseModel internals: `.dict()` → `.model_dump()`,
            `.json()` → `.model_dump_json()`, `__fields__` → `model_fields`,
            `@validator` → `@field_validator`. FastAPI depends on pydantic for
            request/response model handling.

            Your task:
            1. Find where pydantic v2 defines the new BaseModel API
            2. Find where FastAPI uses pydantic BaseModel methods for serialization
            3. Identify all pydantic v1 API calls in FastAPI that break with v2
            4. Document the migration path for each affected FastAPI module

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/pydantic/pydantic", "v2.0.0", "pydantic", "dependency"),
            RepoSpec("https://github.com/tiangolo/fastapi", "0.99.1", "fastapi", "primary"),
        ),
        languages=("python",), frameworks=("pydantic", "fastapi"),
        multi_repo_pattern="propagate",
        checkpoints=(
            Checkpoint("pydantic_v2_api", 0.30, "Agent identifies pydantic v2 API changes",
                       ("model_dump|model_fields|field_validator", "BaseModel", "pydantic")),
            Checkpoint("fastapi_usage", 0.40, "Agent finds FastAPI's pydantic v1 API usage",
                       ("\\.dict\\(|__fields__|@validator", "encoders|routing|utils")),
            Checkpoint("migration_plan", 0.30, "Agent documents migration path",
                       ("migration|v2|model_dump", "deprecated|replacement")),
        ),
        required_files=(
            FileRef("pydantic/pydantic/main.py", "pydantic", 0.95),
            FileRef("fastapi/fastapi/encoders.py", "fastapi", 0.90),
        ),
        sufficient_files=(
            FileRef("fastapi/fastapi/routing.py", "fastapi", 0.85),
            FileRef("pydantic/pydantic/fields.py", "pydantic", 0.80),
        ),
        artifacts_required=("answer",), mcp_benefit="medium",
        mcp_rationale="Cross-repo search helps find all pydantic v1 API calls in FastAPI",
        ground_truth_extra={"breakage_type": "api_rename"},
    ),
    TaskDef(
        task_id="dep-graph-dual-cryptography-paramiko-001",
        suite="dependency_management",
        task_type="dependency_graph",
        difficulty="hard",
        difficulty_stratum="dual_repo",
        repo_set_id="python-packaging-ecosystem",
        estimated_minutes=35,
        description="Trace cryptography 41.0 OpenSSL changes breaking paramiko SSH transport",
        prompt=textwrap.dedent("""\
            The cryptography library v41.0 dropped support for OpenSSL 1.1.1 and
            removed the deprecated `default_backend()` parameter. Paramiko uses
            cryptography for SSH key exchange and transport encryption.

            Your task:
            1. Find where cryptography removed default_backend() and changed cipher APIs
            2. Find where paramiko calls cryptography APIs for key exchange and encryption
            3. Trace the impact through paramiko's transport and kex modules
            4. Document which paramiko functions need updating

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/pyca/cryptography", "41.0.0", "cryptography", "dependency"),
            RepoSpec("https://github.com/paramiko/paramiko", "3.2.0", "paramiko", "primary"),
        ),
        languages=("python",), frameworks=("cryptography", "paramiko"),
        multi_repo_pattern="propagate",
        checkpoints=(
            Checkpoint("crypto_api_changes", 0.30, "Agent identifies cryptography API removals",
                       ("default_backend|backend", "Cipher|algorithms", "deprecated|removed")),
            Checkpoint("paramiko_crypto_usage", 0.40, "Agent finds paramiko's cryptography usage",
                       ("transport|kex|packet", "Cipher|AESGCM|backend")),
            Checkpoint("impact_scope", 0.30, "Agent documents full impact scope",
                       ("kex_group|transport|ssh", "affected|update|fix")),
        ),
        required_files=(
            FileRef("cryptography/src/cryptography/hazmat/backends/__init__.py", "cryptography", 0.90),
            FileRef("paramiko/paramiko/transport.py", "paramiko", 0.90),
        ),
        sufficient_files=(
            FileRef("paramiko/paramiko/kex_group1.py", "paramiko", 0.85),
            FileRef("cryptography/src/cryptography/hazmat/primitives/ciphers/base.py", "cryptography", 0.80),
        ),
        artifacts_required=("answer",), mcp_benefit="medium",
        mcp_rationale="Tracing backend= parameter removal across crypto API surface",
        ground_truth_extra={"breakage_type": "deprecated_api_removal"},
    ),
    TaskDef(
        task_id="error-prov-dual-urllib3-requests-001",
        suite="customer_escalation",
        task_type="error_provenance",
        difficulty="hard",
        difficulty_stratum="dual_repo",
        repo_set_id="python-packaging-ecosystem",
        estimated_minutes=35,
        description="Trace urllib3 2.0 SSL error chain through requests adapter layer",
        prompt=textwrap.dedent("""\
            After upgrading urllib3 to 2.0, requests is raising different SSL
            exceptions than expected. The error messages have changed and some
            error types were restructured.

            Your task:
            1. Find how urllib3 2.0 restructured its exception hierarchy for SSL errors
            2. Find where requests catches and wraps urllib3 exceptions
            3. Trace an SSL connection error from urllib3 through requests' adapter layer
            4. Identify where error context is transformed or lost

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/urllib3/urllib3", "2.0.0", "urllib3", "dependency"),
            RepoSpec("https://github.com/psf/requests", "v2.28.2", "requests", "primary"),
        ),
        languages=("python",), frameworks=("urllib3", "requests"),
        multi_repo_pattern="investigate",
        checkpoints=(
            Checkpoint("urllib3_exceptions", 0.30, "Agent identifies urllib3 2.0 exception changes",
                       ("SSLError|MaxRetryError|exception", "urllib3")),
            Checkpoint("requests_wrapping", 0.40, "Agent traces requests exception wrapping",
                       ("adapters|ConnectionError|except.*urllib3", "requests")),
            Checkpoint("error_chain", 0.30, "Agent documents error transformation chain",
                       ("raise.*from|__cause__|context", "ssl|tls|certificate")),
        ),
        required_files=(
            FileRef("urllib3/src/urllib3/exceptions.py", "urllib3", 0.95),
            FileRef("requests/requests/adapters.py", "requests", 0.90),
        ),
        sufficient_files=(
            FileRef("requests/requests/exceptions.py", "requests", 0.85),
            FileRef("urllib3/src/urllib3/connectionpool.py", "urllib3", 0.80),
        ),
        artifacts_required=("answer",), mcp_benefit="medium",
        mcp_rationale="Exception hierarchy tracing across two repos benefits from cross-repo search",
        ground_truth_extra={"error_type": "ssl_exception_chain"},
    ),
    TaskDef(
        task_id="config-drift-dual-setuptools-pip-001",
        suite="platform_engineering",
        task_type="config_drift",
        difficulty="expert",
        difficulty_stratum="dual_repo",
        repo_set_id="python-packaging-ecosystem",
        estimated_minutes=50,
        description="Trace setuptools pkg_resources deprecation affecting pip's metadata resolution",
        prompt=textwrap.dedent("""\
            setuptools 67.0 deprecated pkg_resources in favor of importlib.metadata.
            pip vendors its own copy of pkg_resources and uses it for metadata
            resolution, entry point discovery, and namespace package handling.

            Your task:
            1. Find where setuptools deprecated/changed pkg_resources APIs
            2. Find where pip vendors and uses pkg_resources internally
            3. Identify which pip features depend on deprecated pkg_resources behavior
            4. Document the drift between setuptools' intended migration path and
               pip's vendored copy

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/pypa/setuptools", "v67.0.0", "setuptools", "dependency"),
            RepoSpec("https://github.com/pypa/pip", "23.0", "pip", "primary"),
        ),
        languages=("python",), frameworks=("setuptools", "pip"),
        multi_repo_pattern="investigate",
        checkpoints=(
            Checkpoint("setuptools_deprecation", 0.25, "Agent identifies setuptools deprecation",
                       ("pkg_resources|importlib.metadata", "deprecated|DeprecationWarning")),
            Checkpoint("pip_vendored_usage", 0.40, "Agent finds pip's vendored pkg_resources usage",
                       ("_vendor.*pkg_resources|pkg_resources", "metadata|entry_point")),
            Checkpoint("drift_analysis", 0.35, "Agent documents configuration drift",
                       ("drift|vendored|diverge", "importlib.metadata|replacement")),
        ),
        required_files=(
            FileRef("setuptools/pkg_resources/__init__.py", "setuptools", 0.90),
            FileRef("pip/src/pip/_vendor/pkg_resources/__init__.py", "pip", 0.90),
        ),
        sufficient_files=(
            FileRef("pip/src/pip/_internal/metadata/pkg_resources.py", "pip", 0.85),
            FileRef("setuptools/setuptools/_entry_points.py", "setuptools", 0.80),
        ),
        artifacts_required=("answer",), mcp_benefit="high",
        mcp_rationale="Large vendored copy requires cross-repo diff to identify drift points",
        ground_truth_extra={"drift_type": "vendored_copy_divergence"},
    ),
    TaskDef(
        task_id="dep-graph-tri-boto3-urllib3-requests-001",
        suite="dependency_management",
        task_type="dependency_graph",
        difficulty="expert",
        difficulty_stratum="tri_repo",
        repo_set_id="python-packaging-ecosystem",
        estimated_minutes=55,
        description="Trace urllib3 2.0 upgrade cascade through requests and botocore/boto3",
        prompt=textwrap.dedent("""\
            urllib3 2.0 was a major release that affected the entire Python HTTP
            stack. Both requests and botocore depend on urllib3 with conflicting
            version constraints. botocore vendors its own urllib3 copy.

            Your task:
            1. Map the dependency chain: boto3/botocore → urllib3, requests → urllib3
            2. Find version pin conflicts between botocore and requests for urllib3
            3. Identify where botocore's vendored urllib3 diverges from upstream 2.0
            4. Document the upgrade coordination challenge across all three repos

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/urllib3/urllib3", "2.0.0", "urllib3", "dependency"),
            RepoSpec("https://github.com/psf/requests", "v2.31.0", "requests", "intermediary"),
            RepoSpec("https://github.com/boto/botocore", "1.31.0", "botocore", "primary"),
        ),
        languages=("python",), frameworks=("urllib3", "requests", "botocore"),
        multi_repo_pattern="propagate",
        checkpoints=(
            Checkpoint("dep_chain_map", 0.30, "Agent maps three-repo dependency chain",
                       ("setup.cfg|setup.py|pyproject.toml", "urllib3.*<|urllib3.*>", "version")),
            Checkpoint("version_conflicts", 0.35, "Agent identifies version pin conflicts",
                       ("urllib3.*1.27|urllib3.*2.0|vendor", "pin|constraint|conflict")),
            Checkpoint("upgrade_strategy", 0.35, "Agent documents upgrade coordination",
                       ("upgrade|migrate|de-vendor", "coordination|all.*three|cascade")),
        ),
        required_files=(
            FileRef("urllib3/CHANGES.rst", "urllib3", 0.85),
            FileRef("requests/setup.cfg", "requests", 0.90),
            FileRef("botocore/setup.py", "botocore", 0.90),
        ),
        sufficient_files=(
            FileRef("botocore/botocore/httpsession.py", "botocore", 0.85),
            FileRef("requests/requests/adapters.py", "requests", 0.80),
        ),
        artifacts_required=("answer",), mcp_benefit="high",
        mcp_rationale="Three-repo version constraint analysis requires extensive cross-repo search",
        ground_truth_extra={"dependency_depth": 3, "vendored": True},
    ),
    TaskDef(
        task_id="api-contract-dual-sqlalchemy-alembic-001",
        suite="dependency_management",
        task_type="api_contract",
        difficulty="hard",
        difficulty_stratum="dual_repo",
        repo_set_id="python-packaging-ecosystem",
        estimated_minutes=35,
        description="Trace SQLAlchemy 2.0 session API changes affecting Alembic migration engine",
        prompt=textwrap.dedent("""\
            SQLAlchemy 2.0 changed the Session and Connection APIs: `session.execute()`
            now requires explicit `text()` wrapping for raw SQL, and the engine/connection
            model was restructured. Alembic uses SQLAlchemy internally for migration execution.

            Your task:
            1. Find where SQLAlchemy 2.0 changed Session and Connection execute APIs
            2. Find where Alembic uses SQLAlchemy Session/Connection for migrations
            3. Identify breaking contract points between the two
            4. Document the impact on Alembic's migration runner

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/sqlalchemy/sqlalchemy", "rel_2_0_0", "sqlalchemy", "dependency"),
            RepoSpec("https://github.com/sqlalchemy/alembic", "rel_1_12_0", "alembic", "primary"),
        ),
        languages=("python",), frameworks=("sqlalchemy", "alembic"),
        multi_repo_pattern="propagate",
        checkpoints=(
            Checkpoint("sqla_api_changes", 0.30, "Agent identifies SQLAlchemy 2.0 execute API changes",
                       ("execute|text\\(|Connection", "Session|Engine")),
            Checkpoint("alembic_usage", 0.40, "Agent finds Alembic's SQLAlchemy API usage",
                       ("MigrationContext|operations|execute", "connection|engine")),
            Checkpoint("contract_drift", 0.30, "Agent documents breaking contract points",
                       ("text\\(|2.0|legacy|deprecat", "migration|breaking")),
        ),
        required_files=(
            FileRef("sqlalchemy/lib/sqlalchemy/engine/base.py", "sqlalchemy", 0.90),
            FileRef("alembic/alembic/runtime/migration.py", "alembic", 0.90),
        ),
        sufficient_files=(
            FileRef("alembic/alembic/operations/ops.py", "alembic", 0.85),
            FileRef("sqlalchemy/lib/sqlalchemy/orm/session.py", "sqlalchemy", 0.80),
        ),
        artifacts_required=("answer",), mcp_benefit="medium",
        mcp_rationale="SQLAlchemy API tracing through Alembic's migration engine",
        ground_truth_extra={"api_contract_type": "python_method_api"},
    ),

    # ── Java enterprise ecosystem (6) ─────────��──────────────────────
    TaskDef(
        task_id="api-contract-dual-jackson-spring-001",
        suite="dependency_management",
        task_type="api_contract",
        difficulty="hard",
        difficulty_stratum="dual_repo",
        repo_set_id="java-enterprise-ecosystem",
        estimated_minutes=35,
        description="Trace Jackson 2.15 ObjectMapper default changes breaking Spring Boot serialization",
        prompt=textwrap.dedent("""\
            Jackson 2.15 changed ObjectMapper default settings: FAIL_ON_UNKNOWN_PROPERTIES
            is now disabled by default, and date serialization format changed.
            Spring Boot auto-configures Jackson as its default JSON serializer.

            Your task:
            1. Find where Jackson defines ObjectMapper defaults and feature flags
            2. Find where Spring Boot auto-configures Jackson ObjectMapper
            3. Identify default behavior changes that affect Spring REST endpoints
            4. Document the migration path for Spring Boot applications

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/FasterXML/jackson-databind", "jackson-databind-2.15.0", "jackson-databind", "dependency"),
            RepoSpec("https://github.com/spring-projects/spring-boot", "v3.1.0", "spring-boot", "primary"),
        ),
        languages=("java",), frameworks=("jackson", "spring-boot"),
        multi_repo_pattern="propagate",
        checkpoints=(
            Checkpoint("jackson_defaults", 0.30, "Agent identifies Jackson default changes",
                       ("ObjectMapper|MapperFeature|DeserializationFeature", "FAIL_ON_UNKNOWN|default")),
            Checkpoint("spring_autoconfig", 0.40, "Agent finds Spring Boot's Jackson auto-configuration",
                       ("JacksonAutoConfiguration|ObjectMapperBuilder", "customize|configure")),
            Checkpoint("behavior_drift", 0.30, "Agent documents behavioral changes",
                       ("default.*changed|behavior|breaking", "serializ|date|unknown.propert")),
        ),
        required_files=(
            FileRef("jackson-databind/src/main/java/com/fasterxml/jackson/databind/ObjectMapper.java", "jackson-databind", 0.95),
            FileRef("spring-boot/spring-boot-project/spring-boot-autoconfigure/src/main/java/org/springframework/boot/autoconfigure/jackson/JacksonAutoConfiguration.java", "spring-boot", 0.90),
        ),
        sufficient_files=(
            FileRef("jackson-databind/src/main/java/com/fasterxml/jackson/databind/cfg/MapperConfig.java", "jackson-databind", 0.80),
        ),
        artifacts_required=("answer",), mcp_benefit="high",
        mcp_rationale="Large repos; cross-repo search helps find Jackson config in Spring autoconfigure",
        ground_truth_extra={"api_contract_type": "java_default_behavior"},
    ),
    TaskDef(
        task_id="dep-graph-dual-spring-hibernate-001",
        suite="dependency_management",
        task_type="dependency_graph",
        difficulty="hard",
        difficulty_stratum="dual_repo",
        repo_set_id="java-enterprise-ecosystem",
        estimated_minutes=40,
        description="Trace Hibernate 6 SessionFactory changes affecting Spring Data JPA auto-config",
        prompt=textwrap.dedent("""\
            Hibernate 6 restructured the SessionFactory and removed several
            deprecated configuration properties. Spring Data JPA relies on
            Hibernate as its default JPA provider.

            Your task:
            1. Find where Hibernate 6 changed SessionFactory configuration API
            2. Find where Spring Data JPA configures the Hibernate SessionFactory
            3. Map the dependency chain from Spring Boot auto-config to Hibernate
            4. Document deprecated configuration properties and their replacements

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/hibernate/hibernate-orm", "6.2.0", "hibernate-orm", "dependency"),
            RepoSpec("https://github.com/spring-projects/spring-boot", "v3.1.0", "spring-boot", "primary"),
        ),
        languages=("java",), frameworks=("hibernate", "spring-boot", "spring-data-jpa"),
        multi_repo_pattern="propagate",
        checkpoints=(
            Checkpoint("hibernate_changes", 0.30, "Agent identifies Hibernate 6 SessionFactory changes",
                       ("SessionFactory|Configuration|StandardServiceRegistry", "deprecated|removed")),
            Checkpoint("spring_jpa_config", 0.40, "Agent finds Spring JPA auto-configuration",
                       ("HibernateJpaAutoConfiguration|JpaProperties|EntityManagerFactory", "configure|vendor")),
            Checkpoint("migration_path", 0.30, "Agent documents property migrations",
                       ("hibernate\\..*=|spring\\.jpa", "property|replacement|migration")),
        ),
        required_files=(
            FileRef("hibernate-orm/hibernate-core/src/main/java/org/hibernate/cfg/Configuration.java", "hibernate-orm", 0.90),
            FileRef("spring-boot/spring-boot-project/spring-boot-autoconfigure/src/main/java/org/springframework/boot/autoconfigure/orm/jpa/HibernateJpaAutoConfiguration.java", "spring-boot", 0.90),
        ),
        sufficient_files=(
            FileRef("hibernate-orm/hibernate-core/src/main/java/org/hibernate/cfg/AvailableSettings.java", "hibernate-orm", 0.85),
        ),
        artifacts_required=("answer",), mcp_benefit="high",
        mcp_rationale="Spring Boot's massive codebase benefits from targeted cross-repo search",
        ground_truth_extra={"dependency_type": "jpa_provider"},
    ),
    TaskDef(
        task_id="config-drift-tri-javax-jakarta-spring-001",
        suite="platform_engineering",
        task_type="config_drift",
        difficulty="expert",
        difficulty_stratum="tri_repo",
        repo_set_id="java-enterprise-ecosystem",
        estimated_minutes=60,
        description="Trace javax → jakarta namespace migration across Spring Boot, Hibernate, and Tomcat",
        prompt=textwrap.dedent("""\
            The Jakarta EE migration changed the namespace from javax.* to jakarta.*
            across the entire Java ecosystem. Spring Boot 3.0 adopted jakarta, requiring
            all dependencies to also migrate.

            Your task:
            1. Find where Spring Boot 3.0 switched from javax to jakarta imports
            2. Find where Hibernate 6 made the jakarta migration
            3. Find where Tomcat 10 adopted the jakarta namespace
            4. Identify any configuration drift where one component uses javax
               while another expects jakarta, causing ClassNotFoundException

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/spring-projects/spring-boot", "v3.0.0", "spring-boot", "primary"),
            RepoSpec("https://github.com/hibernate/hibernate-orm", "6.0.0.Final", "hibernate-orm", "dependency"),
            RepoSpec("https://github.com/apache/tomcat", "10.1.0", "tomcat", "dependency"),
        ),
        languages=("java",), frameworks=("spring-boot", "hibernate", "tomcat"),
        multi_repo_pattern="enforce",
        checkpoints=(
            Checkpoint("spring_jakarta", 0.25, "Agent identifies Spring Boot jakarta migration",
                       ("jakarta\\.|javax\\.", "import|namespace|migration")),
            Checkpoint("hibernate_jakarta", 0.25, "Agent identifies Hibernate jakarta migration",
                       ("jakarta\\.persistence|javax\\.persistence", "Entity|Session")),
            Checkpoint("tomcat_jakarta", 0.25, "Agent identifies Tomcat jakarta adoption",
                       ("jakarta\\.servlet|javax\\.servlet", "Servlet|Filter|Request")),
            Checkpoint("drift_analysis", 0.25, "Agent identifies cross-component namespace drift",
                       ("ClassNotFoundException|NoClassDefFound|drift|conflict", "javax.*jakarta|mixed")),
        ),
        required_files=(
            FileRef("spring-boot/spring-boot-project/spring-boot/src/main/java/org/springframework/boot/SpringApplication.java", "spring-boot", 0.85),
            FileRef("hibernate-orm/hibernate-core/src/main/java/org/hibernate/cfg/Configuration.java", "hibernate-orm", 0.85),
            FileRef("tomcat/java/org/apache/catalina/startup/Tomcat.java", "tomcat", 0.85),
        ),
        sufficient_files=(),
        artifacts_required=("answer",), mcp_benefit="high",
        mcp_rationale="Three-repo namespace migration analysis benefits greatly from cross-repo search",
        ground_truth_extra={"drift_type": "namespace_migration"},
    ),
    TaskDef(
        task_id="error-prov-dual-netty-spring-001",
        suite="customer_escalation",
        task_type="error_provenance",
        difficulty="expert",
        difficulty_stratum="dual_repo",
        repo_set_id="java-enterprise-ecosystem",
        estimated_minutes=50,
        description="Trace Netty channel pipeline error propagation through Spring WebFlux reactive stack",
        prompt=textwrap.dedent("""\
            A Spring WebFlux application is experiencing intermittent connection
            reset errors. The errors originate in Netty's channel pipeline but
            surface as generic 500 errors in Spring WebFlux.

            Your task:
            1. Find how Netty handles channel pipeline exceptions (exceptionCaught)
            2. Find how Spring WebFlux wraps Netty's reactor-netty in its server
            3. Trace an exception from Netty's channel handler through reactor-netty
               into Spring WebFlux's error handling
            4. Identify where error context is lost in the translation

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/netty/netty", "netty-4.1.100.Final", "netty", "dependency"),
            RepoSpec("https://github.com/spring-projects/spring-framework", "v6.0.11", "spring-framework", "primary"),
        ),
        languages=("java",), frameworks=("netty", "spring-webflux", "reactor"),
        multi_repo_pattern="investigate",
        checkpoints=(
            Checkpoint("netty_error_handling", 0.30, "Agent identifies Netty exception propagation",
                       ("exceptionCaught|ChannelHandler|pipeline", "fireException|ctx")),
            Checkpoint("spring_webflux_bridge", 0.40, "Agent traces Spring WebFlux → Netty bridge",
                       ("ReactorHttpServer|reactor.netty|HttpHandler", "WebFlux|ServerWebExchange")),
            Checkpoint("error_context_loss", 0.30, "Agent identifies where context is lost",
                       ("ResponseStatusException|500|ServerError", "cause|context|suppress")),
        ),
        required_files=(
            FileRef("netty/handler/src/main/java/io/netty/handler/codec/http/HttpServerCodec.java", "netty", 0.85),
            FileRef("spring-framework/spring-web/src/main/java/org/springframework/http/server/reactive/ReactorHttpHandlerAdapter.java", "spring-framework", 0.90),
        ),
        sufficient_files=(
            FileRef("netty/transport/src/main/java/io/netty/channel/DefaultChannelPipeline.java", "netty", 0.80),
        ),
        artifacts_required=("answer",), mcp_benefit="high",
        mcp_rationale="Error chain tracing across Netty, reactor-netty, and Spring requires deep search",
        ground_truth_extra={"error_type": "channel_pipeline_exception"},
    ),
    TaskDef(
        task_id="dep-graph-dual-log4j-spring-001",
        suite="dependency_management",
        task_type="dependency_graph",
        difficulty="hard",
        difficulty_stratum="dual_repo",
        repo_set_id="java-enterprise-ecosystem",
        estimated_minutes=30,
        description="Trace Log4j2 API bridge changes affecting Spring Boot logging auto-configuration",
        prompt=textwrap.dedent("""\
            Log4j2 restructured its bridge modules (log4j-to-slf4j, log4j-api) in
            a major release, changing how Spring Boot's logging auto-configuration
            detects and configures logging providers.

            Your task:
            1. Find where Log4j2 defines its API bridge modules
            2. Find where Spring Boot auto-configures logging providers
            3. Identify how the bridge module restructuring affects detection logic
            4. Document the dependency chain from Spring Boot → SLF4J → Log4j2

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/apache/logging-log4j2", "rel/2.20.0", "log4j2", "dependency"),
            RepoSpec("https://github.com/spring-projects/spring-boot", "v3.1.0", "spring-boot", "primary"),
        ),
        languages=("java",), frameworks=("log4j2", "spring-boot", "slf4j"),
        multi_repo_pattern="investigate",
        checkpoints=(
            Checkpoint("log4j_bridges", 0.30, "Agent identifies Log4j2 bridge modules",
                       ("log4j-to-slf4j|log4j-api|LogManager", "bridge|provider")),
            Checkpoint("spring_logging_config", 0.40, "Agent finds Spring Boot logging auto-config",
                       ("LoggingSystem|LogbackLoggingSystem|Log4J2", "auto-config|detect")),
            Checkpoint("dep_chain", 0.30, "Agent documents dependency chain",
                       ("dependency|classpath|bridge", "slf4j|log4j|logback")),
        ),
        required_files=(
            FileRef("log4j2/log4j-api/src/main/java/org/apache/logging/log4j/LogManager.java", "log4j2", 0.90),
            FileRef("spring-boot/spring-boot-project/spring-boot/src/main/java/org/springframework/boot/logging/LoggingSystem.java", "spring-boot", 0.90),
        ),
        sufficient_files=(
            FileRef("spring-boot/spring-boot-project/spring-boot/src/main/java/org/springframework/boot/logging/log4j2/Log4J2LoggingSystem.java", "spring-boot", 0.85),
        ),
        artifacts_required=("answer",), mcp_benefit="medium",
        mcp_rationale="Bridge module detection logic in Spring Boot can be found via class name search",
        ground_truth_extra={"dependency_type": "logging_bridge"},
    ),
    TaskDef(
        task_id="dep-graph-dual-junit-mockito-001",
        suite="dependency_management",
        task_type="dependency_graph",
        difficulty="hard",
        difficulty_stratum="dual_repo",
        repo_set_id="java-enterprise-ecosystem",
        estimated_minutes=30,
        description="Trace JUnit 5 extension model changes affecting Mockito's test integration",
        prompt=textwrap.dedent("""\
            JUnit 5 changed its extension model from JUnit 4's @RunWith to the
            @ExtendWith annotation system. Mockito needed to create a new
            MockitoExtension to replace the JUnit 4 MockitoJUnitRunner.

            Your task:
            1. Find where JUnit 5 defines the Extension interface and lifecycle callbacks
            2. Find where Mockito implements JUnit 5 extension support
            3. Identify API contract differences between JUnit 4 Runner and JUnit 5 Extension
            4. Document the migration from MockitoJUnitRunner to MockitoExtension

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/junit-team/junit5", "r5.10.0", "junit5", "dependency"),
            RepoSpec("https://github.com/mockito/mockito", "v5.5.0", "mockito", "primary"),
        ),
        languages=("java",), frameworks=("junit5", "mockito"),
        multi_repo_pattern="propagate",
        checkpoints=(
            Checkpoint("junit5_extension", 0.30, "Agent identifies JUnit 5 extension API",
                       ("Extension|ExtendWith|BeforeEachCallback|AfterEachCallback", "jupiter")),
            Checkpoint("mockito_integration", 0.40, "Agent finds Mockito's JUnit 5 integration",
                       ("MockitoExtension|MockitoSession|ExtendWith", "subpackage|junit.jupiter")),
            Checkpoint("migration_doc", 0.30, "Agent documents Runner → Extension migration",
                       ("RunWith|Runner|Extension|migration", "MockitoJUnitRunner|MockitoExtension")),
        ),
        required_files=(
            FileRef("junit5/junit-jupiter-api/src/main/java/org/junit/jupiter/api/extension/Extension.java", "junit5", 0.90),
            FileRef("mockito/subprojects/junit-jupiter/src/main/java/org/mockito/junit/jupiter/MockitoExtension.java", "mockito", 0.95),
        ),
        sufficient_files=(
            FileRef("junit5/junit-jupiter-api/src/main/java/org/junit/jupiter/api/extension/BeforeEachCallback.java", "junit5", 0.80),
        ),
        artifacts_required=("answer",), mcp_benefit="medium",
        mcp_rationale="Extension interface search across repos helps trace callback implementations",
        ground_truth_extra={"api_contract_type": "java_interface_api"},
    ),

    # ── CNCF observability / non-K8s (6) ─────────────────────────────
    TaskDef(
        task_id="dep-graph-dual-prometheus-thanos-001",
        suite="dependency_management",
        task_type="dependency_graph",
        difficulty="hard",
        difficulty_stratum="dual_repo",
        repo_set_id="cncf-observability-ecosystem",
        estimated_minutes=35,
        description="Trace Prometheus TSDB storage API changes affecting Thanos sidecar integration",
        prompt=textwrap.dedent("""\
            Prometheus changed its TSDB storage engine API for block compaction
            and chunk encoding. Thanos extends Prometheus as a sidecar for
            long-term storage and depends on these internal APIs.

            Your task:
            1. Find where Prometheus defines TSDB storage and compaction APIs
            2. Find where Thanos implements its Prometheus sidecar integration
            3. Identify TSDB API changes that break Thanos' block uploading
            4. Document the dependency chain and affected Thanos components

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/prometheus/prometheus", "v2.47.0", "prometheus", "dependency"),
            RepoSpec("https://github.com/thanos-io/thanos", "v0.32.0", "thanos", "primary"),
        ),
        languages=("go",), frameworks=("prometheus", "thanos"),
        multi_repo_pattern="investigate",
        checkpoints=(
            Checkpoint("prometheus_tsdb", 0.30, "Agent identifies Prometheus TSDB API",
                       ("tsdb|storage|Compactor|Block", "prometheus")),
            Checkpoint("thanos_sidecar", 0.40, "Agent finds Thanos sidecar integration",
                       ("sidecar|shipper|block", "upload|prometheus|tsdb")),
            Checkpoint("api_impact", 0.30, "Agent documents TSDB API changes affecting Thanos",
                       ("compaction|chunk|encode|breaking", "migration|adapter")),
        ),
        required_files=(
            FileRef("prometheus/tsdb/db.go", "prometheus", 0.90),
            FileRef("thanos/pkg/shipper/shipper.go", "thanos", 0.90),
        ),
        sufficient_files=(
            FileRef("prometheus/tsdb/compact.go", "prometheus", 0.85),
            FileRef("thanos/pkg/store/prometheus.go", "thanos", 0.80),
        ),
        artifacts_required=("answer",), mcp_benefit="high",
        mcp_rationale="Internal TSDB API tracing across large Go repos benefits from code search",
        ground_truth_extra={"dependency_type": "storage_api"},
    ),
    TaskDef(
        task_id="api-contract-dual-envoy-istio-001",
        suite="dependency_management",
        task_type="api_contract",
        difficulty="expert",
        difficulty_stratum="dual_repo",
        repo_set_id="cncf-observability-ecosystem",
        estimated_minutes=55,
        description="Trace Envoy xDS API v3 changes affecting Istio's control plane configuration",
        prompt=textwrap.dedent("""\
            Envoy's xDS (discovery service) API evolved from v2 to v3, changing
            resource type names and proto message structures. Istio's control
            plane (istiod) generates xDS configuration for Envoy sidecars.

            Your task:
            1. Find where Envoy defines xDS v3 API proto messages
            2. Find where Istio generates xDS configuration for Envoy
            3. Identify v2 → v3 API changes in cluster, listener, and route configs
            4. Document the contract points between Istio's xDS generator and
               Envoy's xDS consumer

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/envoyproxy/envoy", "v1.28.0", "envoy", "dependency"),
            RepoSpec("https://github.com/istio/istio", "1.20.0", "istio", "primary"),
        ),
        languages=("go", "cpp"), frameworks=("envoy", "istio"),
        multi_repo_pattern="enforce",
        checkpoints=(
            Checkpoint("envoy_xds_api", 0.30, "Agent identifies Envoy xDS v3 proto definitions",
                       ("envoy.config|discovery|xds", "Cluster|Listener|Route|v3")),
            Checkpoint("istio_xds_gen", 0.40, "Agent finds Istio's xDS configuration generation",
                       ("xds|ads|pilot|discovery", "generate|push|config")),
            Checkpoint("v2_v3_drift", 0.30, "Agent documents v2→v3 contract changes",
                       ("v2|v3|deprecated|migration", "type_url|resource_name")),
        ),
        required_files=(
            FileRef("envoy/api/envoy/config/cluster/v3/cluster.proto", "envoy", 0.90),
            FileRef("istio/pilot/pkg/xds/ads.go", "istio", 0.90),
        ),
        sufficient_files=(
            FileRef("istio/pilot/pkg/model/push_context.go", "istio", 0.85),
            FileRef("envoy/api/envoy/config/listener/v3/listener.proto", "envoy", 0.80),
        ),
        artifacts_required=("answer",), mcp_benefit="high",
        mcp_rationale="Proto API tracing across envoy and istio's large codebases needs code search",
        ground_truth_extra={"api_contract_type": "protobuf_api"},
    ),
    TaskDef(
        task_id="config-drift-dual-jaeger-otel-001",
        suite="platform_engineering",
        task_type="config_drift",
        difficulty="hard",
        difficulty_stratum="dual_repo",
        repo_set_id="cncf-observability-ecosystem",
        estimated_minutes=35,
        description="Trace Jaeger collector configuration drift with OpenTelemetry Collector migration",
        prompt=textwrap.dedent("""\
            Jaeger is migrating its backend to use the OpenTelemetry Collector as
            its core processing engine. During the migration, configuration formats
            between Jaeger's legacy config and OTel Collector config can drift.

            Your task:
            1. Find Jaeger's legacy collector configuration format and flags
            2. Find OpenTelemetry Collector's configuration schema for tracing
            3. Identify configuration parameters that exist in one but not the other
            4. Document the configuration migration path from Jaeger to OTel format

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/jaegertracing/jaeger", "v1.51.0", "jaeger", "primary"),
            RepoSpec("https://github.com/open-telemetry/opentelemetry-collector", "v0.89.0", "otel-collector", "dependency"),
        ),
        languages=("go",), frameworks=("jaeger", "opentelemetry"),
        multi_repo_pattern="investigate",
        checkpoints=(
            Checkpoint("jaeger_config", 0.30, "Agent identifies Jaeger collector configuration",
                       ("collector|config|flag|viper", "jaeger")),
            Checkpoint("otel_config", 0.35, "Agent finds OTel Collector config schema",
                       ("config|factory|receiver|exporter", "otel|opentelemetry")),
            Checkpoint("drift_points", 0.35, "Agent documents configuration drift",
                       ("drift|mismatch|missing|migration", "config|parameter|format")),
        ),
        required_files=(
            FileRef("jaeger/cmd/collector/app/flags.go", "jaeger", 0.90),
            FileRef("otel-collector/service/service.go", "otel-collector", 0.85),
        ),
        sufficient_files=(
            FileRef("jaeger/cmd/collector/app/handler/grpc_handler.go", "jaeger", 0.80),
            FileRef("otel-collector/receiver/otlpreceiver/factory.go", "otel-collector", 0.80),
        ),
        artifacts_required=("answer",), mcp_benefit="medium",
        mcp_rationale="Configuration format comparison across repos benefits from targeted search",
        ground_truth_extra={"drift_type": "config_format_migration"},
    ),
    TaskDef(
        task_id="dep-graph-tri-prometheus-alertmanager-grafana-001",
        suite="dependency_management",
        task_type="dependency_graph",
        difficulty="expert",
        difficulty_stratum="tri_repo",
        repo_set_id="cncf-observability-ecosystem",
        estimated_minutes=55,
        description="Trace Prometheus alert rule API changes cascading through Alertmanager and Grafana",
        prompt=textwrap.dedent("""\
            Prometheus changed its alerting rule evaluation and notification API.
            Alertmanager receives alerts from Prometheus, and Grafana reads alert
            state from both. Changes in Prometheus' alert format cascade downstream.

            Your task:
            1. Find where Prometheus defines alert rule evaluation and notification format
            2. Find where Alertmanager receives and processes Prometheus alerts
            3. Find where Grafana queries Prometheus and Alertmanager for alert state
            4. Document the three-way API contract and cascade points

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/prometheus/prometheus", "v2.47.0", "prometheus", "dependency"),
            RepoSpec("https://github.com/prometheus/alertmanager", "v0.26.0", "alertmanager", "intermediary"),
            RepoSpec("https://github.com/grafana/grafana", "v10.2.0", "grafana", "primary"),
        ),
        languages=("go",), frameworks=("prometheus", "alertmanager", "grafana"),
        multi_repo_pattern="propagate",
        checkpoints=(
            Checkpoint("prometheus_alert_api", 0.25, "Agent identifies Prometheus alert notification format",
                       ("rules|alert|notifier|api", "Alert|State|Labels")),
            Checkpoint("alertmanager_receiver", 0.25, "Agent finds Alertmanager alert processing",
                       ("api.*v2|receiver|dispatch", "alert|silence|notify")),
            Checkpoint("grafana_query", 0.25, "Agent finds Grafana alert state queries",
                       ("datasource.*prometheus|alerting|ruler", "query|alert|state")),
            Checkpoint("cascade_analysis", 0.25, "Agent documents three-way cascade",
                       ("cascade|propagat|format.*change", "all.*three|alertmanager.*grafana")),
        ),
        required_files=(
            FileRef("prometheus/rules/alerting.go", "prometheus", 0.90),
            FileRef("alertmanager/api/v2/restapi/operations/alert/post_alerts.go", "alertmanager", 0.85),
            FileRef("grafana/pkg/services/ngalert/api/api_prometheus.go", "grafana", 0.85),
        ),
        sufficient_files=(
            FileRef("prometheus/notifier/notifier.go", "prometheus", 0.80),
        ),
        artifacts_required=("answer",), mcp_benefit="high",
        mcp_rationale="Three-repo alert API tracing across large Go codebases needs extensive search",
        ground_truth_extra={"dependency_depth": 3},
    ),
    TaskDef(
        task_id="incident-inv-dual-cortex-thanos-001",
        suite="incident_response",
        task_type="incident_investigation",
        difficulty="hard",
        difficulty_stratum="dual_repo",
        repo_set_id="cncf-observability-ecosystem",
        estimated_minutes=40,
        description="Investigate Cortex ingester ring hash collision causing query failures via Thanos StoreAPI",
        prompt=textwrap.dedent("""\
            A metrics platform using Cortex for write path and Thanos for read path
            is experiencing intermittent query failures. The issue appears when
            Cortex ingesters change ring membership and Thanos' StoreAPI returns
            partial results.

            Your task:
            1. Find how Cortex manages the ingester hash ring and handles membership changes
            2. Find how Thanos StoreAPI discovers and queries Cortex ingesters
            3. Identify the race condition between ring membership changes and query routing
            4. Document the failure mode and recommended mitigation

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/cortexproject/cortex", "v1.16.0", "cortex", "primary"),
            RepoSpec("https://github.com/thanos-io/thanos", "v0.32.0", "thanos", "dependency"),
        ),
        languages=("go",), frameworks=("cortex", "thanos"),
        multi_repo_pattern="investigate",
        checkpoints=(
            Checkpoint("cortex_ring", 0.30, "Agent identifies Cortex ring management",
                       ("ring|ingester|hash|membership", "cortex")),
            Checkpoint("thanos_store", 0.40, "Agent finds Thanos StoreAPI discovery",
                       ("StoreAPI|store.*gateway|discover", "thanos")),
            Checkpoint("race_condition", 0.30, "Agent identifies the race condition",
                       ("race|partial|membership.*change|inconsist", "query|result")),
        ),
        required_files=(
            FileRef("cortex/pkg/ring/ring.go", "cortex", 0.90),
            FileRef("thanos/pkg/store/proxy.go", "thanos", 0.90),
        ),
        sufficient_files=(
            FileRef("cortex/pkg/ingester/ingester.go", "cortex", 0.85),
            FileRef("thanos/pkg/query/querier.go", "thanos", 0.80),
        ),
        artifacts_required=("answer",), mcp_benefit="high",
        mcp_rationale="Ring membership and query routing tracing across two large repos needs search",
        ground_truth_extra={"incident_type": "distributed_consistency"},
    ),
    TaskDef(
        task_id="error-prov-dual-otel-jaeger-001",
        suite="customer_escalation",
        task_type="error_provenance",
        difficulty="hard",
        difficulty_stratum="dual_repo",
        repo_set_id="cncf-observability-ecosystem",
        estimated_minutes=35,
        description="Trace OpenTelemetry SDK span export error propagation through Jaeger exporter",
        prompt=textwrap.dedent("""\
            An application using OpenTelemetry SDK to export traces to Jaeger is
            silently dropping spans. The export errors are being swallowed somewhere
            in the pipeline between OTel SDK and Jaeger collector.

            Your task:
            1. Find how OTel SDK handles span export errors in the batch processor
            2. Find how Jaeger exporter reports errors back to OTel SDK
            3. Trace the error path from Jaeger collector rejection to OTel SDK
            4. Identify where errors are silently swallowed

            Write your analysis to /workspace/analysis/IMPACT_REPORT.md"""),
        repos=(
            RepoSpec("https://github.com/open-telemetry/opentelemetry-go", "v1.21.0", "otel-go", "primary"),
            RepoSpec("https://github.com/jaegertracing/jaeger", "v1.51.0", "jaeger", "dependency"),
        ),
        languages=("go",), frameworks=("opentelemetry", "jaeger"),
        multi_repo_pattern="investigate",
        checkpoints=(
            Checkpoint("otel_batch_processor", 0.30, "Agent identifies OTel batch export error handling",
                       ("batchProcessor|SpanExporter|ExportSpans", "error|retry|drop")),
            Checkpoint("jaeger_exporter", 0.40, "Agent finds Jaeger exporter error reporting",
                       ("jaeger.*exporter|grpc.*export|collector", "error|status|reject")),
            Checkpoint("silent_drop", 0.30, "Agent identifies where errors are swallowed",
                       ("swallow|silent|drop|ignore.*error", "log.*warn|discard")),
        ),
        required_files=(
            FileRef("otel-go/sdk/trace/batch_span_processor.go", "otel-go", 0.90),
            FileRef("jaeger/cmd/collector/app/handler/grpc_handler.go", "jaeger", 0.85),
        ),
        sufficient_files=(
            FileRef("otel-go/sdk/trace/span.go", "otel-go", 0.80),
            FileRef("jaeger/cmd/collector/app/span_handler_builder.go", "jaeger", 0.80),
        ),
        artifacts_required=("answer",), mcp_benefit="medium",
        mcp_rationale="Error path tracing across OTel SDK and Jaeger benefits from cross-repo search",
        ground_truth_extra={"error_type": "silent_span_drop"},
    ),
]


# ---------------------------------------------------------------------------
# Generation logic
# ---------------------------------------------------------------------------

def _render_task_toml(t: TaskDef) -> str:
    """Render a complete task.toml from a TaskDef."""
    lines = [
        f"# {t.task_type}: {t.description[:60]}",
        f"",
        f'difficulty_stratum = "{t.difficulty_stratum}"',
        f'mcp_suite = "eb_v1"',
        f'repo_set_id = "{t.repo_set_id}"',
        f"org_scale = true",
        f'verification_modes = ["deterministic"]',
        f"",
        f"[task]",
        f'id = "{t.task_id}"',
        f'suite = "{t.suite}"',
        f'task_type = "{t.task_type}"',
        f'difficulty = "{t.difficulty}"',
        f"estimated_duration_minutes = {t.estimated_minutes}",
        f'session_type = "single"',
        f'description = "{t.description}"',
        f'prompt = """',
        t.prompt.rstrip(),
        f'"""',
    ]

    for r in t.repos:
        lines += [
            "", "[[repos]]",
            f'url = "{r.url}"', f'rev = "{r.rev}"',
            f'path = "{r.path}"', f'role = "{r.role}"',
        ]

    langs = ", ".join(f'"{l}"' for l in t.languages)
    fws = ", ".join(f'"{f}"' for f in t.frameworks)
    lines += [
        "", "[metadata]",
        f"languages = [{langs}]",
        f"total_loc = 200_000",
        f"dependency_depth = {len(t.repos)}",
        f"frameworks = [{fws}]",
        f'multi_repo_pattern = "{t.multi_repo_pattern}"',
    ]

    for cp in t.checkpoints:
        verifier = f"checks/check_{cp.name}.sh"
        lines += [
            "", "[[checkpoints]]",
            f'name = "{cp.name}"',
            f"weight = {cp.weight:.2f}",
            f'verifier = "{verifier}"',
            f'description = "{cp.description}"',
            f"timeout_seconds = 120",
        ]

    arts_req = ", ".join(f'"{a}"' for a in t.artifacts_required)
    lines += [
        "", "[artifacts]",
        f"required = [{arts_req}]",
        f'optional = ["migration_guide"]',
    ]

    lines += [
        "", "[tool_access]",
        f'expected_mcp_benefit = "{t.mcp_benefit}"',
        f'mcp_benefit_rationale = "{t.mcp_rationale}"',
    ]

    lines += [
        "", "[ground_truth]",
        f'tiers = ["deterministic"]',
    ]

    for ref in t.required_files:
        lines += [
            "", "[[ground_truth.required_files]]",
            f'path = "{ref.path}"', f'repo = "{ref.repo}"',
            f"confidence = {ref.confidence}", f'source = "{ref.source}"',
        ]

    for ref in t.sufficient_files:
        lines += [
            "", "[[ground_truth.sufficient_files]]",
            f'path = "{ref.path}"', f'repo = "{ref.repo}"',
            f"confidence = {ref.confidence}",
        ]

    return "\n".join(lines) + "\n"


def _render_ground_truth_json(t: TaskDef) -> str:
    """Render ground_truth.json."""
    data = {
        "task_id": t.task_id,
        "task_type": t.task_type,
        "repos": [{"url": r.url, "rev": r.rev, "path": r.path} for r in t.repos],
        "required_files": [
            {"path": f.path, "repo": f.repo, "confidence": f.confidence, "source": f.source}
            for f in t.required_files
        ],
        "sufficient_files": [
            {"path": f.path, "repo": f.repo, "confidence": f.confidence}
            for f in t.sufficient_files
        ],
    }
    data.update(t.ground_truth_extra)
    return json.dumps(data, indent=2) + "\n"


def _render_instruction_md(t: TaskDef) -> str:
    """Render instruction.md."""
    repo_names = " + ".join(r.path for r in t.repos)
    return textwrap.dedent(f"""\
        # {t.task_id}

        **Suite:** {t.suite} | **Type:** {t.task_type} | **Difficulty:** {t.difficulty}
        **Repos:** {repo_names}

        ## Context

        {t.prompt.strip()}

        ## Expected Output

        Write your analysis to `/workspace/analysis/IMPACT_REPORT.md`.

        Your report should include:
        - File paths from each repository that are relevant
        - Specific code symbols, functions, or types involved
        - The nature of each change (removal, rename, signature change, behavioral)
        - Migration recommendations

        ## Hints

        - All repos are cloned under `/workspace/`
        - Focus on import/dependency declarations first, then trace into implementation
        - Check version constraints in dependency manifests (Cargo.toml, go.mod, pom.xml, requirements.txt)
    """)


def _render_check_script(cp: Checkpoint, t: TaskDef) -> str:
    """Render a checkpoint verifier shell script."""
    grep_checks = []
    for i, pat in enumerate(cp.grep_patterns):
        grep_checks.append(
            f'grep -qiE \'{pat}\' "$REPORT" && FOUND=$((FOUND + 1))'
        )
    grep_block = "\n".join(grep_checks)
    total = len(cp.grep_patterns)

    return textwrap.dedent(f"""\
        #!/usr/bin/env bash
        # Verifier for checkpoint: {cp.name}
        # Task: {t.task_id}
        set -euo pipefail

        REPORT="${{WORKSPACE:-/workspace}}/analysis/IMPACT_REPORT.md"

        if [[ ! -f "$REPORT" ]]; then
            printf '{{"score": 0.0, "passed": false, "detail": "Report not found"}}\\n'
            exit 0
        fi

        FOUND=0
        TOTAL={total}

        {grep_block}

        SCORE=$(awk "BEGIN {{printf \\"%.2f\\", $FOUND/$TOTAL}}")
        PASSED=$([ "$FOUND" -ge "$TOTAL" ] && echo true || echo false)
        printf '{{"score": %s, "passed": %s, "detail": "Matched %d/%d patterns"}}\\n' "$SCORE" "$PASSED" "$FOUND" "$TOTAL"
    """)


def generate_all(dry_run: bool = False) -> list[str]:
    """Generate all task directories."""
    generated = []

    for t in TASKS:
        task_dir = BENCHMARKS_DIR / t.suite / t.task_id

        if dry_run:
            print(f"[DRY RUN] Would create: {task_dir}")
            generated.append(str(task_dir))
            continue

        task_dir.mkdir(parents=True, exist_ok=True)
        checks_dir = task_dir / "checks"
        checks_dir.mkdir(exist_ok=True)

        # task.toml
        (task_dir / "task.toml").write_text(_render_task_toml(t))

        # ground_truth.json
        (task_dir / "ground_truth.json").write_text(_render_ground_truth_json(t))

        # instruction.md
        (task_dir / "instruction.md").write_text(_render_instruction_md(t))

        # Checkpoint verifier scripts
        for cp in t.checkpoints:
            script_path = checks_dir / f"check_{cp.name}.sh"
            script_path.write_text(_render_check_script(cp, t))
            script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        generated.append(str(task_dir))
        print(f"  Created: {t.task_id} ({t.repo_set_id}, {t.difficulty})")

    return generated


def main() -> None:
    import sys
    dry_run = "--dry-run" in sys.argv

    print(f"Generating {len(TASKS)} diversity tasks across 4 ecosystems...")
    print(f"  rust-async-ecosystem: {sum(1 for t in TASKS if t.repo_set_id == 'rust-async-ecosystem')}")
    print(f"  python-packaging-ecosystem: {sum(1 for t in TASKS if t.repo_set_id == 'python-packaging-ecosystem')}")
    print(f"  java-enterprise-ecosystem: {sum(1 for t in TASKS if t.repo_set_id == 'java-enterprise-ecosystem')}")
    print(f"  cncf-observability-ecosystem: {sum(1 for t in TASKS if t.repo_set_id == 'cncf-observability-ecosystem')}")
    print()

    generated = generate_all(dry_run=dry_run)

    print(f"\nGenerated {len(generated)} task directories")
    if not dry_run:
        print("Run: python3 scripts/validation/ecosystem_diversity_report.py")


if __name__ == "__main__":
    main()
