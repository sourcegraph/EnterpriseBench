"""Task definitions for the 18 non-Go multi-repo tasks.

Each entry is a TaskDef built from real cross-repo coupling. The bug
patterns are inspired by long-running issues in each ecosystem; the
required_files are real paths at the pinned revisions.
"""
from __future__ import annotations

from non_go_multirepo_tasks import (
    Checkpoint,
    GTFile,
    Repo,
    TaskDef,
)


# ---------------------------------------------------------------------------
# Python (4) — customer_escalation / support_code_mapping
# ---------------------------------------------------------------------------


T_DJANGO_WAGTAIL = TaskDef(
    task_id="support-mapping-dual-django-wagtail-treepath-001",
    suite="customer_escalation",
    task_type="support_code_mapping",
    difficulty="hard",
    duration=35,
    description=(
        "Map customer Wagtail page-tree query slowdown to the Django ORM "
        "treebeard MP_Node path-encoding boundary"
    ),
    prompt=(
        "A customer reports that their Wagtail site's page navigation is suddenly\n"
        "executing N+1 queries per request after they upgraded Django and added a\n"
        "thin custom Page subclass. Wagtail's get_descendants() now produces a query\n"
        "plan that scans the entire wagtailcore_page table instead of using the\n"
        "MP_Node materialized path index.\n\n"
        "Your task:\n"
        "1. Find where Wagtail's Page model is defined and how it inherits from\n"
        "   treebeard's MP_Node (the materialized-path tree mixin) for path-prefix\n"
        "   queries.\n"
        "2. Find where Django's QuerySet builds the SQL for a startswith path lookup\n"
        "   and how the query planner picks between an index scan and a full table\n"
        "   scan based on the Field's db_index and ordering.\n"
        "3. Trace the cross-repo path from Page.get_descendants() through MP_Node's\n"
        "   path filter into Django's QuerySet.filter() and the SQL builder. Cite\n"
        "   each file and the function or attribute responsible.\n"
        "4. Identify why the customer's custom Page subclass disables the path index\n"
        "   (Meta.indexes inheritance + abstract base class semantics on Wagtail's\n"
        "   AbstractPage)."
    ),
    repos=(
        Repo("https://github.com/wagtail/wagtail", "v5.2.2", "wagtail", "primary"),
        Repo("https://github.com/django/django", "4.2.10", "django", "upstream"),
    ),
    languages=("python",),
    total_loc=900_000,
    frameworks=("django", "wagtail", "treebeard"),
    multi_repo_pattern="investigate",
    repo_set_id="wagtail-django-cms-ecosystem",
    mcp_benefit_rationale=(
        "Tracing get_descendants() from Wagtail's Page model into Django's "
        "QuerySet path filter requires cross-repo navigation: Wagtail defines "
        "the MP_Node-based Page tree, Django enforces the SQL builder choice. "
        "The query plan regression is invisible from either repo alone."
    ),
    required_files=(
        GTFile("wagtail/wagtail/models/__init__.py", "wagtail", 0.95),
        GTFile("wagtail/wagtail/query.py", "wagtail", 0.85),
        GTFile("django/django/db/models/query.py", "django", 0.90),
        GTFile("django/django/db/models/sql/compiler.py", "django", 0.85),
    ),
    sufficient_files=(
        GTFile("django/django/db/models/options.py", "django", 0.75),
        GTFile("django/django/db/models/fields/__init__.py", "django", 0.70),
    ),
    error_chain=(
        "Wagtail Page extends AbstractPage which inherits from treebeard MP_Node, "
        "providing a materialized-path field used to encode tree position",
        "Page.get_descendants() builds a queryset filtered by path startswith on "
        "the parent's path, expecting an index-supported prefix lookup",
        "When the customer's custom Page subclass overrides Meta without "
        "preserving indexes, Django's options.py drops the inherited db_index for "
        "path during model construction",
        "Django's QuerySet.filter() in db/models/query.py compiles to SQL via "
        "sql/compiler.py, which falls back to a full table scan because no usable "
        "index is registered on the path column",
        "The query plan regression manifests as N+1 latency in Wagtail's "
        "navigation rendering despite the same source code as before",
    ),
    trigger_conditions=(
        "The customer's custom Page subclass overrides Meta or class_prepared "
        "without re-declaring Meta.indexes, dropping the inherited path index",
        "Django's model-options inheritance treats Meta.indexes as non-cumulative "
        "by default; Wagtail's AbstractPage relies on the parent index being kept",
        "The startswith path filter is only fast when the path field has a "
        "B-tree index; without it Django's compiler emits a full-scan plan",
    ),
    expected_answer={
        "error_origin": (
            "Django sql/compiler.py emits a full-scan plan for the path "
            "startswith lookup because the customer's custom Page subclass "
            "dropped the inherited db_index on path"
        ),
        "bypass_reason": (
            "Meta.indexes is not cumulative across abstract bases in Django's "
            "options.py; the customer's subclass silently removes Wagtail's "
            "AbstractPage.path index"
        ),
        "correct_config": (
            "Re-declare Meta.indexes on the custom Page subclass to include "
            "the path B-tree index, or leave Meta unset so Wagtail's "
            "AbstractPage Meta is inherited verbatim"
        ),
    },
)


T_FLASK_WERKZEUG = TaskDef(
    task_id="support-mapping-dual-flask-werkzeug-ctxvar-001",
    suite="customer_escalation",
    task_type="support_code_mapping",
    difficulty="hard",
    duration=30,
    description=(
        "Map customer Flask request-context vanishing under asyncio + ContextVar "
        "to the Werkzeug LocalStack contextvars boundary"
    ),
    prompt=(
        "A customer reports that their Flask 2.3 app loses request context inside\n"
        "an async view that awaits a coroutine spawned via asyncio.create_task.\n"
        "Inside the spawned task, request.path raises RuntimeError: 'Working\n"
        "outside of request context.' even though the same code worked under\n"
        "Flask 2.0 with werkzeug 2.0's local-stack semantics.\n\n"
        "Your task:\n"
        "1. Find where Flask exposes the global `request` proxy and how it binds\n"
        "   to a Werkzeug LocalStack/ContextVar-backed object.\n"
        "2. Find where Werkzeug's Local switched from threadlocal to contextvars\n"
        "   and how a forked asyncio task inherits or fails to inherit the\n"
        "   active context.\n"
        "3. Trace the request-context-not-available chain: Flask app context ->\n"
        "   werkzeug Local() proxy -> contextvars.ContextVar.get() -> asyncio\n"
        "   Task.create(context=copy_context()) semantics. Cite each file and\n"
        "   the function responsible.\n"
        "4. Identify why asyncio.create_task captures the parent context but a\n"
        "   bare `ensure_future(coro)` from a thread-pool worker may not, and\n"
        "   the consequence at the werkzeug/Flask boundary."
    ),
    repos=(
        Repo("https://github.com/pallets/flask", "2.3.3", "flask", "primary"),
        Repo("https://github.com/pallets/werkzeug", "2.3.7", "werkzeug", "upstream"),
    ),
    languages=("python",),
    total_loc=80_000,
    frameworks=("flask", "werkzeug", "asyncio"),
    multi_repo_pattern="investigate",
    repo_set_id="pallets-ecosystem",
    mcp_benefit_rationale=(
        "Tracing the request proxy from Flask through Werkzeug's Local into "
        "contextvars semantics requires cross-repo navigation: Flask defines "
        "the public `request` global, Werkzeug owns the storage primitive that "
        "interacts with asyncio's context-copy semantics."
    ),
    required_files=(
        GTFile("flask/src/flask/globals.py", "flask", 0.95),
        GTFile("flask/src/flask/ctx.py", "flask", 0.90),
        GTFile("werkzeug/src/werkzeug/local.py", "werkzeug", 0.95),
    ),
    sufficient_files=(
        GTFile("flask/src/flask/app.py", "flask", 0.80),
        GTFile("werkzeug/src/werkzeug/wrappers/request.py", "werkzeug", 0.70),
    ),
    error_chain=(
        "Flask's globals.py defines `request` as a LocalProxy backed by "
        "werkzeug.local.Local with a contextvars-based storage strategy",
        "Werkzeug's local.py replaced the threadlocal storage with "
        "contextvars.ContextVar so that async coroutines see a per-task "
        "logical context",
        "Flask's ctx.py pushes a RequestContext onto the Local stack at the "
        "start of dispatching and pops it at request teardown",
        "When the async view awaits asyncio.create_task(coro), the task "
        "captures the current context via copy_context, preserving the bound "
        "request proxy",
        "When code instead schedules work onto a thread pool or uses a bare "
        "ensure_future without copying context, the spawned task evaluates "
        "ContextVar.get() in an empty context, raising RuntimeError from the "
        "LocalProxy access",
    ),
    trigger_conditions=(
        "Customer code awaits a coroutine via run_in_executor or thread-pool "
        "worker which does not propagate the active contextvars context",
        "Werkzeug's Local switched from threadlocal to ContextVar storage, "
        "so the regression appeared after the werkzeug 2.0+ upgrade",
        "Flask's RequestContext proxy raises RuntimeError when the underlying "
        "ContextVar.get() returns LookupError, masking the contextvars cause",
    ),
    expected_answer={
        "error_origin": (
            "werkzeug.local.Local's ContextVar storage returns LookupError in "
            "the spawned task because the parent context was not copied at "
            "task creation"
        ),
        "bypass_reason": (
            "asyncio.create_task captures the current context but "
            "ensure_future from a thread pool or run_in_executor does not, "
            "so the LocalProxy resolves to an empty contextvars binding"
        ),
        "correct_config": (
            "Use loop.run_in_executor with contextvars.copy_context().run, "
            "or push a Flask app/request context inside the coroutine via "
            "app.test_request_context() / app.app_context() so the proxy "
            "resolves before any thread-pool offload"
        ),
    },
)


T_FASTAPI_PYDANTIC = TaskDef(
    task_id="support-mapping-dual-fastapi-pydantic-validator-001",
    suite="customer_escalation",
    task_type="support_code_mapping",
    difficulty="hard",
    duration=30,
    description=(
        "Map customer FastAPI request-body field-drop to the Pydantic v2 "
        "model_validator chain integration boundary"
    ),
    prompt=(
        "A customer reports that after upgrading from Pydantic v1 to Pydantic v2\n"
        "their FastAPI POST endpoint silently drops a discriminated-union field on\n"
        "the request body. The endpoint returns 200 OK, the response body shows the\n"
        "field as None, but the client clearly sent it. No 422 error is raised.\n\n"
        "Your task:\n"
        "1. Find where FastAPI builds a request-body parameter from a Pydantic\n"
        "   model and validates it before invoking the route handler.\n"
        "2. Find where Pydantic v2 evaluates a discriminated union and how\n"
        "   model_validator(mode='before') interacts with discriminator field\n"
        "   resolution.\n"
        "3. Trace the cross-repo flow: FastAPI dependency injection -> body\n"
        "   parameter solving -> Pydantic model_validate_json -> discriminator\n"
        "   resolution -> field assignment. Cite each file and function.\n"
        "4. Identify why a model_validator(mode='before') that pre-processes\n"
        "   incoming JSON can silently strip the discriminator key, leading to\n"
        "   the union resolving to its default variant with the field set to None."
    ),
    repos=(
        Repo("https://github.com/tiangolo/fastapi", "0.110.0", "fastapi", "primary"),
        Repo("https://github.com/pydantic/pydantic", "v2.6.4", "pydantic", "upstream"),
    ),
    languages=("python",),
    total_loc=200_000,
    frameworks=("fastapi", "pydantic", "starlette"),
    multi_repo_pattern="investigate",
    repo_set_id="fastapi-pydantic-ecosystem",
    mcp_benefit_rationale=(
        "FastAPI's body validation funnels request data into Pydantic, but the "
        "field-drop bug lives at the discriminator resolver inside Pydantic's "
        "core schema. Cross-repo navigation is required to see why a "
        "FastAPI 200 OK is consistent with the model dropping the field."
    ),
    required_files=(
        GTFile("fastapi/fastapi/dependencies/utils.py", "fastapi", 0.95),
        GTFile("fastapi/fastapi/routing.py", "fastapi", 0.85),
        GTFile("pydantic/pydantic/main.py", "pydantic", 0.90),
        GTFile("pydantic/pydantic/_internal/_discriminated_union.py", "pydantic", 0.85),
    ),
    sufficient_files=(
        GTFile("fastapi/fastapi/_compat.py", "fastapi", 0.70),
        GTFile("pydantic/pydantic/functional_validators.py", "pydantic", 0.75),
    ),
    error_chain=(
        "FastAPI routing.py builds a request handler that calls "
        "solve_dependencies in dependencies/utils.py, which in turn validates "
        "the request body via Pydantic's model_validate_json",
        "Pydantic v2 main.py routes model_validate_json into the core schema, "
        "where _internal/_discriminated_union.py uses the discriminator field "
        "to pick the right variant",
        "If a model_validator(mode='before') in functional_validators.py "
        "returns a dict that omits the discriminator key, the union resolver "
        "falls back to the first variant and skips the field",
        "FastAPI receives the populated model with None on the dropped field "
        "and serializes it as the 200 OK response without raising 422 because "
        "Pydantic considered validation successful",
    ),
    trigger_conditions=(
        "The model uses a discriminated union with a model_validator(mode='before') "
        "that pre-processes the dict and drops the discriminator key",
        "Pydantic v2 changed discriminator resolution to silently fall back to "
        "the first variant when the discriminator value is missing",
        "FastAPI does not surface this fallback as a 422; the agent must "
        "inspect the dependency-resolution path to detect it",
    ),
    expected_answer={
        "error_origin": (
            "Pydantic v2's _internal/_discriminated_union.py fallback when "
            "the discriminator key is dropped by a model_validator before-hook"
        ),
        "bypass_reason": (
            "FastAPI's solve_dependencies treats successful Pydantic "
            "validation as a 200 OK regardless of which union variant won, "
            "so the dropped field shows up as None instead of as a 422"
        ),
        "correct_config": (
            "Move pre-processing into model_validator(mode='after') so the "
            "discriminator field is preserved through union resolution, or "
            "validate the discriminator field explicitly in the before-hook"
        ),
    },
)


