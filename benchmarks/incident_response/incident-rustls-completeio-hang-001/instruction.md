# Incident: a synchronous HTTPS worker thread pins a core and never returns

## Reported issue

A batch job makes synchronous (blocking) HTTPS calls with a Rust HTTP client
configured to use the `rustls` TLS backend. Most runs are fine, but a few times a
week a worker wedges:

- one thread goes to **100% CPU and stays there indefinitely**;
- that thread is stuck inside a plain blocking `agent.get(url).call()` — it never
  returns, never times out at the TLS layer, and never surfaces an error;
- the rest of the process keeps running, so it looks like a slow leak of workers
  until enough of them are wedged that throughput collapses.

A thread dump of a wedged worker shows it spinning in the TLS stack while doing a
read on the connection — not blocked in a syscall waiting for data, but **busy
looping in user space**. `strace` on the thread shows almost no syscalls: it is
not waiting on the socket, it is going around some loop in the library without
ever calling read or write on the underlying connection.

The team correlated the wedges with a specific flaky upstream that, under load,
**stalls its side of the TLS connection at an awkward moment** — it stops sending
and stops reading mid-exchange without cleanly closing the connection (no TLS
close-notify, no TCP FIN for a long time). Every wedged worker was talking to that
upstream. A normal upstream that either finishes the exchange or closes the socket
never triggers it. The client's own read/response timeout does not help, because
the wedged thread never yields back to the client code to observe the deadline.

## Your task

1. Determine which component actually spins. Your client crate does not run the
   TLS protocol itself — it wraps the socket in a boxed read-write object and hands
   it to the TLS library's blocking stream adapter, then calls `read` on that. The
   loop that drives the TLS connection's IO to completion, and that fails to
   terminate when the connection can make no further progress, lives inside the TLS
   library. Identify the exact repository, file, and routine where the loop can
   iterate forever.

2. Trace the full path from the client's blocking `read` to that origin: the
   client-side TLS stream wrapper that delegates its `read`, the TLS library's
   blocking stream adapter that calls the IO-completion routine in a loop, and the
   IO-completion routine itself that alternately tries to write pending TLS data
   and read new TLS data. Name the file and the type or routine responsible at each
   hop, and explain why the spinning loop lives in a different repository than the
   client crate whose `call()` never returns.

3. Fix the defect at its real origin. The completion loop keeps going around as
   long as it is not certain the exchange is finished, but there is a state where
   the connection wants **neither** to write pending data **nor** to read more data
   and is not cleanly closed; in that state the loop does no IO yet loops again,
   burning the CPU forever. Make the loop detect that no progress is possible —
   nothing to write and nothing to read — and return in that case instead of
   iterating again. Leave the change as an uncommitted diff in the workspace; do
   not work around it with a wall-clock timeout in the client.

## Deliverables

- In `answer.json`, alongside your analysis, include a top-level `citations` list
  with one entry per cited file: `{"repo": ..., "file": ..., "evidence_span": ...}`
  where `evidence_span` is a verbatim excerpt (at least 20 characters) copied from
  the cited file. Cover every file on the failure path across both repositories.
- An uncommitted code change at the true origin that makes the completion loop
  return when neither a read nor a write can make progress.
