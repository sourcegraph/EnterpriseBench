# Incident: a service slowly leaks HTTP connections until every request times out waiting for the pool

## What we are seeing

A backend service makes outbound calls to a handful of internal APIs through
Apache HttpClient 5, using a shared, pooling connection manager. The relevant
configuration is ordinary:

    // one shared manager for the whole process
    connManager.setMaxTotal(200);
    connManager.setDefaultMaxPerRoute(50);
    // callers ask for a connection with a bounded wait
    RequestConfig.custom()
        .setConnectionRequestTimeout(Timeout.ofMilliseconds(50))
        .build();

Under normal traffic this is fine. But under sustained high concurrency the
service degrades over hours: the number of leased connections reported by the
manager climbs and never comes back down, even though every request handler
returns its connection in a `finally` block. Eventually the pool is saturated and
*every* new request fails, fast, with:

    org.apache.hc.core5.http.ConnectionRequestTimeoutException: Timeout waiting for connection from pool
        ... (our request executor) ...

Restarting the process resets the leased count to zero and buys another few hours.

Here is what makes it strange, and why we are escalating:

- We have audited our own code. Every borrowed connection is released exactly
  once; there is no path that forgets to return one.
- The leak rate tracks the *connection-request-timeout*, not request volume per
  se. When we lengthen that timeout, the leak slows dramatically. When we shorten
  it, the leak accelerates. A timeout of zero (wait forever) makes the leak
  disappear entirely.
- The leaked connections are never handed back to any caller: the leased counter
  goes up, but no request handler ever received that connection. So it is being
  taken out of the pool and then dropped on the floor, by something below our
  code, precisely around the moment a lease is timing out.

Our code contains nothing about the pool's internals — we only set two limits and
a timeout. Whatever is losing the connection lives underneath the connection
manager we configure.

## Your task

1. Explain how a connection request travels from the pooling connection manager
   the app configures down to the object that actually parcels out pooled entries.
   Identify the file and method in the client library that hands the lease off,
   and note that the concrete pool doing the work lives in a different, lower-level
   dependency and is selected at construction time rather than named directly.

2. Find the true root cause: the exact repository, file, and code where a lease
   that is waiting with a bounded timeout can both time out AND be fulfilled at the
   same instant, and where the losing side of that race discards the pool entry
   instead of returning it. Describe the defect concretely: what the timeout path
   does after the wait expires, why that is wrong when the entry was in fact
   delivered in the same moment, and how that produces exactly the symptom we see
   (leased count climbs, no caller ever gets the connection, rate scales with the
   timeout).

3. Fix the defect at its real origin so a lease that loses the cancel race recovers
   the connection that was delivered to it instead of leaking it, while a lease
   that genuinely timed out still surfaces the timeout. Leave the change as an
   uncommitted diff in the workspace; do not paper over it by disabling the timeout
   or by adding a reaper in the app.

## Deliverables

- In `answer.json`, alongside your analysis, include a top-level `citations` list
  with one entry per cited file: `{"repo": ..., "file": ..., "evidence_span": ...}`
  where `evidence_span` is a verbatim excerpt (at least 20 characters) copied from
  the cited file. Cover every file on the failure path across both repositories:
  where the manager delegates the lease, and where the entry is dropped.
- An uncommitted code change at the true origin that stops the leak by recovering
  the delivered entry when the cancel loses the race.