T_CELERY_KOMBU = TaskDef(
    task_id="support-mapping-dual-celery-kombu-prefetch-001",
    suite="customer_escalation",
    task_type="support_code_mapping",
    difficulty="hard",
    duration=35,
    description=(
        "Map customer Celery worker stalling after broker reconnect to the "
        "Kombu connection-retry / channel-prefetch reset boundary"
    ),
    prompt=(
        "A customer reports that their Celery worker stops consuming new tasks\n"
        "after the RabbitMQ broker briefly disconnects. The worker logs the\n"
        "reconnect, but no further task callbacks fire even though the queue\n"
        "has thousands of waiting messages.\n\n"
        "Your task:\n"
        "1. Find where Celery's worker consumer wraps a Kombu Connection and\n"
        "   subscribes to queues with a configured prefetch_count.\n"
        "2. Find where Kombu re-establishes a channel after a broker drop and\n"
        "   identify whether basic_qos is reapplied to the new channel.\n"
        "3. Trace the cross-repo path: celery worker.consumer.Consumer ->\n"
        "   kombu.connection.Connection.ensure -> channel re-creation -> prefetch\n"
        "   reset. Cite each file and the function or attribute responsible.\n"
        "4. Identify why a fresh AMQP channel without basic_qos defaults to\n"
        "   prefetch=0 (unlimited), which combined with the RabbitMQ broker's\n"
        "   semantics can leave the consumer effectively idle waiting for\n"
        "   already-delivered messages to ack."
    ),
    repos=(
        Repo("https://github.com/celery/celery", "v5.3.6", "celery", "primary"),
        Repo("https://github.com/celery/kombu", "v5.3.5", "kombu", "upstream"),
    ),
    languages=("python",),
    total_loc=180_000,
    frameworks=("celery", "kombu", "amqp"),
    multi_repo_pattern="investigate",
    repo_set_id="celery-kombu-ecosystem",
    mcp_benefit_rationale=(
        "Celery's consumer logic and Kombu's channel-recovery logic both touch "
        "the basic_qos call but neither is responsible alone. Cross-repo "
        "navigation is required to see why prefetch is silently reset to 0 "
        "after reconnect."
    ),
    required_files=(
        GTFile("celery/celery/worker/consumer/consumer.py", "celery", 0.95),
        GTFile("celery/celery/worker/consumer/tasks.py", "celery", 0.80),
        GTFile("kombu/kombu/connection.py", "kombu", 0.90),
        GTFile("kombu/kombu/messaging.py", "kombu", 0.85),
    ),
    sufficient_files=(
        GTFile("kombu/kombu/transport/pyamqp.py", "kombu", 0.70),
        GTFile("celery/celery/worker/consumer/mingle.py", "celery", 0.65),
    ),
    error_chain=(
        "Celery's consumer.py opens a Kombu Connection and invokes "
        "tasks.Tasks.start which calls channel.basic_qos with the configured "
        "prefetch_count once at startup",
        "Kombu's connection.py ensure() wrapper detects the broker drop and "
        "transparently re-creates the underlying transport, exposing a fresh "
        "channel object",
        "kombu/messaging.py rebinds the consumer to the new channel but does "
        "not re-issue basic_qos, leaving the new channel at the AMQP default "
        "prefetch_count of 0 (unlimited)",
        "RabbitMQ delivers a burst of unacked messages, but Celery's worker "
        "thread pool is already bounded; without prefetch backpressure the "
        "consumer state machine deadlocks on its own queue size accounting",
    ),
    trigger_conditions=(
        "The broker connection drops and Kombu's ensure() recreates the "
        "channel without re-applying basic_qos",
        "Celery's consumer does not re-issue prefetch on reconnect because it "
        "treats the Kombu channel as a stable abstraction",
        "RabbitMQ's default prefetch_count of 0 means unlimited, so the "
        "fresh channel ignores Celery's worker_prefetch_multiplier setting",
    ),
    expected_answer={
        "error_origin": (
            "kombu/messaging.py rebinding to a recovered channel without "
            "re-issuing basic_qos, leaving prefetch unlimited at the new channel"
        ),
        "bypass_reason": (
            "Celery's consumer.py only calls basic_qos at startup; it relies "
            "on Kombu to preserve channel-level QoS across reconnects, which "
            "Kombu does not do"
        ),
        "correct_config": (
            "Hook into Kombu's on_connection_revived signal in "
            "celery/worker/consumer/consumer.py to re-issue basic_qos with "
            "the configured prefetch_count, or pin the consumer to a "
            "Connection.ensure callback that restores QoS"
        ),
    },
)


# ---------------------------------------------------------------------------
# TypeScript / JavaScript (4) — customer_escalation / support_code_mapping
# ---------------------------------------------------------------------------


T_BABEL_SWC = TaskDef(
    task_id="support-mapping-dual-babel-swc-decorators-001",
    suite="customer_escalation",
    task_type="support_code_mapping",
    difficulty="hard",
    duration=35,
    description=(
        "Map customer TypeScript decorator regression to the Babel "
        "plugin-proposal-decorators / SWC transform stage boundary"
    ),
    prompt=(
        "A customer reports that after switching their build pipeline from Babel\n"
        "to SWC for faster CI, their @injectable() class decorators on Nest.js\n"
        "providers stop firing. The classes still construct, but the DI metadata\n"
        "table never registers them, so resolution fails at runtime with\n"
        "'Cannot read properties of undefined (reading getMetadata)'.\n\n"
        "Your task:\n"
        "1. Find where Babel's plugin-proposal-decorators decides between the\n"
        "   stage-2 legacy form and the stage-3 (TC39) form, and how it interacts\n"
        "   with @babel/plugin-transform-typescript ordering.\n"
        "2. Find where SWC's TypeScript transform handles decorators -- which\n"
        "   stage it implements, and whether it applies decorators before or\n"
        "   after class-property emit.\n"
        "3. Trace the cross-repo path: babel preset-typescript -> "
        "   plugin-proposal-decorators (legacy=true) -> Reflect.metadata side\n"
        "   effect -> Nest's IoC registration. Then trace SWC's parallel path\n"
        "   and identify the divergence.\n"
        "4. Identify the configuration choice (babel-plugin order, swc.jsc.\n"
        "   transform.legacyDecorator + decoratorMetadata) that determines\n"
        "   whether DI metadata is emitted, and why the customer's swc config\n"
        "   produces no metadata at all."
    ),
    repos=(
        Repo("https://github.com/babel/babel", "v7.24.0", "babel", "primary"),
        Repo("https://github.com/swc-project/swc", "v1.4.6", "swc", "upstream"),
    ),
    languages=("typescript", "javascript", "rust"),
    total_loc=600_000,
    frameworks=("babel", "swc", "typescript"),
    multi_repo_pattern="investigate",
    repo_set_id="js-transpiler-ecosystem",
    mcp_benefit_rationale=(
        "Decorator semantics differ between Babel's plugin-proposal-decorators "
        "and SWC's transform stage; tracing why DI metadata vanishes requires "
        "reading both transformers' decorator/class emit code paths."
    ),
    required_files=(
        GTFile("babel/packages/babel-plugin-proposal-decorators/src/index.ts", "babel", 0.95),
        GTFile("babel/packages/babel-plugin-transform-typescript/src/index.ts", "babel", 0.85),
        GTFile("swc/crates/swc_ecma_transforms_proposal/src/decorators.rs", "swc", 0.90),
        GTFile("swc/crates/swc_ecma_transforms_typescript/src/lib.rs", "swc", 0.80),
    ),
    sufficient_files=(
        GTFile("babel/packages/babel-helper-create-class-features-plugin/src/decorators.ts", "babel", 0.75),
        GTFile("swc/crates/swc/src/config/mod.rs", "swc", 0.70),
    ),
    error_chain=(
        "Babel preset-typescript runs plugin-transform-typescript which strips "
        "type annotations but defers decorator emission to "
        "plugin-proposal-decorators",
        "plugin-proposal-decorators with legacy=true emits the stage-2 form: "
        "decorators run as functions wrapping the class and "
        "Reflect.metadata('design:paramtypes', ...) side effects fire, which "
        "Nest's IoC depends on",
        "SWC's swc_ecma_transforms_typescript strips type annotations in the "
        "same logical step as decorators.rs, but decoratorMetadata defaults "
        "to false unless explicitly set in jsc.transform",
        "Without decoratorMetadata, SWC emits the wrapped class without the "
        "Reflect.metadata side effects, so Nest's container has no design:"
        "paramtypes entry for the constructor and DI resolution fails",
    ),
    trigger_conditions=(
        "Customer's swc config sets jsc.parser.decorators=true and "
        "jsc.transform.legacyDecorator=true but omits decoratorMetadata=true, "
        "so Reflect.metadata calls are never emitted",
        "Babel's plugin-proposal-decorators emits the metadata side effects "
        "for free under legacy mode; SWC requires them to be opted in",
        "Nest's IoC silently fails when getMetadata returns undefined, "
        "presenting as a runtime undefined-access rather than a clear DI error",
    ),
    expected_answer={
        "error_origin": (
            "swc_ecma_transforms_typescript / decorators.rs not emitting "
            "Reflect.metadata calls because jsc.transform.decoratorMetadata is "
            "false by default"
        ),
        "bypass_reason": (
            "Babel's plugin-proposal-decorators legacy mode emits the metadata "
            "side effects automatically; SWC treats them as an opt-in flag"
        ),
        "correct_config": (
            "Set jsc.transform.decoratorMetadata=true in .swcrc alongside "
            "legacyDecorator=true, or keep Babel for the decorator pass and "
            "use SWC only for non-decorator files"
        ),
    },
)


