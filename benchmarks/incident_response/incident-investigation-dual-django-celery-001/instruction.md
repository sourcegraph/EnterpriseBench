# Investigate Django database connection pool exhaustion caused by Celery prefork worker lifecycle mismatch

Production monitoring shows PostgreSQL connection count climbing steadily and
eventually exhausting the pool (max_connections=100). The application uses Django
with Celery for background task processing. The connection exhaustion correlates
with Celery worker restarts (--max-tasks-per-child=1000).

Key observations:
- Django web server connections are stable (~20)
- Celery worker connections grow linearly with task execution
- After a worker child is recycled (max-tasks-per-child), its connections are NOT
  returned to PostgreSQL for ~10 minutes (the TCP keepalive timeout)
- Setting CONN_MAX_AGE=0 in Django fixes the leak but hurts performance

The issue is a mismatch between Django's database connection lifecycle and
Celery's prefork worker process lifecycle.

Your task:
1. Find where Django manages database connection lifecycle — trace from
   django.db.connections through the connection handler to identify how
   connections are created, cached per-thread, and closed. Focus on
   close_old_connections() and CONN_MAX_AGE behavior.
2. Find where Celery's prefork pool manages worker child processes — trace
   through the pool implementation to identify how child processes are
   forked, recycled (max-tasks-per-child), and what cleanup hooks are
   called during child termination.
3. Identify the mismatch: Django caches connections per-thread with
   CONN_MAX_AGE, but Celery's prefork children are processes (not threads)
   that may not trigger Django's connection cleanup on termination.
4. Trace the connection leak: fork -> Django opens connection -> task executes ->
   child recycle -> process exits -> connection left in TIME_WAIT.

- Connection lifecycle in Django (file:function chain)
- Worker lifecycle in Celery prefork pool (file:function chain)
- Root cause: why connections leak on worker recycle
- Correct mitigation configuration
