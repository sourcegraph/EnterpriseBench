# Incident: an HTTP/2 API server pegs a CPU core and leaks memory on a single connection

## Reported issue

An internal API gateway built on a Rust HTTP/2 server has started falling over in
production. On-call sees the pattern below repeatedly:

- one worker process climbs to **100% CPU on a single core** and its RSS grows
  steadily for as long as the offending connection stays open;
- the gateway stops accepting new work behind that worker; health checks time out
  and the pod is eventually OOM-killed or restarted;
- it always traces back to **one client connection**, and tearing down that
  connection instantly returns the worker to normal.

The request on the bad connection never completes — there is no access-log line
for it, because the server is still waiting for the request headers to finish
arriving.

A packet capture of a bad connection shows the client opening a stream with a
`HEADERS` frame that does **not** set the `END_HEADERS` flag, then sending a
seemingly endless stream of tiny `CONTINUATION` frames on that same stream —
thousands per second, each carrying a few bytes of header-block fragment, none of
them ever setting `END_HEADERS`. The server keeps accepting and buffering them.
The team suspects this is the "HTTP/2 CONTINUATION flood" class of abuse
(tracked upstream as a resource-exhaustion advisory), but they have set every
server-level limit the public configuration exposes — max concurrent streams, max
header list size, keep-alive timeouts — and none of them stops it.

## Your task

1. Determine which component actually lets a single request consume unbounded CPU
   and memory. Your server crate does not decode HTTP/2 frames itself — it obtains
   a connection by driving a handshake/connection future from a separate HTTP/2
   library and then pulls decoded requests off it. The code that reads frames off
   the socket and **reassembles a header block that has been split across
   CONTINUATION frames** lives in that other library. Identify the exact
   repository, file, and routine where the CONTINUATION frames of one header block
   are accumulated.

2. Trace the full path from your server's connection setup to that origin: the
   server crate's HTTP/2 connection wiring that hands the socket to the library's
   handshake, the frame-reading layer that dispatches decoded frames, and the
   header-frame reassembly that appends each CONTINUATION payload to the partial
   block for a stream. Name the file and the type or routine responsible at each
   hop, and explain why the vulnerable reassembly lives in a different repository
   than the server crate whose limits you configured.

3. Fix the defect at its real origin. The reassembler will append CONTINUATION
   frames to a single header block without ever bounding how many it accepts, so a
   client that never sets `END_HEADERS` keeps the server doing hpack work and
   buffering forever. Make the reassembly count the CONTINUATION frames that
   extend one header block and, once that count crosses a sane bound, fail the
   connection with a protocol error instead of continuing to accept frames. Leave
   the change as an uncommitted diff in the workspace; do not work around it by
   dropping the connection from application code.

## Deliverables

- In `answer.json`, alongside your analysis, include a top-level `citations` list
  with one entry per cited file: `{"repo": ..., "file": ..., "evidence_span": ...}`
  where `evidence_span` is a verbatim excerpt (at least 20 characters) copied from
  the cited file. Cover every file on the failure path across both repositories.
- An uncommitted code change at the true origin that bounds the number of
  CONTINUATION frames per header block and fails the connection when the bound is
  exceeded.