T_REACT_RELAY = TaskDef(
    task_id="support-mapping-dual-react-relay-suspense-001",
    suite="customer_escalation",
    task_type="support_code_mapping",
    difficulty="hard",
    duration=35,
    description=(
        "Map customer React Suspense boundary unmount on Relay store "
        "invalidation to the React reconciler / Relay environment boundary"
    ),
    prompt=(
        "A customer reports that their React 18 + Relay 16 page silently unmounts\n"
        "and remounts a Suspense boundary every time a Relay store invalidation\n"
        "fires from a mutation, even though no fragment subscribed to that\n"
        "boundary actually reads the invalidated record. The whole subtree's\n"
        "client-side state is lost.\n\n"
        "Your task:\n"
        "1. Find where React's reconciler decides whether a Suspense boundary\n"
        "   re-renders or unmounts after a context update.\n"
        "2. Find where Relay's RelayModernEnvironment publishes a store\n"
        "   invalidation and how it notifies subscribers via\n"
        "   useSubscribeToInvalidationState.\n"
        "3. Trace the cross-repo path: Relay store mutation commit ->\n"
        "   notify subscribers -> useFragment / useLazyLoadQuery hook re-eval\n"
        "   -> React useSyncExternalStore subscription -> reconciler\n"
        "   schedules update -> Suspense boundary re-render decision. Cite\n"
        "   each file and the function or attribute responsible.\n"
        "4. Identify why a global store-version bump can transitively cause\n"
        "   any consumer of useFragment under the Suspense boundary to\n"
        "   re-throw a promise, forcing the boundary to fall back."
    ),
    repos=(
        Repo("https://github.com/facebook/react", "v18.2.0", "react", "upstream"),
        Repo("https://github.com/facebook/relay", "v16.2.0", "relay", "primary"),
    ),
    languages=("javascript", "typescript"),
    total_loc=900_000,
    frameworks=("react", "relay", "graphql"),
    multi_repo_pattern="investigate",
    repo_set_id="react-relay-ecosystem",
    mcp_benefit_rationale=(
        "Tracing why a Relay store invalidation unmounts a React Suspense "
        "boundary requires cross-repo navigation: Relay owns the "
        "store-subscriber graph, React owns the Suspense lifecycle. Neither "
        "repo alone explains the unmount."
    ),
    required_files=(
        GTFile("react/packages/react-reconciler/src/ReactFiberSuspenseComponent.js", "react", 0.90),
        GTFile("react/packages/react-reconciler/src/ReactFiberBeginWork.js", "react", 0.85),
        GTFile("relay/packages/relay-runtime/store/RelayModernEnvironment.js", "relay", 0.95),
        GTFile("relay/packages/react-relay/relay-hooks/useFragmentInternal.js", "relay", 0.90),
    ),
    sufficient_files=(
        GTFile("relay/packages/relay-runtime/store/RelayModernStore.js", "relay", 0.80),
        GTFile("react/packages/use-sync-external-store/src/useSyncExternalStoreShared.js", "react", 0.75),
    ),
    error_chain=(
        "Relay's RelayModernEnvironment.executeMutation calls "
        "store.publish which bumps the store version and fans out to all "
        "subscribed snapshots",
        "useFragmentInternal subscribes via useSyncExternalStore so any "
        "version bump triggers React to re-evaluate the snapshot, even if "
        "the fragment data did not change",
        "When Relay's snapshot rebuild encounters a missing record (because "
        "an invalidator widened the scope), useFragmentInternal throws a "
        "promise to suspend the component",
        "ReactFiberBeginWork sees the thrown promise and forces the nearest "
        "Suspense boundary into pending state; ReactFiberSuspenseComponent "
        "discards the previous tree state per the offscreen-mount semantics",
        "The boundary's children unmount and remount when the mutation "
        "settles, dropping all client-side state under the boundary",
    ),
    trigger_conditions=(
        "A Relay mutation calls commitMutation with an updater that "
        "invalidates a record, bumping the global store version",
        "A useFragment under the Suspense boundary subscribes to the same "
        "store and triggers a snapshot rebuild that suspends",
        "React's Suspense boundary, when its child suspends, discards the "
        "concurrent-mode offscreen state because the reconciler treats the "
        "remount as a fresh subtree",
    ),
    expected_answer={
        "error_origin": (
            "useFragmentInternal throwing a promise after a Relay store "
            "version bump, which propagates to React's Suspense boundary"
        ),
        "bypass_reason": (
            "Relay's invalidation API bumps a global version regardless of "
            "subscriber scope; React's Suspense lifecycle treats child "
            "suspension as a remount"
        ),
        "correct_config": (
            "Constrain Relay invalidation to specific records via "
            "invalidateRecord rather than store-wide invalidation, and wrap "
            "the boundary content with React.startTransition so suspension "
            "preserves offscreen state instead of remounting"
        ),
    },
)


T_JEST_BABEL = TaskDef(
    task_id="support-mapping-dual-jest-babel-cache-001",
    suite="customer_escalation",
    task_type="support_code_mapping",
    difficulty="medium",
    duration=30,
    description=(
        "Map customer Jest tests passing once but failing on rerun after "
        "Babel plugin upgrade to the babel-jest transform-cache key boundary"
    ),
    prompt=(
        "A customer reports that after upgrading @babel/plugin-transform-runtime\n"
        "their Jest test suite passes once on a clean checkout but every rerun\n"
        "fails with 'ReferenceError: regeneratorRuntime is not defined' in async\n"
        "tests. Clearing node_modules/.cache makes it pass once again.\n\n"
        "Your task:\n"
        "1. Find where Jest's runtime invokes its transformer to compile source\n"
        "   files and how it decides whether to use a cached transform output.\n"
        "2. Find where babel-jest computes the transform cache key (file content,\n"
        "   babel config, plugin versions) and which inputs it includes.\n"
        "3. Trace the cross-repo path: jest-runtime ScriptTransformer.transform()\n"
        "   -> babel-jest getCacheKey -> babel-core loadOptions / partialConfig\n"
        "   -> plugin signature inputs. Cite each file and the function\n"
        "   responsible.\n"
        "4. Identify why a plugin upgrade that does NOT bump the package.json\n"
        "   version (for example a local lockfile resolution change) can\n"
        "   produce a stale cache hit, and what makes the second run fail\n"
        "   when the first one passed."
    ),
    repos=(
        Repo("https://github.com/jestjs/jest", "v29.7.0", "jest", "primary"),
        Repo("https://github.com/babel/babel", "v7.24.0", "babel", "upstream"),
    ),
    languages=("javascript", "typescript"),
    total_loc=400_000,
    frameworks=("jest", "babel"),
    multi_repo_pattern="investigate",
    repo_set_id="jest-babel-ecosystem",
    mcp_benefit_rationale=(
        "The cache-mismatch lives between Jest's ScriptTransformer cache key "
        "policy and Babel's plugin loader; understanding why a plugin upgrade "
        "produces a stale hit requires cross-repo navigation."
    ),
    required_files=(
        GTFile("jest/packages/jest-runtime/src/index.ts", "jest", 0.85),
        GTFile("jest/packages/jest-transform/src/ScriptTransformer.ts", "jest", 0.95),
        GTFile("babel/packages/babel-core/src/config/full.ts", "babel", 0.85),
        GTFile("babel/packages/babel-core/src/config/partial.ts", "babel", 0.80),
    ),
    sufficient_files=(
        GTFile("jest/packages/babel-jest/src/index.ts", "jest", 0.90),
        GTFile("babel/packages/babel-core/src/config/files/plugins.ts", "babel", 0.70),
    ),
    error_chain=(
        "Jest's ScriptTransformer.transform invokes the configured "
        "transformer (babel-jest) and consults a content-addressed cache "
        "keyed on the result of getCacheKey",
        "babel-jest's getCacheKey hashes the source content, the babel "
        "config object, and the resolved package versions, but it does not "
        "follow plugin entry-points through transitive resolution",
        "babel-core's partialConfig resolves plugins via "
        "loadPlugin, which walks node_modules but caches the loader result "
        "across runs in the same process",
        "After a plugin upgrade without a package.json version bump (for "
        "instance a workspace symlink swap), babel-jest's cache key still "
        "matches the previous run because the version field is unchanged",
        "Jest reuses the stale transformed output on the rerun; "
        "regeneratorRuntime is missing because the new plugin chunked the "
        "helper differently and the cached file points at a no-longer-valid "
        "helper path",
    ),
    trigger_conditions=(
        "A Babel plugin is upgraded in a way that changes its emit but not "
        "its package.json version (workspaces, lockfile resolution shifts)",
        "babel-jest's getCacheKey uses package version, not plugin content "
        "hash, so the cache survives the upgrade",
        "Jest's ScriptTransformer trusts the cache hit and returns the "
        "stale transformed source on the rerun",
    ),
    expected_answer={
        "error_origin": (
            "babel-jest's getCacheKey omitting plugin content hashes, "
            "letting Jest's ScriptTransformer return a stale cached transform"
        ),
        "bypass_reason": (
            "babel-core resolves plugins by version + name, but a lockfile "
            "change can swap implementations without bumping versions, so "
            "the cache key never invalidates"
        ),
        "correct_config": (
            "Add a postinstall hook that clears jest's cache "
            "(--clearCache) on dependency changes, or extend "
            "babel-jest's getCacheKey via a custom transformer that "
            "includes plugin file mtimes / content hashes"
        ),
    },
)


T_NEXT_TURBOPACK = TaskDef(
    task_id="support-mapping-dual-next-turbopack-resolve-001",
    suite="customer_escalation",
    task_type="support_code_mapping",
    difficulty="hard",
    duration=35,
    description=(
        "Map customer Next.js Turbopack monorepo workspace resolution divergence "
        "from Webpack to the next-config / Turbopack resolver boundary"
    ),
    prompt=(
        "A customer reports that their Next.js 14 monorepo build works under\n"
        "webpack (next dev) but fails under Turbopack (next dev --turbo) with\n"
        "'Module not found: Cannot resolve @internal/ui' for a workspace package\n"
        "they consume via pnpm. Their next.config.js sets transpilePackages and\n"
        "the webpack alias keeps working, but Turbopack's resolver does not see\n"
        "the package.\n\n"
        "Your task:\n"
        "1. Find where Next.js's webpack-config.ts adds the workspace package to\n"
        "   the module resolution path and applies transpilePackages.\n"
        "2. Find where Turbopack's resolver implementation handles workspace\n"
        "   roots and how it interprets pnpm's nested node_modules layout.\n"
        "3. Trace the cross-repo path: next next-server/dev start ->\n"
        "   loadConfig -> turbopack resolve plugin -> turbo-tasks-fs read ->\n"
        "   package.json walk. Cite each file and the function or attribute\n"
        "   responsible.\n"
        "4. Identify why pnpm's symlinked workspace layout (with .pnpm/ store)\n"
        "   resolves under webpack's enhanced-resolve but not under\n"
        "   Turbopack's default resolver, and what config option closes the gap."
    ),
    repos=(
        Repo("https://github.com/vercel/next.js", "v14.1.4", "next.js", "primary"),
        Repo("https://github.com/vercel/turbo", "v1.13.2", "turbo", "upstream"),
    ),
    languages=("typescript", "javascript", "rust"),
    total_loc=900_000,
    frameworks=("next.js", "turbopack", "pnpm"),
    multi_repo_pattern="investigate",
    repo_set_id="vercel-build-ecosystem",
    mcp_benefit_rationale=(
        "Module resolution semantics differ between Next.js's webpack config "
        "(transpilePackages alias) and Turbopack's Rust resolver. Tracing the "
        "divergence requires cross-repo reading of both pipelines."
    ),
    required_files=(
        GTFile("next.js/packages/next/src/build/webpack-config.ts", "next.js", 0.90),
        GTFile("next.js/packages/next/src/server/lib/turbopack-warning.ts", "next.js", 0.75),
        GTFile("turbo/crates/turbopack-ecmascript/src/references/esm/mod.rs", "turbo", 0.85),
        GTFile("turbo/crates/turbopack-resolve/src/resolve.rs", "turbo", 0.90),
    ),
    sufficient_files=(
        GTFile("next.js/packages/next/src/build/load-jsconfig.ts", "next.js", 0.70),
        GTFile("turbo/crates/turbopack-resolve/src/resolve_options_context.rs", "turbo", 0.70),
    ),
    error_chain=(
        "Next.js's webpack-config.ts injects an enhanced-resolve alias for "
        "transpilePackages so pnpm-symlinked workspace packages are visible "
        "to Webpack's resolver",
        "Turbopack uses its own Rust resolver in turbopack-resolve/resolve.rs "
        "with a resolve_options_context that does not honor "
        "transpilePackages by default",
        "When pnpm symlinks @internal/ui under apps/<app>/node_modules pointing "
        "to .pnpm/<store>/node_modules/@internal/ui, Webpack follows the "
        "symlink and loads the package",
        "Turbopack's resolver, lacking the transpilePackages bridge, treats "
        ".pnpm-style nested links as outside the workspace root and refuses "
        "to load them, raising 'Module not found'",
    ),
    trigger_conditions=(
        "The customer uses pnpm with workspaces and consumes a workspace "
        "package via transpilePackages in next.config.js",
        "Turbopack does not apply transpilePackages aliases (Next 14 limitation) "
        "so the resolver does not bridge the symlinked store",
        "Webpack's enhanced-resolve follows pnpm symlinks transparently while "
        "Turbopack's default resolver does not",
    ),
    expected_answer={
        "error_origin": (
            "turbopack-resolve/src/resolve.rs not honoring "
            "transpilePackages aliases that Next.js's webpack-config.ts "
            "applies for workspace packages"
        ),
        "bypass_reason": (
            "Turbopack's resolve_options_context does not bridge pnpm-symlinked "
            "workspace packages the way Webpack's enhanced-resolve does"
        ),
        "correct_config": (
            "Pin the workspace package via experimental.turbo.resolveAlias in "
            "next.config.js, or move the package to a normal dependency until "
            "Turbopack adopts transpilePackages parity"
        ),
    },
)


# ---------------------------------------------------------------------------
# Java (4) — customer_escalation / support_code_mapping
# ---------------------------------------------------------------------------


