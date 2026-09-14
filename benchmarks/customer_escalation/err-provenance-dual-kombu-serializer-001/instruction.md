# Escalation: Celery workers crash on startup with a cryptic KeyError

## Reported issue

A platform team hardens their Celery deployment by restricting which payload
formats the workers are allowed to accept, setting the accepted-content-types
configuration to an explicit allow-list instead of the default. After the change,
every worker crashes immediately on startup, before processing a single task:

    Traceback (most recent call last):
      ...
    KeyError: 'msgpack'

The exception is a bare `KeyError` whose only detail is a short string naming a
serialization format (here `'msgpack'`). There is no message explaining what is
wrong, what is missing, or how to fix it. The traceback descends out of the
team's own code and Celery's app/worker bootstrap into the underlying
messaging/broker library and dies there.

Clues from the team's investigation:

- It only happens when they restrict the accepted-content-types list. With the
  default configuration the workers start fine.
- The format named in the `KeyError` is one they listed in the allow-list but had
  not actually installed/registered the serializer for. They expected a clear
  "that serializer isn't installed" error — the same well-formed error they get
  from other parts of the stack when a serializer is missing — not a raw
  `KeyError` with no context.
- The value in the `KeyError` is exactly the short-name they put in the config,
  not a MIME content-type, which tells them the crash happens while the
  short-names are being resolved to content-types.

The team never calls the messaging library directly; they only set Celery
configuration and start the workers.

## Your task

1. Determine what actually raises the `KeyError`. Celery's app and worker consumer
   layers forward the configured accepted-content-types across the boundary into
   the messaging library, which normalizes that allow-list by mapping each
   short-name to its registered content-type. Identify the exact repository, file,
   and function where an unregistered short-name is looked up in the name-to-type
   registry with no guard, so a missing serializer escapes as a bare `KeyError`
   instead of the library's normal "serializer not installed" error.

2. Trace the full path across the repository boundary: the Celery app messaging
   layer that builds the task consumer and passes the accepted-content-types
   through, the Celery worker consumer bootstrap where the crash surfaces at
   startup, and — in the messaging library — the Consumer setup that calls the
   accept-content preparation helper. Name the file and the function/class
   responsible at each hop, and explain why the throwing frame lives in the
   messaging library rather than in Celery, whose configuration triggered it.

3. Fix the defect at its real origin so that an unregistered serializer name in
   the accepted-content-types list produces the library's own "serializer not
   installed" error (the same normal, descriptive failure the rest of the library
   raises for a missing serializer) instead of an uncaught `KeyError`. Make the
   change in the workspace and leave it as an uncommitted diff; do not catch the
   `KeyError` in Celery's bootstrap or pre-validate the list in your own code.

## Deliverables

- In `answer.json`, alongside your analysis, include a top-level `citations` list
  with one entry per cited file: `{"repo": ..., "file": ..., "evidence_span": ...}`
  where `evidence_span` is a verbatim excerpt (at least 20 characters) copied from
  the cited file. Cover every file on the failure path across both repositories.
- An uncommitted code change at the true origin that converts the bare `KeyError`
  into the library's normal not-installed serializer error.
