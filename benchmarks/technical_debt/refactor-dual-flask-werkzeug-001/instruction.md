# Plan refactor of Flask request/app context globals from Werkzeug LocalStack to Python contextvars

Flask's request context system (flask.g, flask.request, flask.session) is built
on Werkzeug's LocalStack/LocalProxy, which predates Python's native contextvars
module. Werkzeug 2.3+ added contextvars support alongside the legacy Local
implementation. Flask 3.0 began migrating but still has hybrid code paths.

Your task:
1. Find all Flask code that uses Werkzeug's LocalStack and LocalProxy for
   context management — trace from flask.globals through the request context
   push/pop to identify every point where LocalStack is used vs where
   contextvars could be used directly.
2. Find where Werkzeug implements both the legacy Local/LocalStack and the new
   contextvars-based ContextVar equivalents — identify the migration bridge
   classes and how they maintain backward compatibility.
3. Plan the refactor: for each Flask context global (request, session, g,
   _app_ctx_stack, _request_ctx_stack), document what Werkzeug class it
   currently uses, what it should migrate to, and what breaks if migrated
   (particularly for greenlet/eventlet/gevent compatibility).
4. Identify ordering constraints: which changes must happen in Werkzeug first,
   and which Flask changes depend on specific Werkzeug versions.

Document in your answer (use these JSON fields):
{
  "refactor_steps": [
    {
      "target": "<Flask/Werkzeug class or module>",
      "current_implementation": "<what it uses now>",
      "target_implementation": "<what it should use>",
      "source_file": "<file path>",
      "dependencies": ["<other steps that must complete first>"],
      "breaking_changes": ["<what breaks>"]
    }
  ]
}