T_SPRING_HIBERNATE = TaskDef(
    task_id="support-mapping-dual-spring-hibernate-flush-001",
    suite="customer_escalation",
    task_type="support_code_mapping",
    difficulty="hard",
    duration=35,
    description=(
        "Map customer Spring Boot @Transactional updates not flushing before "
        "save to the Spring TransactionTemplate / Hibernate Session.flush boundary"
    ),
    prompt=(
        "A customer reports that their Spring Boot 3 service writes are\n"
        "occasionally invisible to a downstream read inside the same\n"
        "@Transactional method. The trace shows a save() call followed by a\n"
        "JDBC query against the same row that returns the OLD value, even though\n"
        "Hibernate's first-level cache shows the new entity. The customer\n"
        "expected the dirty entity to be flushed before the query.\n\n"
        "Your task:\n"
        "1. Find where Spring's TransactionTemplate / @Transactional configures\n"
        "   the JpaTransactionManager and how it triggers flush at synchronization\n"
        "   points.\n"
        "2. Find where Hibernate's Session.flush decides whether to write\n"
        "   pending changes to the database (FlushMode.AUTO vs MANUAL) and how\n"
        "   it interacts with native query execution.\n"
        "3. Trace the cross-repo path: Spring TransactionTemplate.execute ->\n"
        "   PlatformTransactionManager beforeCommit -> JpaTransactionManager\n"
        "   -> EntityManager flush -> Hibernate SessionImpl.flush ->\n"
        "   ActionQueue execute. Cite each file and the function or attribute\n"
        "   responsible.\n"
        "4. Identify why a native SQL query inside the same transaction does\n"
        "   not auto-flush pending changes the way HQL does, and what\n"
        "   FlushMode setting closes the gap."
    ),
    repos=(
        Repo("https://github.com/spring-projects/spring-framework", "v6.1.4", "spring-framework", "primary"),
        Repo("https://github.com/hibernate/hibernate-orm", "6.4.4", "hibernate-orm", "upstream"),
    ),
    languages=("java",),
    total_loc=1_500_000,
    frameworks=("spring", "hibernate", "jpa"),
    multi_repo_pattern="investigate",
    repo_set_id="spring-hibernate-ecosystem",
    mcp_benefit_rationale=(
        "Spring's @Transactional and Hibernate's flush semantics interact in "
        "non-obvious ways for native queries. Cross-repo navigation is "
        "required to trace why a save+native-read sees stale data."
    ),
    required_files=(
        GTFile("spring-framework/spring-orm/src/main/java/org/springframework/orm/jpa/JpaTransactionManager.java", "spring-framework", 0.95),
        GTFile("spring-framework/spring-tx/src/main/java/org/springframework/transaction/support/TransactionTemplate.java", "spring-framework", 0.85),
        GTFile("hibernate-orm/hibernate-core/src/main/java/org/hibernate/internal/SessionImpl.java", "hibernate-orm", 0.95),
        GTFile("hibernate-orm/hibernate-core/src/main/java/org/hibernate/event/internal/DefaultAutoFlushEventListener.java", "hibernate-orm", 0.85),
    ),
    sufficient_files=(
        GTFile("spring-framework/spring-orm/src/main/java/org/springframework/orm/jpa/EntityManagerFactoryUtils.java", "spring-framework", 0.70),
        GTFile("hibernate-orm/hibernate-core/src/main/java/org/hibernate/engine/spi/ActionQueue.java", "hibernate-orm", 0.75),
    ),
    error_chain=(
        "Spring's TransactionTemplate wraps the operation in a JTA-style "
        "transaction via JpaTransactionManager which delegates flush "
        "decisions to the EntityManager",
        "Hibernate's SessionImpl owns the dirty-entity action queue; an HQL "
        "query triggers DefaultAutoFlushEventListener which flushes pending "
        "actions before the query runs",
        "A native SQL query path bypasses DefaultAutoFlushEventListener "
        "because Hibernate cannot determine which entities the SQL touches; "
        "FlushMode.AUTO does not apply to native queries",
        "The native query reads the database directly, sees the pre-save "
        "value, and Hibernate's first-level cache still holds the new value; "
        "the transaction commits with the discrepancy invisible until "
        "downstream code re-reads",
    ),
    trigger_conditions=(
        "Customer's @Transactional method calls save() then immediately "
        "executes a native query (entityManager.createNativeQuery)",
        "Hibernate's auto-flush listener does not fire for native queries, "
        "so dirty entities are not written before the SQL executes",
        "Spring's JpaTransactionManager defers the flush to commit, leaving "
        "the in-method native read against stale persistent state",
    ),
    expected_answer={
        "error_origin": (
            "Hibernate's DefaultAutoFlushEventListener not firing for native "
            "SQL paths in SessionImpl"
        ),
        "bypass_reason": (
            "Spring's JpaTransactionManager only flushes at commit; it does "
            "not insert a flush before a native query because Hibernate's "
            "auto-flush is the contract"
        ),
        "correct_config": (
            "Either explicitly call entityManager.flush() before the native "
            "query, or set FlushMode.ALWAYS on the query, or restructure to "
            "use HQL/Criteria so Hibernate's auto-flush fires"
        ),
    },
)


T_LOG4J_SLF4J = TaskDef(
    task_id="support-mapping-dual-log4j-slf4j-async-001",
    suite="customer_escalation",
    task_type="support_code_mapping",
    difficulty="hard",
    duration=35,
    description=(
        "Map customer async log lines lost under high load to the slf4j Logger / "
        "log4j2 AsyncAppender disruptor binding boundary"
    ),
    prompt=(
        "A customer reports that under sustained burst load their service\n"
        "loses 1-2% of log lines that were written via the slf4j Logger API.\n"
        "Logs from synchronous critical paths show fine; only the async\n"
        "channels (driven by log4j2 AsyncAppender) are dropping. Their config\n"
        "uses log4j-slf4j-impl as the binding.\n\n"
        "Your task:\n"
        "1. Find where slf4j routes a Logger.info() call through its binding\n"
        "   API to a log4j2 implementation.\n"
        "2. Find where log4j2 AsyncAppender wraps an LMAX Disruptor ring buffer\n"
        "   and its overflow / blocking policy.\n"
        "3. Trace the cross-repo path: slf4j LoggerFactory -> log4j-slf4j-impl\n"
        "   Log4jLogger -> log4j2 AsyncAppender -> Disruptor publish -> ring\n"
        "   buffer overflow policy. Cite each file and the function or\n"
        "   attribute responsible.\n"
        "4. Identify why the default Disruptor ring-buffer-full policy is\n"
        "   non-blocking with a configurable async-queue-full-policy that\n"
        "   silently drops events under burst load."
    ),
    repos=(
        Repo("https://github.com/qos-ch/slf4j", "v_2.0.13", "slf4j", "primary"),
        Repo("https://github.com/apache/logging-log4j2", "rel/2.23.1", "log4j2", "upstream"),
    ),
    languages=("java",),
    total_loc=350_000,
    frameworks=("slf4j", "log4j2", "disruptor"),
    multi_repo_pattern="investigate",
    repo_set_id="java-logging-ecosystem",
    mcp_benefit_rationale=(
        "Async log loss is split between slf4j's binding indirection and "
        "log4j2's AsyncAppender ring-buffer policy. Tracing the chain "
        "requires reading both repos."
    ),
    required_files=(
        GTFile("slf4j/slf4j-api/src/main/java/org/slf4j/LoggerFactory.java", "slf4j", 0.90),
        GTFile("slf4j/slf4j-api/src/main/java/org/slf4j/Logger.java", "slf4j", 0.80),
        GTFile("log4j2/log4j-core/src/main/java/org/apache/logging/log4j/core/appender/AsyncAppender.java", "log4j2", 0.95),
        GTFile("log4j2/log4j-core/src/main/java/org/apache/logging/log4j/core/async/AsyncLoggerConfigDisruptor.java", "log4j2", 0.85),
    ),
    sufficient_files=(
        GTFile("slf4j/slf4j-api/src/main/java/org/slf4j/spi/LocationAwareLogger.java", "slf4j", 0.70),
        GTFile("log4j2/log4j-core/src/main/java/org/apache/logging/log4j/core/async/AsyncQueueFullPolicy.java", "log4j2", 0.85),
    ),
    error_chain=(
        "slf4j's LoggerFactory binds at runtime to log4j-slf4j-impl which "
        "exposes a Log4jLogger that forwards each call to log4j2's "
        "AbstractLogger.logMessage",
        "When the appender chain includes AsyncAppender, log events enter a "
        "blocking queue or LMAX Disruptor ring depending on configuration",
        "log4j2's AsyncAppender uses AsyncQueueFullPolicy to decide what to "
        "do when the queue is full; the default Discard policy drops events "
        "below ERROR level",
        "Under burst load the ring buffer fills, AsyncQueueFullPolicy.Discard "
        "drops INFO/DEBUG events silently, and slf4j callers see "
        "Logger.info() return without a thrown exception",
    ),
    trigger_conditions=(
        "Customer's log4j2 config uses AsyncAppender or AsyncLoggerConfig "
        "with the default discard policy",
        "Sustained burst load fills the Disruptor ring beyond ringBufferSize",
        "The discard policy drops sub-ERROR events without error reporting "
        "back to slf4j's Logger.info caller",
    ),
    expected_answer={
        "error_origin": (
            "log4j2 AsyncQueueFullPolicy.Discard dropping sub-ERROR events "
            "when the AsyncAppender ring buffer is full"
        ),
        "bypass_reason": (
            "slf4j's binding API has no return contract for delivery; "
            "log4j2's discard policy silently drops events at the appender "
            "rather than back-pressuring callers"
        ),
        "correct_config": (
            "Set log4j2.AsyncQueueFullPolicy=Default (which blocks the "
            "caller) or increase ringBufferSize via "
            "AsyncLoggerConfig.RingBufferSize, accepting back-pressure on "
            "the slf4j caller path"
        ),
    },
)


T_JACKSON_SPRING = TaskDef(
    task_id="support-mapping-dual-jackson-spring-deserializer-001",
    suite="customer_escalation",
    task_type="support_code_mapping",
    difficulty="hard",
    duration=35,
    description=(
        "Map customer custom Jackson @JsonDeserializer being ignored under "
        "Spring Boot ObjectMapper auto-config to the SimpleModule registration "
        "boundary"
    ),
    prompt=(
        "A customer reports that their custom Jackson @JsonDeserializer for a\n"
        "money type is silently ignored when Spring Boot deserializes a request\n"
        "body. Calling objectMapper.readValue directly from a unit test works,\n"
        "but the Spring MVC controller path uses the deserializer Jackson ships\n"
        "by default. They expected the @JsonComponent annotation on their\n"
        "deserializer to register it.\n\n"
        "Your task:\n"
        "1. Find where Spring Boot's JacksonAutoConfiguration builds the\n"
        "   ObjectMapper bean, including how it discovers @JsonComponent and\n"
        "   integrates it via SimpleModule.\n"
        "2. Find where Jackson's ObjectMapper resolves a deserializer for a\n"
        "   property at deserialization time, and the precedence order between\n"
        "   per-property annotations, registered modules, and BeanDeserializerFactory.\n"
        "3. Trace the cross-repo path: Spring MVC HandlerMethodArgumentResolver\n"
        "   -> Spring's MappingJackson2HttpMessageConverter -> Jackson\n"
        "   ObjectMapper.readValue -> BeanDeserializerFactory ->\n"
        "   SimpleModule deserializer cache. Cite each file and the function\n"
        "   responsible.\n"
        "4. Identify why @JsonComponent registers the deserializer on the\n"
        "   primary ObjectMapper bean only, and how a second ObjectMapper bean\n"
        "   (for example for caching or messaging) bypasses the registration."
    ),
    repos=(
        Repo("https://github.com/spring-projects/spring-boot", "v3.2.4", "spring-boot", "primary"),
        Repo("https://github.com/FasterXML/jackson-databind", "jackson-databind-2.16.2", "jackson-databind", "upstream"),
    ),
    languages=("java",),
    total_loc=1_400_000,
    frameworks=("spring-boot", "jackson", "jackson-databind"),
    multi_repo_pattern="investigate",
    repo_set_id="spring-jackson-ecosystem",
    mcp_benefit_rationale=(
        "Custom deserializer registration is split between Spring Boot's "
        "JacksonAutoConfiguration and Jackson's deserializer factory. "
        "Tracing the silent ignore requires cross-repo navigation."
    ),
    required_files=(
        GTFile("spring-boot/spring-boot-project/spring-boot-autoconfigure/src/main/java/org/springframework/boot/autoconfigure/jackson/JacksonAutoConfiguration.java", "spring-boot", 0.95),
        GTFile("spring-boot/spring-boot-project/spring-boot-autoconfigure/src/main/java/org/springframework/boot/autoconfigure/jackson/Jackson2ObjectMapperBuilderCustomizer.java", "spring-boot", 0.80),
        GTFile("jackson-databind/src/main/java/com/fasterxml/jackson/databind/ObjectMapper.java", "jackson-databind", 0.90),
        GTFile("jackson-databind/src/main/java/com/fasterxml/jackson/databind/deser/BeanDeserializerFactory.java", "jackson-databind", 0.90),
    ),
    sufficient_files=(
        GTFile("spring-boot/spring-boot-project/spring-boot/src/main/java/org/springframework/boot/jackson/JsonComponentModule.java", "spring-boot", 0.85),
        GTFile("jackson-databind/src/main/java/com/fasterxml/jackson/databind/module/SimpleModule.java", "jackson-databind", 0.80),
    ),
    error_chain=(
        "Spring Boot's JacksonAutoConfiguration creates the primary "
        "ObjectMapper bean and discovers @JsonComponent via "
        "JsonComponentModule, registering each as a SimpleModule on the bean",
        "Jackson's ObjectMapper applies registered modules in registration "
        "order; SimpleModule.setupModule writes deserializers into the "
        "deserializer cache",
        "BeanDeserializerFactory consults the cache when resolving the "
        "deserializer for a property; if the property type matches, the "
        "module's deserializer wins over the default",
        "When a second ObjectMapper bean is created (cache, RabbitMQ, etc.) "
        "without invoking JacksonAutoConfiguration's customizer chain, the "
        "@JsonComponent module is never registered on it",
        "Spring MVC's MappingJackson2HttpMessageConverter is wired to the "
        "primary ObjectMapper, but if the customer's controller is configured "
        "with a different converter or sub-context, the wrong ObjectMapper "
        "deserializes the body",
    ),
    trigger_conditions=(
        "Customer has multiple ObjectMapper beans in the context, only the "
        "primary one wired by JacksonAutoConfiguration includes JsonComponentModule",
        "@JsonComponent registration is one-shot at the primary ObjectMapper; "
        "manually constructed ObjectMapper instances bypass it",
        "Jackson's BeanDeserializerFactory falls back to default deserializers "
        "silently when the module is not registered",
    ),
    expected_answer={
        "error_origin": (
            "Spring Boot's JsonComponentModule registering only on the "
            "primary ObjectMapper; secondary ObjectMappers fall back to "
            "Jackson's BeanDeserializerFactory defaults"
        ),
        "bypass_reason": (
            "Jackson's deserializer resolution checks registered modules "
            "first; without the module the default deserializer wins silently"
        ),
        "correct_config": (
            "Inject the primary ObjectMapper into all message converters, or "
            "register the deserializer module manually on every ObjectMapper "
            "bean via Jackson2ObjectMapperBuilder.modulesToInstall"
        ),
    },
)


