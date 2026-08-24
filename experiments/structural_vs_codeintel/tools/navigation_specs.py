"""Specifications for the corpus tasks that close the slice's coverage gaps.

The disclosure gate that makes the 63-task retrieval slice leak-free also
removed every task whose answer is a known identifier, and left only five whose
answer is defined by code SHAPE. Those are exactly the two axes on which
lexical and structural search are supposed to be sufficient, so without them
the slice can show precise navigation winning on symbol tracing but cannot
falsify a lexical-first claim on its own ground.

Each spec here names an ORACLE and the exact query it runs against the pinned
clone. Nothing in a generated answer key is asserted by hand: the key is
whatever the oracle returns, and ``--check`` fails if the repo no longer
produces the pinned set. ``expected`` exists so a silent drift (a re-clone at a
different revision, an ast-grep grammar change) is loud rather than quietly
rewriting the truth an experiment was scored against.

Two oracles are used, and the asymmetry is deliberate and must be reported:

* ``identifier`` tasks use a word-boundary text match. Lexical search can
  reproduce that key trivially. That is the POINT of the axis -- it is the
  cheap floor, and what it tests is not who can find the answer but whether an
  agent holding richer tools overspends on a question that did not need them.
* ``shape`` tasks use a parser. Structural search can reproduce THAT key
  trivially, for the mirror-image reason.

So one axis favours the lexical arm's tool as oracle and the other favours the
structural arm's, and precise code intel is the oracle for neither. Any
per-category claim drawn from these tasks has to be stated with that in view.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Spec:
    task_id: str
    kind: str  # "identifier" | "shape"
    store: str  # sg-evals mirror directory name
    url: str
    rev: str
    path: str  # workspace directory name
    scope: str  # repo-relative subtree the oracle searches
    query: str  # identifier name, or ast-grep pattern
    language: str  # ast-grep language name; "" for identifier tasks
    title: str
    scenario: str  # instruction body; must not name a required file
    categories: tuple[str, ...]
    expected: tuple[str, ...]
    languages: tuple[str, ...] = ()
    frameworks: tuple[str, ...] = ()
    notes: str = ""


_ANSWER = """
## What to produce

Write `/workspace/agent_output/answer.json` with this shape:

```json
{
  "source_files": [
    {"path": "<repo-relative path>", "why": "<one line: what this file contributes>"}
  ]
}
```

