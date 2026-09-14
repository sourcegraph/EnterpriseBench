# Map customer Flask request-context vanishing under asyncio + ContextVar to the Werkzeug LocalStack contextvars boundary

A customer reports that their Flask 2.3 app loses request context inside
an async view that awaits a coroutine spawned via asyncio.create_task.
Inside the spawned task, request.path raises RuntimeError: 'Working
outside of request context.' even though the same code worked under
Flask 2.0 with werkzeug 2.0's local-stack semantics.

Your task:
1. Find where Flask exposes the global `request` proxy and how it binds
   to a Werkzeug LocalStack/ContextVar-backed object.
2. Find where Werkzeug's Local switched from threadlocal to contextvars
   and how a forked asyncio task inherits or fails to inherit the
   active context.
3. Trace the request-context-not-available chain: Flask app context ->
   werkzeug Local() proxy -> contextvars.ContextVar.get() -> asyncio
   Task.create(context=copy_context()) semantics. Cite each file and
   the function responsible.
4. Identify why asyncio.create_task captures the parent context but a
   bare `ensure_future(coro)` from a thread-pool worker may not, and
   the consequence at the werkzeug/Flask boundary.