T_SPRING_KAFKA_REBALANCE = TaskDef(
    task_id="support-mapping-dual-spring-kafka-rebalance-002",
    suite="customer_escalation",
    task_type="support_code_mapping",
    difficulty="hard",
    duration=35,
    description=(
        "Map customer Spring Kafka listener acks lost during consumer rebalance "
        "to the KafkaListener / ConsumerRebalanceListener ack-mode boundary"
    ),
    prompt=(
        "A customer reports that their Spring Kafka listener occasionally\n"
        "double-processes messages whenever the consumer group rebalances. The\n"
        "logs show acks fired by the listener, but Kafka shows the offsets\n"
        "were not committed before the partition was revoked.\n\n"
        "Your task:\n"
        "1. Find where Spring Kafka's KafkaMessageListenerContainer wires its\n"
        "   ConsumerRebalanceListener and how it interacts with the configured\n"
        "   ack-mode (BATCH, RECORD, MANUAL_IMMEDIATE).\n"
        "2. Find where Apache Kafka's clients module enforces the\n"
        "   ConsumerRebalanceListener.onPartitionsRevoked contract and the\n"
        "   commit semantics around it.\n"
        "3. Trace the cross-repo path: Spring KafkaListener -> ListenerConsumer\n"
        "   poll loop -> rebalance event -> ConsumerCoordinator.invokeOnPartitionsRevoked\n"
        "   -> Spring's ack queue flush -> commit. Cite each file and the\n"
        "   function or attribute responsible.\n"
        "4. Identify why ack-mode BATCH defers commit until after the listener\n"
        "   batch returns, and why this can race with onPartitionsRevoked,\n"
        "   leaving commit-pending records double-processed after rebalance."
    ),
    repos=(
        Repo("https://github.com/spring-projects/spring-kafka", "v3.1.4", "spring-kafka", "primary"),
        Repo("https://github.com/apache/kafka", "3.6.1", "kafka", "upstream"),
    ),
    languages=("java",),
    total_loc=2_500_000,
    frameworks=("spring-kafka", "kafka", "kafka-clients"),
    multi_repo_pattern="investigate",
    repo_set_id="spring-kafka-clients-ecosystem",
    mcp_benefit_rationale=(
        "Rebalance ack-loss is split between Spring Kafka's ack-mode policy "
        "and Kafka's ConsumerCoordinator timing. Tracing the race requires "
        "reading both repos' rebalance flows."
    ),
    required_files=(
        GTFile("spring-kafka/spring-kafka/src/main/java/org/springframework/kafka/listener/KafkaMessageListenerContainer.java", "spring-kafka", 0.95),
        GTFile("spring-kafka/spring-kafka/src/main/java/org/springframework/kafka/listener/ContainerProperties.java", "spring-kafka", 0.80),
        GTFile("kafka/clients/src/main/java/org/apache/kafka/clients/consumer/internals/ConsumerCoordinator.java", "kafka", 0.95),
        GTFile("kafka/clients/src/main/java/org/apache/kafka/clients/consumer/KafkaConsumer.java", "kafka", 0.85),
    ),
    sufficient_files=(
        GTFile("spring-kafka/spring-kafka/src/main/java/org/springframework/kafka/listener/AcknowledgingMessageListener.java", "spring-kafka", 0.75),
        GTFile("kafka/clients/src/main/java/org/apache/kafka/clients/consumer/ConsumerRebalanceListener.java", "kafka", 0.70),
    ),
    error_chain=(
        "Spring Kafka's ListenerConsumer poll loop in "
        "KafkaMessageListenerContainer accumulates pending acks based on "
        "ContainerProperties.ackMode (BATCH, RECORD, etc.)",
        "Kafka's ConsumerCoordinator detects rebalance and invokes "
        "ConsumerRebalanceListener.onPartitionsRevoked before assigning new "
        "partitions",
        "With ackMode=BATCH, Spring defers commit until after the listener "
        "batch returns; if rebalance fires mid-batch, the deferred acks are "
        "discarded when the partition is revoked",
        "Kafka's ConsumerCoordinator commits any acked offsets in "
        "onPartitionsRevoked, but Spring's pending ack queue is unflushed, "
        "so those records appear unacked and are reprocessed by the new owner",
    ),
    trigger_conditions=(
        "ackMode is set to BATCH (default) and a consumer group rebalance "
        "fires while a batch is still being acked",
        "Spring's pending-ack queue has not been flushed to KafkaConsumer.commitSync "
        "by the time onPartitionsRevoked runs",
        "Kafka's ConsumerCoordinator commits only what it sees in its own "
        "in-memory offset map; Spring's queued acks never made it there",
    ),
    expected_answer={
        "error_origin": (
            "Spring Kafka's ackMode=BATCH deferring commit until end-of-batch, "
            "racing with Kafka's ConsumerCoordinator.onPartitionsRevoked"
        ),
        "bypass_reason": (
            "Kafka's ConsumerCoordinator commits only what KafkaConsumer "
            "knows about; Spring's deferred ack queue is invisible to it"
        ),
        "correct_config": (
            "Set ackMode=RECORD or MANUAL_IMMEDIATE so each ack is committed "
            "to Kafka before the next poll, eliminating the rebalance race "
            "window; alternatively, hook ConsumerRebalanceListener.onPartitionsRevoked "
            "to flush Spring's pending queue"
        ),
    },
)


# ---------------------------------------------------------------------------
# Rust (3) — customer_escalation / support_code_mapping
# ---------------------------------------------------------------------------


T_TOKIO_HYPER = TaskDef(
    task_id="support-mapping-dual-tokio-hyper-graceful-001",
    suite="customer_escalation",
    task_type="support_code_mapping",
    difficulty="hard",
    duration=35,
    description=(
        "Map customer hyper server hanging on graceful shutdown when tokio "
        "runtime drains to the hyper Server::with_graceful_shutdown / tokio "
        "runtime drain boundary"
    ),
    prompt=(
        "A customer reports that their hyper-based service hangs for 60 seconds\n"
        "on SIGTERM during deployment. The signal handler triggers\n"
        "Server::with_graceful_shutdown's shutdown_signal future, but the\n"
        "process refuses to exit until the kubernetes terminationGracePeriod\n"
        "kills it. They observe one in-flight long-poll request that never\n"
        "drops.\n\n"
        "Your task:\n"
        "1. Find where hyper's Server type implements with_graceful_shutdown\n"
        "   and the connection-tracking it does for in-flight requests.\n"
        "2. Find where tokio's runtime drains tasks on Runtime::shutdown or\n"
        "   when the runtime handle is dropped, and how it interacts with\n"
        "   tasks holding I/O resources.\n"
        "3. Trace the cross-repo path: tokio::signal::ctrl_c future ->\n"
        "   hyper Server::with_graceful_shutdown future select -> hyper\n"
        "   Connection::poll close -> tokio reactor I/O cancellation. Cite\n"
        "   each file and the function or attribute responsible.\n"
        "4. Identify why an in-flight HTTP/1.1 long-poll request held in a\n"
        "   tokio task is not interrupted by hyper's graceful shutdown,\n"
        "   because hyper waits for the response body to complete and tokio\n"
        "   cannot force-cancel the task without dropping the runtime."
    ),
    repos=(
        Repo("https://github.com/hyperium/hyper", "v1.2.0", "hyper", "primary"),
        Repo("https://github.com/tokio-rs/tokio", "tokio-1.36.0", "tokio", "upstream"),
    ),
    languages=("rust",),
    total_loc=250_000,
    frameworks=("hyper", "tokio", "tower"),
    multi_repo_pattern="investigate",
    repo_set_id="rust-async-ecosystem",
    mcp_benefit_rationale=(
        "Graceful shutdown is split between hyper's connection-tracking and "
        "tokio's task cancellation semantics. The shutdown hang requires "
        "reading both repos to see why long-poll never drops."
    ),
    required_files=(
        GTFile("hyper/src/server/conn/http1.rs", "hyper", 0.90),
        GTFile("hyper/src/proto/h1/conn.rs", "hyper", 0.85),
        GTFile("tokio/tokio/src/runtime/runtime.rs", "tokio", 0.85),
        GTFile("tokio/tokio/src/task/join_set.rs", "tokio", 0.80),
    ),
    sufficient_files=(
        GTFile("hyper/src/proto/h1/dispatch.rs", "hyper", 0.75),
        GTFile("tokio/tokio/src/signal/unix.rs", "tokio", 0.70),
    ),
    error_chain=(
        "Customer wires Server::with_graceful_shutdown to a tokio::signal "
        "future; SIGTERM resolves the future, hyper stops accepting new "
        "connections",
        "hyper's Connection::poll for HTTP/1 in proto/h1/conn.rs continues "
        "draining the body of in-flight requests because graceful shutdown "
        "only declines new requests, not active ones",
        "The long-poll request is parked on tokio's reactor inside a tokio "
        "task; hyper's graceful path awaits Connection::shutdown which only "
        "completes when the response body finishes",
        "tokio's runtime on shutdown_timeout drops still-running tasks but "
        "the long-poll task is not in a JoinSet hyper can cancel; the "
        "container terminationGracePeriod kicks in and SIGKILLs the process",
    ),
    trigger_conditions=(
        "Server::with_graceful_shutdown is wired to a tokio::signal future "
        "and the request handler holds a long-running future without "
        "an explicit cancellation token",
        "hyper's HTTP/1 graceful shutdown only declines new connections; "
        "active connections must complete naturally",
        "tokio's runtime cannot force-cancel a parked task without dropping "
        "the runtime, so the long-poll task survives the shutdown signal",
    ),
    expected_answer={
        "error_origin": (
            "hyper's HTTP/1 graceful path waits for Connection::shutdown "
            "completion; the long-poll task remains parked on tokio's "
            "reactor and tokio cannot force-cancel it"
        ),
        "bypass_reason": (
            "hyper's graceful_shutdown only stops accepting new connections, "
            "not in-flight ones; tokio's task cancellation is cooperative"
        ),
        "correct_config": (
            "Pass an explicit cancellation token (tokio_util::CancellationToken) "
            "into the request handler so it can short-circuit on shutdown, "
            "or use Runtime::shutdown_timeout with a forced drop after "
            "graceful_shutdown completes"
        ),
    },
)