List every source file that qualifies and no others. Exclude tests, vendored
trees, generated code, and documentation. Paths are relative to the repository
root, not to `/workspace`.
""".strip()

IDENTIFIER_SPECS = (
    Spec(
        task_id="symbol-footprint-envoy-recursion-guard-001",
        kind="identifier",
        store="envoy--v1.31.2",
        url="https://github.com/envoyproxy/envoy",
        rev="v1.31.2",
        path="envoy",
        scope=".",
        query="AutoDebugRecursionChecker",
        language="",
        title="Removal impact: AutoDebugRecursionChecker",
        scenario=(
            "A cleanup proposal on our Envoy fork removes the debug-only guard type "
            "`AutoDebugRecursionChecker` and folds its assertion into the callers "
            "directly. Before anyone writes that change we need to know its blast "
            "radius exactly.\n\n"
            "Report every source file in the repository that would have to be edited: "
            "the file that declares the type and every file that names it."
        ),
        categories=("identifier_lookup", "api_call_sites", "cross_file"),
        expected=(
            "source/common/common/debug_recursion_checker.h",
            "source/common/conn_pool/conn_pool_base.cc",
            "source/common/upstream/conn_pool_map_impl.h",
        ),
        languages=("cpp",),
    ),
    Spec(
        task_id="symbol-footprint-envoy-token-bucket-001",
        kind="identifier",
        store="envoy--v1.31.2",
        url="https://github.com/envoyproxy/envoy",
        rev="v1.31.2",
        path="envoy",
        scope=".",
        query="AtomicTokenBucketImpl",
        language="",
        title="Removal impact: AtomicTokenBucketImpl",
        scenario=(
            "We are replacing the lock-free rate-limiter class "
            "`AtomicTokenBucketImpl` with a shared implementation from our platform "
            "library. The migration has to land in one commit, so we need the full "
            "list of files it touches before we start.\n\n"
            "Report every source file in the repository that declares, defines, or "
            "names that class."
        ),
        categories=("identifier_lookup", "api_call_sites", "cross_file"),
        expected=(
            "source/common/common/token_bucket_impl.cc",
            "source/common/common/token_bucket_impl.h",
            "source/extensions/filters/common/local_ratelimit/local_ratelimit_impl.h",
        ),
        languages=("cpp",),
    ),
    Spec(
        task_id="symbol-footprint-k8s-runningpod-conv-001",
        kind="identifier",
        store="kubernetes--v1.34.0",
        url="https://github.com/kubernetes/kubernetes",
        rev="v1.34.0",
        path="kubernetes",
        scope="pkg",
        query="ConvertPodStatusToRunningPod",
        language="",
        title="Removal impact: ConvertPodStatusToRunningPod",
        scenario=(
            "A proposal on our Kubernetes fork deprecates the helper "
            "`ConvertPodStatusToRunningPod` in favour of a conversion the runtime "
            "layer already performs. Deprecating it means updating every caller in "
            "the same release.\n\n"
            "Report every source file under `pkg/` that defines or names that "
            "function."
        ),
        categories=("identifier_lookup", "api_call_sites", "cross_file"),
        expected=(
            "pkg/kubelet/container/helpers.go",
            "pkg/kubelet/kubelet.go",
            "pkg/kubelet/kuberuntime/kuberuntime_manager.go",
        ),
        languages=("go",),
    ),
    Spec(
        task_id="symbol-footprint-django-array-maxlen-001",
        kind="identifier",
        store="django--5.0",
        url="https://github.com/django/django",
        rev="5.0",
        path="django",
        scope="django",
        query="ArrayMaxLengthValidator",
        language="",
        title="Removal impact: ArrayMaxLengthValidator",
        scenario=(
            "Our platform team is consolidating the PostgreSQL-specific length checks in "
            "our Django fork onto the generic ones. The first candidate is "
            "`ArrayMaxLengthValidator`.\n\n"
            "Report every source file under `django/` that defines that class or "
            "names it."
        ),
        categories=("identifier_lookup", "api_call_sites", "cross_file", "framework_noise"),
        expected=(
            "django/contrib/postgres/fields/array.py",
            "django/contrib/postgres/forms/array.py",
            "django/contrib/postgres/validators.py",
        ),
        languages=("python",),
        frameworks=("django",),
    ),
    Spec(
        task_id="symbol-footprint-tokio-barrier-result-001",
        kind="identifier",
        store="tokio--tokio-1.29.1",
        url="https://github.com/tokio-rs/tokio",
        rev="tokio-1.29.1",
        path="tokio",
        scope="tokio/src",
        query="BarrierWaitResult",
        language="",
        title="Removal impact: BarrierWaitResult",
        scenario=(
            "An API review on our Tokio fork proposes collapsing the "
            "`BarrierWaitResult` type into a plain boolean return. Whoever picks that "
            "up needs the exact edit list first.\n\n"
            "Report every source file under `tokio/src/` that defines that type, "
            "re-exports it, or otherwise names it."
        ),
        categories=("identifier_lookup", "api_call_sites", "cross_file"),
        expected=(
            "tokio/src/loom/std/barrier.rs",
            "tokio/src/sync/barrier.rs",
            "tokio/src/sync/mod.rs",
        ),
        languages=("rust",),
    ),
    Spec(
        task_id="symbol-footprint-grafana-avatar-cache-001",
        kind="identifier",
        store="grafana--v10.2.0",
        url="https://github.com/grafana/grafana",
        rev="v10.2.0",
        path="grafana",
        scope="pkg",
        query="AvatarCacheServer",
        language="",
        title="Removal impact: AvatarCacheServer",
        scenario=(
            "Our hardening review wants the outbound image cache served by "
            "`AvatarCacheServer` removed from the deployment entirely. Removing a "
            "server type means removing its construction and its route wiring in the "
            "same change.\n\n"
            "Report every source file under `pkg/` that declares that type or names "
            "it."
        ),
        categories=("identifier_lookup", "api_call_sites", "cross_file"),
        expected=(
            "pkg/api/api.go",
            "pkg/api/avatar/avatar.go",
            "pkg/api/http_server.go",
        ),
        languages=("go",),
    ),
)

SHAPE_SPECS = (
    Spec(
        task_id="shape-audit-envoy-http-downcast-001",
        kind="shape",
        store="envoy--v1.31.2",
        url="https://github.com/envoyproxy/envoy",
        rev="v1.31.2",
        path="envoy",
        scope="source/common/http",
        query="dynamic_cast<$T>($X)",
        language="cpp",
        title="Downcast audit in the HTTP layer",
        scenario=(
            "A performance review flagged run-time type checks on the request hot "
            "path. Before we decide whether to replace any of them we need an "
            "inventory of where the HTTP layer converts a base-class pointer or "
            "reference down to a derived type using a checked run-time conversion "
            "(the conversion form that returns null or throws when the object is not "
            "of the target type, rather than the unchecked form).\n\n"
            "Search the tree under `source/common/http/`. Report every source file "
            "there that performs at least one such conversion. Ignore conversions "
            "that are unchecked, and ignore ones that only appear in a comment."
        ),
        categories=("syntactic_pattern", "cross_file"),
        expected=(
            "source/common/http/codec_client.cc",
            "source/common/http/http3/conn_pool.cc",
            "source/common/http/mixed_conn_pool.cc",
        ),
        languages=("cpp",),
    ),
    Spec(
        task_id="shape-audit-grafana-panic-recovery-001",
        kind="shape",
        store="grafana--v10.2.0",
        url="https://github.com/grafana/grafana",
        rev="v10.2.0",
        path="grafana",
        scope="pkg/services",
        query="recover()",
        language="go",
        title="Panic-recovery inventory across the service layer",
        scenario=(
            "After an outage where a background goroutine took the process down, we "
            "are standardising how services contain a panic instead of letting it "
            "reach the top of the stack. Step one is an inventory of the current "
            "state.\n\n"
            "Search the tree under `pkg/services/`. Report every source file there "
            "that installs a panic-containment call, that is, a file that calls the "
            "built-in which stops a panicking goroutine and returns the panic value. "
            "Exclude tests. A file counts if it contains at least one such call, "
            "whether or not it is inside a deferred function."
        ),
        categories=("syntactic_pattern", "cross_file"),
        expected=(
            "pkg/services/alerting/engine.go",
            "pkg/services/anonymous/anonimpl/client.go",
            "pkg/services/authn/authnimpl/sync/user_sync.go",
            "pkg/services/authn/clients/api_key.go",
            "pkg/services/live/pipeline/models.go",
            "pkg/services/ngalert/api/hcl/hcl.go",
            "pkg/services/ngalert/eval/eval.go",
            "pkg/services/query/query.go",
            "pkg/services/signingkeys/signingkeystore/store.go",
            "pkg/services/supportbundles/supportbundlesimpl/service.go",
            "pkg/services/supportbundles/supportbundlesimpl/service_bundle.go",
        ),
        languages=("go",),
    ),
    Spec(
        task_id="shape-audit-tokio-scheduler-drop-001",
        kind="shape",
        store="tokio--tokio-1.29.1",
        url="https://github.com/tokio-rs/tokio",
        rev="tokio-1.29.1",
        path="tokio",
        scope="tokio/src/runtime/scheduler",
        query="impl Drop for $T {\n    $$$B\n}",
        language="rust",
        title="Teardown-hook inventory in the scheduler",
        scenario=(
            "We are changing the order in which the runtime tears itself down, and "
            "any type that runs code at destruction time inside the scheduler is "
            "affected by that ordering.\n\n"
            "Search the tree under `tokio/src/runtime/scheduler/`. Report every "
            "source file there that provides a destructor implementation for a type, "
            "that is, an implementation block for the trait the language runs when a "
            "value goes out of scope."
        ),
        categories=("syntactic_pattern", "interface_implementations", "cross_file"),
        expected=(
            "tokio/src/runtime/scheduler/current_thread.rs",
            "tokio/src/runtime/scheduler/multi_thread/counters.rs",
            "tokio/src/runtime/scheduler/multi_thread/worker.rs",
        ),
        languages=("rust",),
    ),
    Spec(
        task_id="shape-audit-django-backend-hooks-001",
        kind="shape",
        store="django--5.0",
        url="https://github.com/django/django",
        rev="5.0",
        path="django",
        scope="django/db",
        query="def $F($$$A):\n    raise NotImplementedError($$$M)",
        language="python",
        title="Unimplemented extension points in the database layer",
        scenario=(
            "We are writing a database backend for an in-house column store, and we "
            "need to know which parts of the database layer are abstract: the methods "
            "it declares without behaviour, leaving the real implementation to a "
            "subclass.\n\n"
            "Search the tree under `django/db/`. Report every source file there that "
            "declares at least one method whose entire body raises the "
            "not-implemented error, signalling that a subclass must provide the real "
            "behaviour. A method that raises it only on one branch, after doing other "
            "work, does not count."
        ),
        categories=("syntactic_pattern", "interface_implementations", "cross_file",
                    "framework_noise"),
        expected=(
            "django/db/backends/base/client.py",
            "django/db/backends/base/operations.py",
            "django/db/backends/ddl_references.py",
            "django/db/migrations/serializer.py",
            "django/db/models/constraints.py",
            "django/db/models/expressions.py",
            "django/db/models/lookups.py",
        ),
        languages=("python",),
        frameworks=("django",),
    ),
    Spec(
        task_id="shape-audit-ansible-plugin-hooks-001",
        kind="shape",
        store="ansible--v2.16.0",
        url="https://github.com/ansible/ansible",
        rev="v2.16.0",
        path="ansible",
        scope="lib/ansible",
        query="def $F($$$A):\n    raise NotImplementedError($$$M)",
        language="python",
        title="Unimplemented extension points in the plugin system",
        scenario=(
            "We maintain an internal distribution of Ansible and are documenting "
            "every hook a plugin author is required to implement, as opposed to the "
            "ones that already have working defaults.\n\n"
            "Search the tree under `lib/ansible/`. Report every source file there "
            "that declares at least one method whose entire body raises the "
            "not-implemented error. A method that raises it conditionally, after "
            "doing other work, does not count."
        ),
        categories=("syntactic_pattern", "interface_implementations", "cross_file",
                    "dynamic_ambiguous"),
        expected=(
            "lib/ansible/plugins/__init__.py",
            "lib/ansible/plugins/filter/__init__.py",
            "lib/ansible/plugins/loader.py",
            "lib/ansible/plugins/shell/powershell.py",
            "lib/ansible/template/__init__.py",
            "lib/ansible/utils/collection_loader/_collection_finder.py",
        ),
        languages=("python",),
        frameworks=("ansible",),
    ),
)

SPECS = IDENTIFIER_SPECS + SHAPE_SPECS