T_AXUM_TOWER = TaskDef(
    task_id="support-mapping-dual-axum-tower-middleware-001",
    suite="customer_escalation",
    task_type="support_code_mapping",
    difficulty="hard",
    duration=35,
    description=(
        "Map customer axum auth middleware bypassed for tracing-instrumented "
        "routes to the Router / tower ServiceBuilder layer-order boundary"
    ),
    prompt=(
        "A customer reports that their axum service has an auth middleware\n"
        "that runs on most routes but is silently bypassed for routes nested\n"
        "under a Router that wraps a tracing layer. Requests to those routes\n"
        "succeed without the Authorization header; the auth layer never\n"
        "produces a span.\n\n"
        "Your task:\n"
        "1. Find where axum's Router applies layers and how nested Routers\n"
        "   compose with tower::ServiceBuilder layers.\n"
        "2. Find where tower::ServiceBuilder enforces layer order and how\n"
        "   .layer() differs from .route_layer() in axum.\n"
        "3. Trace the cross-repo path: axum::Router::route_layer vs\n"
        "   axum::Router::layer -> Router::merge -> tower::ServiceBuilder ->\n"
        "   tower::Layer::layer composition. Cite each file and the function\n"
        "   responsible.\n"
        "4. Identify why route_layer() applied to a parent Router does NOT\n"
        "   wrap routes added via Router::merge from a sub-router, leaving\n"
        "   the auth layer skipped for nested routes."
    ),
    repos=(
        Repo("https://github.com/tokio-rs/axum", "axum-v0.7.5", "axum", "primary"),
        Repo("https://github.com/tower-rs/tower", "tower-0.4.13", "tower", "upstream"),
    ),
    languages=("rust",),
    total_loc=180_000,
    frameworks=("axum", "tower", "hyper"),
    multi_repo_pattern="investigate",
    repo_set_id="rust-axum-tower-ecosystem",
    mcp_benefit_rationale=(
        "Layer composition is split between axum's Router merge semantics "
        "and tower's ServiceBuilder. The bypass requires cross-repo "
        "navigation to see why route_layer skips merged routes."
    ),
    required_files=(
        GTFile("axum/axum/src/routing/mod.rs", "axum", 0.95),
        GTFile("axum/axum/src/routing/path_router.rs", "axum", 0.85),
        GTFile("tower/tower/src/builder/mod.rs", "tower", 0.90),
        GTFile("tower/tower/src/layer/layer_fn.rs", "tower", 0.75),
    ),
    sufficient_files=(
        GTFile("axum/axum/src/middleware/from_fn.rs", "axum", 0.75),
        GTFile("tower/tower-layer/src/lib.rs", "tower", 0.70),
    ),
    error_chain=(
        "axum's Router stores routes in a path_router and applies layer() "
        "globally to the underlying service stack",
        "route_layer() in axum's Router differs: it wraps only routes "
        "currently registered, not routes added later via merge",
        "tower's ServiceBuilder composes layers in declaration order; the "
        "wrapped Service is built from the inner-most layer outward",
        "When the customer calls Router::merge(sub_router) after "
        "Router::route_layer(auth), axum's path_router merges sub_router's "
        "routes into the parent without re-applying route_layer, leaving "
        "those endpoints unwrapped by the auth layer",
    ),
    trigger_conditions=(
        "Customer's code uses Router::route_layer(auth_layer) followed by "
        "Router::merge(sub_router) to attach modular routes",
        "axum's route_layer applies only to routes registered before the "
        "merge call; merged routes bypass it",
        "tower's ServiceBuilder is unaware of axum's per-route routing "
        "decisions, so the Layer is effectively absent for the merged subset",
    ),
    expected_answer={
        "error_origin": (
            "axum's Router::route_layer does not wrap routes added later "
            "via Router::merge; tower's ServiceBuilder layer is never "
            "composed for those routes"
        ),
        "bypass_reason": (
            "route_layer() only applies to routes already in the Router; "
            "merge() adds new routes outside that wrap"
        ),
        "correct_config": (
            "Use Router::layer (global) instead of route_layer for auth, "
            "or apply route_layer AFTER all merges, or use middleware::from_fn "
            "on the merged sub-router so the layer travels with its routes"
        ),
    },
)


T_TONIC_TOKIO = TaskDef(
    task_id="support-mapping-dual-tonic-tokio-streamcancel-001",
    suite="customer_escalation",
    task_type="support_code_mapping",
    difficulty="hard",
    duration=35,
    description=(
        "Map customer tonic gRPC client stream stuck after server cancel to "
        "the tonic Streaming / tokio mpsc cancellation boundary"
    ),
    prompt=(
        "A customer reports that their tonic-based gRPC client occasionally\n"
        "hangs forever on the next() call of a server-streaming RPC after the\n"
        "server explicitly cancels the stream with Status::cancelled(). The\n"
        "underlying HTTP/2 RST_STREAM is observed in tcpdump, but the\n"
        "Streaming<T> never returns None.\n\n"
        "Your task:\n"
        "1. Find where tonic's Streaming type wraps the inner mpsc channel\n"
        "   and how it converts a Status::cancelled into a stream end.\n"
        "2. Find where tokio's mpsc Receiver decides between Some(item) and\n"
        "   None on a closed channel, and how it interacts with sender drop\n"
        "   ordering.\n"
        "3. Trace the cross-repo path: tonic Streaming::poll_next -> mpsc\n"
        "   Receiver::poll_recv -> server-side Status::cancelled emit ->\n"
        "   sender drop -> Receiver close. Cite each file and the function\n"
        "   responsible.\n"
        "4. Identify why a Status::cancelled body frame on the wire does\n"
        "   not always close tonic's mpsc sender immediately when h2 stream\n"
        "   reset arrives at the client, and how an outstanding clone of\n"
        "   the sender keeps the client receiver hanging."
    ),
    repos=(
        Repo("https://github.com/hyperium/tonic", "v0.11.0", "tonic", "primary"),
        Repo("https://github.com/tokio-rs/tokio", "tokio-1.36.0", "tokio", "upstream"),
    ),
    languages=("rust",),
    total_loc=300_000,
    frameworks=("tonic", "tokio", "h2"),
    multi_repo_pattern="investigate",
    repo_set_id="rust-tonic-tokio-ecosystem",
    mcp_benefit_rationale=(
        "Cancel propagation is split between tonic's gRPC layer and tokio's "
        "mpsc semantics. The hang requires cross-repo navigation to see why "
        "stream RST does not unblock the receiver."
    ),
    required_files=(
        GTFile("tonic/tonic/src/codec/decode.rs", "tonic", 0.90),
        GTFile("tonic/tonic/src/transport/channel/mod.rs", "tonic", 0.80),
        GTFile("tokio/tokio/src/sync/mpsc/chan.rs", "tokio", 0.95),
        GTFile("tokio/tokio/src/sync/mpsc/bounded.rs", "tokio", 0.85),
    ),
    sufficient_files=(
        GTFile("tonic/tonic/src/client/grpc.rs", "tonic", 0.75),
        GTFile("tokio/tokio/src/sync/mpsc/list.rs", "tokio", 0.70),
    ),
    error_chain=(
        "Server emits Status::cancelled which becomes an HTTP/2 RST_STREAM "
        "or trailers frame; tonic's decode.rs translates trailers into "
        "either Ok(None) or Err(Status)",
        "tonic's transport channel feeds decoded frames into a tokio "
        "mpsc::channel where the client's Streaming receiver awaits",
        "tokio's mpsc channel in chan.rs only signals receiver close when "
        "ALL senders are dropped; if a clone of the sender lives in the "
        "h2 task, the receiver continues blocking",
        "When the RST_STREAM arrives, tonic's transport task may not drop "
        "all sender clones synchronously, leaving Streaming::poll_next "
        "parked on the mpsc despite the wire cancel",
    ),
    trigger_conditions=(
        "Server emits Status::cancelled mid-stream and the wire RST_STREAM "
        "races with tonic's transport task dropping its mpsc sender",
        "tokio's mpsc only closes when sender refcount hits zero; an "
        "outstanding clone (in a tracing span, a retry layer, etc.) keeps "
        "it alive",
        "tonic's Streaming::poll_next maps an open mpsc to Pending, even "
        "though the gRPC stream is logically cancelled",
    ),
    expected_answer={
        "error_origin": (
            "tonic's transport task holds a clone of the mpsc sender that "
            "outlives the RST_STREAM, so tokio's mpsc Receiver never sees "
            "channel-closed"
        ),
        "bypass_reason": (
            "tokio's mpsc close is sender-refcount-driven; tonic's RST "
            "handling does not synchronously drop all clones"
        ),
        "correct_config": (
            "Wrap the client stream in tokio::time::timeout, or upgrade "
            "tonic to a release that drops the transport task's sender "
            "promptly on RST_STREAM, or use a separate cancellation "
            "channel that the client polls alongside the stream"
        ),
    },
)


# ---------------------------------------------------------------------------
# C++ / mixed (3) — customer_escalation / support_code_mapping
# ---------------------------------------------------------------------------


T_ENVOY_ISTIO = TaskDef(
    task_id="support-mapping-dual-envoy-istio-xds-001",
    suite="customer_escalation",
    task_type="support_code_mapping",
    difficulty="expert",
    duration=40,
    description=(
        "Map customer Istio listener drain timing 503 spikes during xDS push "
        "to the Envoy ListenerManager / Istio Pilot ADS push-order boundary"
    ),
    prompt=(
        "A customer reports that during routine Istio Pilot config pushes their\n"
        "service mesh sees brief 503 spikes for ~5 seconds on existing\n"
        "connections, even for unchanged listeners. The spikes correlate with\n"
        "ADS push events; the listener_manager logs show drain_in_progress\n"
        "but no listener config actually changed.\n\n"
        "Your task:\n"
        "1. Find where Envoy's ListenerManager processes an LDS update and\n"
        "   decides whether to drain existing listeners.\n"
        "2. Find where Istio Pilot generates the ADS resource set and the\n"
        "   ordering between LDS, RDS, CDS, and EDS resources for a config\n"
        "   change.\n"
        "3. Trace the cross-repo path: Pilot ConfigGenerator -> ADS Push ->\n"
        "   Envoy XdsManager -> ListenerManager addOrUpdateListener ->\n"
        "   ConnectionHandler drain. Cite each file and the function or\n"
        "   attribute responsible.\n"
        "4. Identify why Pilot's full-snapshot ADS push (vs delta-xDS) can\n"
        "   send LDS resources with the same hash but a new version, causing\n"
        "   Envoy to re-process them and trigger a drain even when nothing\n"
        "   changed."
    ),
    repos=(
        Repo("https://github.com/envoyproxy/envoy", "v1.29.4", "envoy", "primary"),
        Repo("https://github.com/istio/istio", "1.21.1", "istio", "upstream"),
    ),
    languages=("cpp", "go"),
    total_loc=4_500_000,
    frameworks=("envoy", "istio", "xds", "grpc"),
    multi_repo_pattern="investigate",
    repo_set_id="envoy-istio-mesh-ecosystem",
    mcp_benefit_rationale=(
        "Drain spikes during xDS push are split between Envoy's listener "
        "diff logic (C++) and Istio Pilot's ADS push generator (Go). "
        "Cross-repo navigation across both languages is required."
    ),
    required_files=(
        GTFile("envoy/source/common/listener_manager/listener_manager_impl.cc", "envoy", 0.95),
        GTFile("envoy/source/common/listener_manager/listener_impl.cc", "envoy", 0.85),
        GTFile("istio/pilot/pkg/xds/ads.go", "istio", 0.95),
        GTFile("istio/pilot/pkg/xds/lds.go", "istio", 0.90),
    ),
    sufficient_files=(
        GTFile("envoy/source/extensions/config_subscription/grpc/grpc_mux_impl.cc", "envoy", 0.75),
        GTFile("istio/pilot/pkg/xds/discovery.go", "istio", 0.75),
    ),
    error_chain=(
        "Istio Pilot's ConfigGenerator builds a snapshot per push event; "
        "ads.go pushes LDS+RDS+CDS+EDS to subscribed Envoys via "
        "DeltaAggregatedResources or AggregatedResources",
        "lds.go computes listener resources per proxy and serializes them "
        "with a new version string even when content is unchanged, because "
        "the version is monotonic per push",
        "Envoy's grpc_mux_impl receives the snapshot and routes each "
        "listener resource into ListenerManager::addOrUpdateListener",
        "ListenerManager compares the listener config against the existing "
        "active listener; if Envoy considers them different (for example, "
        "by hash recompute mismatch), it triggers a drain via "
        "ConnectionHandler::drainListener",
        "During drain, in-flight requests on idle connections are reset "
        "with 503 from the connection's onCloseSocket path until the new "
        "listener takes over",
    ),
    trigger_conditions=(
        "Istio Pilot uses full-snapshot ADS (not delta-xDS) so version is "
        "bumped on every push regardless of content",
        "Envoy's ListenerManager re-evaluates listener identity on every "
        "LDS update; spurious version changes can trigger drain when hash "
        "comparison drifts",
        "ConnectionHandler::drainListener resets idle connections with 503 "
        "during the drain window, visible as the customer's 503 spike",
    ),
    expected_answer={
        "error_origin": (
            "Envoy's ListenerManager triggering drainListener on Pilot's "
            "monotonic-version LDS push even when listener content is "
            "unchanged"
        ),
        "bypass_reason": (
            "Pilot pushes full snapshots with a new version per event; "
            "Envoy's listener identity check is not strictly content-hash "
            "based"
        ),
        "correct_config": (
            "Enable delta-xDS (PILOT_ENABLE_DELTA_XDS=true) so Pilot only "
            "pushes changed resources, or tune Pilot's "
            "PILOT_PUSH_THROTTLE / debounce so unchanged listener pushes "
            "do not reach Envoy"
        ),
    },
)


T_GRPC_PROTOBUF = TaskDef(
    task_id="support-mapping-dual-grpc-protobuf-reflection-001",
    suite="customer_escalation",
    task_type="support_code_mapping",
    difficulty="hard",
    duration=35,
    description=(
        "Map customer gRPC reflection API regression after protobuf v3->v4 "
        "bump to the grpc-cpp ServerReflection / protobuf DescriptorPool boundary"
    ),
    prompt=(
        "A customer reports that after upgrading protobuf from 3.21 to 4.25\n"
        "their grpc-cpp service's reflection API returns empty descriptors\n"
        "for messages that previously worked. grpcurl -msg-template fails\n"
        "with 'message not found in registry'. The .proto files are\n"
        "unchanged.\n\n"
        "Your task:\n"
        "1. Find where grpc-cpp's ServerReflection service implements\n"
        "   FileContainingSymbol and how it queries the descriptor pool.\n"
        "2. Find where protobuf's DescriptorPool holds generated descriptors\n"
        "   and how the v4 API change to message_factory affects descriptor\n"
        "   visibility.\n"
        "3. Trace the cross-repo path: grpc::reflection::v1alpha::ServerReflection\n"
        "   -> grpc-cpp ProtoServerReflection -> protobuf DescriptorPool\n"
        "   ::generated_pool -> generated_message_table lookup. Cite each\n"
        "   file and the function or attribute responsible.\n"
        "4. Identify why protobuf v4 split the generated descriptor pool\n"
        "   per shared library, causing grpc-cpp's reflection lookup to miss\n"
        "   descriptors compiled into a different .so."
    ),
    repos=(
        Repo("https://github.com/grpc/grpc", "v1.62.0", "grpc", "primary"),
        Repo("https://github.com/protocolbuffers/protobuf", "v25.2", "protobuf", "upstream"),
    ),
    languages=("cpp",),
    total_loc=2_800_000,
    frameworks=("grpc", "protobuf"),
    multi_repo_pattern="investigate",
    repo_set_id="grpc-protobuf-cpp-ecosystem",
    mcp_benefit_rationale=(
        "Reflection regression is split between grpc-cpp's reflection "
        "service and protobuf's descriptor pool. The empty-descriptor bug "
        "requires cross-repo navigation across the language interop boundary."
    ),
    required_files=(
        GTFile("grpc/src/cpp/ext/proto_server_reflection.cc", "grpc", 0.95),
        GTFile("grpc/src/cpp/ext/proto_server_reflection.h", "grpc", 0.80),
        GTFile("protobuf/src/google/protobuf/descriptor.cc", "protobuf", 0.95),
        GTFile("protobuf/src/google/protobuf/descriptor.h", "protobuf", 0.85),
    ),
    sufficient_files=(
        GTFile("grpc/src/cpp/ext/proto_server_reflection_plugin.cc", "grpc", 0.75),
        GTFile("protobuf/src/google/protobuf/message.cc", "protobuf", 0.70),
    ),
    error_chain=(
        "grpc-cpp's ProtoServerReflection in proto_server_reflection.cc "
        "queries DescriptorPool::generated_pool() to enumerate registered "
        "files and symbols",
        "protobuf v4 changed how generated descriptors register: each "
        ".pb.cc registers into the generated_pool via a static initializer, "
        "but the pool is now sharded per ABI version",
        "If the customer's binary links proto-generated code from two "
        ".so's compiled with different protobuf headers, only one .so's "
        "descriptors land in the pool grpc-cpp queries",
        "ProtoServerReflection::FileContainingSymbol looks up by symbol "
        "name in DescriptorPool::FindFileContainingSymbol; missing entries "
        "return nullptr, surfaced to the client as 'message not found'",
    ),
    trigger_conditions=(
        "Customer's binary links protobuf-generated code from multiple "
        "shared libraries built against different protobuf header versions",
        "protobuf v4's descriptor pool sharding leaves only one .so's "
        "descriptors visible to generated_pool",
        "grpc-cpp's reflection service depends on a unified generated_pool "
        "and silently returns nullptr on miss",
    ),
    expected_answer={
        "error_origin": (
            "protobuf v4's per-ABI descriptor pool sharding leaving "
            "descriptors compiled into a different .so invisible to "
            "grpc-cpp's reflection lookup"
        ),
        "bypass_reason": (
            "grpc-cpp's ProtoServerReflection assumes a single unified "
            "generated_pool; protobuf v4 broke that assumption when ABI "
            "versions diverge across linked libraries"
        ),
        "correct_config": (
            "Rebuild all linked libraries against the same protobuf "
            "version, or register descriptors into a custom DescriptorPool "
            "passed to ProtoServerReflection::SetServiceList, or pin "
            "protobuf to a single shared library across the binary"
        ),
    },
)


T_OPENSSL_CURL = TaskDef(
    task_id="support-mapping-dual-openssl-curl-cipher-001",
    suite="customer_escalation",
    task_type="support_code_mapping",
    difficulty="hard",
    duration=35,
    description=(
        "Map customer curl HTTPS handshake failure after openssl 3.0 upgrade "
        "to the libcurl ossl backend / openssl provider security-level boundary"
    ),
    prompt=(
        "A customer reports that after their distro upgraded openssl from\n"
        "1.1.1 to 3.0, curl HTTPS calls to a partner endpoint suddenly fail\n"
        "with 'no cipher match' even though the partner supports modern\n"
        "ciphers. openssl s_client succeeds against the same host. The\n"
        "customer's curl was rebuilt against the new openssl.\n\n"
        "Your task:\n"
        "1. Find where libcurl's openssl backend (vtls/openssl.c) constructs\n"
        "   the SSL_CTX and applies cipher and protocol version constraints.\n"
        "2. Find where openssl 3.0's provider model evaluates the security\n"
        "   level (default 1 in 1.1.1, default 2 in 3.0) and which ciphers\n"
        "   it filters.\n"
        "3. Trace the cross-repo path: curl handle -> Curl_ossl_connect ->\n"
        "   SSL_CTX_set_cipher_list -> openssl provider init -> security "
        "   level check -> cipher_filter callbacks. Cite each file and the\n"
        "   function or attribute responsible.\n"
        "4. Identify why openssl 3.0's default security level rejects RSA\n"
        "   key exchange ciphers and SHA1 signatures that 1.1.1 accepted,\n"
        "   and how libcurl's backend does not surface the level mismatch\n"
        "   except as 'no cipher match'."
    ),
    repos=(
        Repo("https://github.com/curl/curl", "curl-8_6_0", "curl", "primary"),
        Repo("https://github.com/openssl/openssl", "openssl-3.0.13", "openssl", "upstream"),
    ),
    languages=("c", "cpp"),
    total_loc=2_500_000,
    frameworks=("curl", "libcurl", "openssl"),
    multi_repo_pattern="investigate",
    repo_set_id="curl-openssl-tls-ecosystem",
    mcp_benefit_rationale=(
        "TLS handshake regression is split between libcurl's openssl backend "
        "and openssl 3.0's provider model. Cross-repo navigation is required "
        "to see why the security level rejects ciphers libcurl asks for."
    ),
    required_files=(
        GTFile("curl/lib/vtls/openssl.c", "curl", 0.95),
        GTFile("curl/lib/vtls/openssl.h", "curl", 0.75),
        GTFile("openssl/ssl/ssl_ciph.c", "openssl", 0.90),
        GTFile("openssl/ssl/t1_lib.c", "openssl", 0.85),
    ),
    sufficient_files=(
        GTFile("curl/lib/vtls/vtls.c", "curl", 0.70),
        GTFile("openssl/providers/defltprov.c", "openssl", 0.75),
    ),
    error_chain=(
        "libcurl's vtls/openssl.c builds an SSL_CTX and calls "
        "SSL_CTX_set_cipher_list with the cipher string from the curl "
        "handle (default or user-supplied)",
        "openssl 3.0's ssl_ciph.c filters the requested cipher list against "
        "the SSL_CTX's security level, which defaults to 2 (vs 1 in 1.1.1)",
        "Security level 2 disqualifies RSA key exchange, SHA1 signatures, "
        "and ciphers using <2048-bit RSA keys; if the partner's certificate "
        "or cipher prefs hit any of these, the cipher list collapses to "
        "empty",
        "openssl returns SSL_R_NO_CIPHER_MATCH from t1_lib.c during the "
        "ClientHello build; libcurl's openssl.c surfaces it as the user-"
        "facing 'no cipher match' error without naming the security level",
    ),
    trigger_conditions=(
        "Partner endpoint's TLS configuration includes RSA key exchange or "
        "SHA1 signed certificates that openssl 3.0 default level 2 rejects",
        "libcurl's openssl backend does not explicitly call "
        "SSL_CTX_set_security_level so it inherits openssl's compiled-in "
        "default",
        "openssl 3.0 raised the compiled-in default from 1 to 2, breaking "
        "compatibility with deployments that rely on legacy ciphers",
    ),
    expected_answer={
        "error_origin": (
            "openssl 3.0's default security level 2 filtering out RSA/SHA1 "
            "ciphers that libcurl's openssl backend requests via "
            "SSL_CTX_set_cipher_list"
        ),
        "bypass_reason": (
            "libcurl does not set SSL_CTX_set_security_level explicitly and "
            "inherits the openssl 3.0 default of 2; 1.1.1 was 1"
        ),
        "correct_config": (
            "Lower the security level via curl --ciphers '@SECLEVEL=1:...' "
            "or rebuild libcurl/openssl with security_level=1, or upgrade "
            "the partner endpoint to ciphers acceptable at level 2"
        ),
    },
)


# ---------------------------------------------------------------------------
# Top-up (3 more) — added because envoy-istio carries a "go" language tag
# (Istio Pilot is genuinely Go), so we needed 3 additional non-Go tasks
# to push the denominator past 141 and bring Go% below 40%.
# ---------------------------------------------------------------------------


T_SQLALCHEMY_ALEMBIC = TaskDef(
    task_id="support-mapping-dual-sqlalchemy-alembic-metadata-001",
    suite="customer_escalation",
    task_type="support_code_mapping",
    difficulty="hard",
    duration=30,
    description=(
        "Map customer Alembic autogenerate producing empty migrations after "
        "SQLAlchemy 2.x model refactor to the Alembic env.py / SQLAlchemy "
        "MetaData boundary"
    ),
    prompt=(
        "A customer reports that after upgrading SQLAlchemy from 1.4 to 2.0\n"
        "and refactoring their models into separate modules, alembic\n"
        "revision --autogenerate produces empty migrations. The models clearly\n"
        "differ from the database schema, but Alembic detects no changes.\n\n"
        "Your task:\n"
        "1. Find where Alembic's env.py imports the customer's MetaData and\n"
        "   passes target_metadata to context.configure.\n"
        "2. Find where SQLAlchemy 2.0's DeclarativeBase registers tables on a\n"
        "   shared MetaData, and how splitting models across modules can\n"
        "   create multiple unrelated MetaData instances.\n"
        "3. Trace the cross-repo path: alembic command -> env.py import ->\n"
        "   SQLAlchemy DeclarativeBase.metadata -> MetaData.tables -> Alembic\n"
        "   autogenerate compare_metadata. Cite each file and the function\n"
        "   responsible.\n"
        "4. Identify why the customer's split-module refactor (each file\n"
        "   declares its own DeclarativeBase) produces multiple MetaData\n"
        "   instances, and only the one referenced in env.py is compared."
    ),
    repos=(
        Repo("https://github.com/sqlalchemy/sqlalchemy", "rel_2_0_29", "sqlalchemy", "upstream"),
        Repo("https://github.com/sqlalchemy/alembic", "rel_1_13_1", "alembic", "primary"),
    ),
    languages=("python",),
    total_loc=400_000,
    frameworks=("sqlalchemy", "alembic"),
    multi_repo_pattern="investigate",
    repo_set_id="sqlalchemy-alembic-ecosystem",
    mcp_benefit_rationale=(
        "Empty-migration regression is split between Alembic's autogenerate "
        "and SQLAlchemy's DeclarativeBase metadata semantics. Cross-repo "
        "navigation is required to see why split-module models produce "
        "multiple MetaData instances."
    ),
    required_files=(
        GTFile("alembic/alembic/autogenerate/compare.py", "alembic", 0.95),
        GTFile("alembic/alembic/runtime/environment.py", "alembic", 0.85),
        GTFile("sqlalchemy/lib/sqlalchemy/orm/decl_api.py", "sqlalchemy", 0.90),
        GTFile("sqlalchemy/lib/sqlalchemy/sql/schema.py", "sqlalchemy", 0.85),
    ),
    sufficient_files=(
        GTFile("alembic/alembic/autogenerate/api.py", "alembic", 0.75),
        GTFile("sqlalchemy/lib/sqlalchemy/orm/decl_base.py", "sqlalchemy", 0.70),
    ),
    error_chain=(
        "Customer's env.py imports target_metadata from a single module; "
        "Alembic's environment.py passes it to context.configure",
        "SQLAlchemy 2.0's DeclarativeBase, when subclassed in a different "
        "module, creates a NEW MetaData instance unless explicitly sharing "
        "one via metadata=Base.metadata",
        "Alembic's compare.py walks target_metadata.tables and diffs against "
        "the live database; tables registered on OTHER MetaData instances "
        "are invisible to it",
        "The autogenerate output is empty because most of the customer's "
        "model classes ended up on a different DeclarativeBase whose "
        "MetaData was never passed to env.py",
    ),
    trigger_conditions=(
        "Customer split models across modules and each module declared its "
        "own DeclarativeBase subclass without sharing metadata",
        "SQLAlchemy 2.0's DeclarativeBase changed how class-level metadata "
        "is created vs 1.4's declarative_base() factory",
        "Alembic's compare_metadata operates on a single MetaData; tables "
        "on disconnected instances are silently ignored",
    ),
    expected_answer={
        "error_origin": (
            "SQLAlchemy 2.0 DeclarativeBase creating per-subclass MetaData "
            "instances; Alembic's compare.py only sees the one passed via "
            "target_metadata"
        ),
        "bypass_reason": (
            "Alembic does not enumerate all DeclarativeBase subclasses; it "
            "trusts target_metadata to be the union"
        ),
        "correct_config": (
            "Use a single DeclarativeBase across modules and import all "
            "model modules into env.py before passing target_metadata, OR "
            "explicitly construct a combined MetaData and pass that"
        ),
    },
)


T_VITEST_VITE = TaskDef(
    task_id="support-mapping-dual-vitest-vite-optimize-001",
    suite="customer_escalation",
    task_type="support_code_mapping",
    difficulty="medium",
    duration=30,
    description=(
        "Map customer Vitest tests failing with stale module after Vite "
        "optimizeDeps refresh to the Vitest pool / Vite optimizeDeps cache "
        "boundary"
    ),
    prompt=(
        "A customer reports that under Vitest watch mode their tests pass on\n"
        "the first run but fail intermittently on file save with errors like\n"
        "'TypeError: foo is not a function' for re-exported library symbols.\n"
        "Restarting Vitest fixes it until the next save.\n\n"
        "Your task:\n"
        "1. Find where Vitest's pool spawns worker threads and how it shares\n"
        "   Vite's transform cache.\n"
        "2. Find where Vite's optimizeDeps pre-bundles dependencies and how\n"
        "   it invalidates the cache on file change.\n"
        "3. Trace the cross-repo path: Vitest watcher file change -> Vite\n"
        "   moduleGraph invalidate -> optimizeDeps re-pre-bundle -> worker\n"
        "   pool module reload. Cite each file and the function responsible.\n"
        "4. Identify why a stale optimizeDeps bundle can survive a watcher\n"
        "   tick when the pre-bundled chunk's mtime trails the source change."
    ),
    repos=(
        Repo("https://github.com/vitest-dev/vitest", "v1.4.0", "vitest", "primary"),
        Repo("https://github.com/vitejs/vite", "v5.2.6", "vite", "upstream"),
    ),
    languages=("typescript", "javascript"),
    total_loc=350_000,
    frameworks=("vitest", "vite", "esbuild"),
    multi_repo_pattern="investigate",
    repo_set_id="vitest-vite-ecosystem",
    mcp_benefit_rationale=(
        "Stale-module regression is split between Vitest's worker pool and "
        "Vite's optimizeDeps cache. Cross-repo navigation is required to "
        "see why the worker reloads a pre-bundled chunk that has not been "
        "rebuilt."
    ),
    required_files=(
        GTFile("vitest/packages/vitest/src/runtime/runVmTests.ts", "vitest", 0.85),
        GTFile("vitest/packages/vitest/src/node/pool.ts", "vitest", 0.90),
        GTFile("vite/packages/vite/src/node/optimizer/index.ts", "vite", 0.95),
        GTFile("vite/packages/vite/src/node/server/moduleGraph.ts", "vite", 0.85),
    ),
    sufficient_files=(
        GTFile("vitest/packages/vitest/src/node/core.ts", "vitest", 0.75),
        GTFile("vite/packages/vite/src/node/optimizer/optimizer.ts", "vite", 0.85),
    ),
    error_chain=(
        "Vitest's pool runs tests in worker threads that import modules "
        "via Vite's transform pipeline; the worker reuses Vite's "
        "moduleGraph for cache hits",
        "On file save, Vite's chokidar handler invalidates the source "
        "module in moduleGraph and triggers optimizeDeps to re-pre-bundle "
        "if a pre-bundled dependency changed",
        "optimizeDeps writes a new chunk to node_modules/.vite/deps with "
        "an updated metadata.json, but the chunk filename can collide with "
        "the previous build until cache key recomputes",
        "Vitest's worker imports the pre-bundled chunk via the cached URL "
        "from moduleGraph; if the worker's previous import handle is still "
        "live, it reuses the stale chunk in memory and resolves "
        "re-exported symbols against the old binding",
    ),
    trigger_conditions=(
        "Customer's tests import a re-exported symbol from a pre-bundled "
        "dependency that changes between saves",
        "Vite's optimizeDeps recomputes cache key per content hash; if the "
        "hash skips because the change is in a transitive re-export, the "
        "chunk is not rewritten",
        "Vitest's worker holds the previous chunk in module cache and does "
        "not invalidate it on the new optimizeDeps build",
    ),
    expected_answer={
        "error_origin": (
            "Vitest's worker thread retaining the previous pre-bundled "
            "chunk after Vite's optimizeDeps regenerates it"
        ),
        "bypass_reason": (
            "Vite's optimizeDeps does not signal individual workers to "
            "purge their module cache; Vitest's pool relies on import "
            "freshness from Vite's moduleGraph"
        ),
        "correct_config": (
            "Set test.pool='forks' and pass --no-isolate=false so each test "
            "run uses a fresh process, or pin optimizeDeps.exclude on the "
            "re-exporting module so it is loaded from source"
        ),
    },
)


T_PRISMA_ZOD = TaskDef(
    task_id="support-mapping-dual-prisma-zod-codegen-001",
    suite="customer_escalation",
    task_type="support_code_mapping",
    difficulty="medium",
    duration=30,
    description=(
        "Map customer zod-prisma codegen drift after Prisma schema reorder "
        "to the Prisma DMMF / zod-prisma generator boundary"
    ),
    prompt=(
        "A customer reports that after reordering fields in their\n"
        "schema.prisma file, their zod-prisma-generated zod schemas now\n"
        "validate request bodies inconsistently across services. Some\n"
        "services accept payloads other services reject, even though both\n"
        "regenerate from the same schema.prisma file.\n\n"
        "Your task:\n"
        "1. Find where Prisma's generator API exposes the DMMF (Data Model\n"
        "   Meta Format) to downstream generators like zod-prisma.\n"
        "2. Find where zod-prisma walks the DMMF model fields and how field\n"
        "   ordering affects the produced zod object.\n"
        "3. Trace the cross-repo path: prisma generate -> generator helper\n"
        "   getDMMF -> DMMF.Model.fields array -> zod-prisma model writer\n"
        "   -> zod object schema. Cite each file and the function responsible.\n"
        "4. Identify why DMMF's fields order is documented as schema-source\n"
        "   order, and why zod's z.object().strict() can validate identical\n"
        "   key sets but generate different default-discriminator behavior\n"
        "   when zod-prisma emits unions with field-position-dependent keys."
    ),
    repos=(
        Repo("https://github.com/prisma/prisma", "5.12.0", "prisma", "upstream"),
        Repo("https://github.com/colinhacks/zod", "v3.23.0", "zod", "primary"),
    ),
    languages=("typescript",),
    total_loc=600_000,
    frameworks=("prisma", "zod"),
    multi_repo_pattern="investigate",
    repo_set_id="prisma-zod-codegen-ecosystem",
    mcp_benefit_rationale=(
        "Codegen drift is split between Prisma's DMMF emitter and zod's "
        "schema construction order. Cross-repo navigation is required to "
        "see why field reordering in schema.prisma changes runtime "
        "validation behavior."
    ),
    required_files=(
        GTFile("prisma/packages/internals/src/engine-commands/getDmmf.ts", "prisma", 0.90),
        GTFile("prisma/packages/generator-helper/src/dmmf.ts", "prisma", 0.95),
        GTFile("zod/src/types.ts", "zod", 0.90),
        GTFile("zod/src/helpers/parseUtil.ts", "zod", 0.80),
    ),
    sufficient_files=(
        GTFile("prisma/packages/generator-helper/src/byline.ts", "prisma", 0.65),
        GTFile("zod/src/external.ts", "zod", 0.70),
    ),
    error_chain=(
        "Prisma's prisma generate runs the engine to produce DMMF; the "
        "DMMF's Model.fields array preserves source-file declaration order",
        "zod-prisma (third-party generator that consumes prisma's DMMF) "
        "walks fields in array order and emits z.object({...}) keys in "
        "that order",
        "zod's z.object internally stores shape as a plain object whose "
        "key iteration order matches insertion (per ECMAScript 2015+ spec)",
        "When zod-prisma emits a discriminated union via z.discriminatedUnion, "
        "the discriminator key resolution depends on shape iteration order; "
        "a reorder in schema.prisma can change which variant zod picks for "
        "ambiguous payloads",
        "Different services that regenerated at different times (one before, "
        "one after the reorder) emit different zod schemas, leading to "
        "validation drift",
    ),
    trigger_conditions=(
        "Customer reordered fields in schema.prisma (perhaps to group "
        "related fields) and not all services regenerated zod schemas at "
        "the same time",
        "zod's z.discriminatedUnion behavior depends on shape iteration "
        "order; reordering shape keys changes which variant matches an "
        "ambiguous input",
        "Prisma's DMMF preserves source order, propagating the reorder "
        "into zod-prisma's emitted shape",
    ),
    expected_answer={
        "error_origin": (
            "Prisma's DMMF preserving schema.prisma source order, which "
            "zod-prisma maps directly into shape iteration order in zod's "
            "z.object"
        ),
        "bypass_reason": (
            "zod's discriminated-union resolution is shape-iteration-order "
            "dependent for ambiguous payloads; field reorder = different "
            "variant selection"
        ),
        "correct_config": (
            "Pin the field order in schema.prisma alphabetically (or by a "
            "documented convention) and regenerate all services from the "
            "same Prisma version, or replace z.discriminatedUnion with "
            "z.union plus an explicit discriminator check"
        ),
    },
)


# Module-level export — final
TASKS: list[TaskDef] = [
    T_DJANGO_WAGTAIL,
    T_FLASK_WERKZEUG,
    T_FASTAPI_PYDANTIC,
    T_CELERY_KOMBU,
    T_BABEL_SWC,
    T_REACT_RELAY,
    T_JEST_BABEL,
    T_NEXT_TURBOPACK,
    T_SPRING_HIBERNATE,
    T_LOG4J_SLF4J,
    T_JACKSON_SPRING,
    T_SPRING_KAFKA_REBALANCE,
    T_TOKIO_HYPER,
    T_AXUM_TOWER,
    T_TONIC_TOKIO,
    T_ENVOY_ISTIO,
    T_GRPC_PROTOBUF,
    T_OPENSSL_CURL,
    T_SQLALCHEMY_ALEMBIC,
    T_VITEST_VITE,
    T_PRISMA_ZOD,
]
