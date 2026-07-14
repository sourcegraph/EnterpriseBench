# Escalation: streamed downloads from one upstream come back truncated or with foreign bytes

## Reported issue

A data-sync service pulls large files from partner endpoints over HTTPS using a
Rust HTTP client. It streams each response body in chunks rather than buffering:

    let mut resp = client.get(url).send().await?;
    while let Some(chunk) = resp.chunk().await? {
        sink.write_all(&chunk).await?;
    }

Against almost every partner this is rock solid. But against one partner's
download endpoint, a small fraction of transfers come back wrong. The failure has
two shapes, both intermittent:

- the written file is silently **truncated** — the stream ends early with no
  error, well short of the advertised size; or
- the tail of one download contains a burst of bytes that clearly belong to a
  **different response** (HTTP status lines and header text appear embedded in
  what should be file data).

No error is returned in either case; the future just completes. Retrying the
exact same URL usually succeeds, which is why it took months to catch.

The partner's endpoint uses `Transfer-Encoding: chunked` (no `Content-Length`).
A packet capture of a failing transfer shows the partner emitting a chunk whose
size line is an **unusually long run of hex digits** — on the order of sixteen or
more hex characters before the CRLF (for example a size field like
`ffffffffffffffff0` or `10000000000000005`), where healthy partners send short
size lines. The partner insists their server "just streams the file".

## Your task

1. Determine which component actually mis-frames the response. The only code your
   team wrote is the chunk-streaming loop above; the crate whose client type you
   call does not parse transfer framing itself — it hands the connection's body to
   something else through a boxed streaming-body abstraction. The code that reads
   the hex chunk-size line off the wire and turns it into a byte count lives in a
   different crate entirely, deep in an HTTP/1 read state machine. Identify the
   exact repository, file, and routine where an over-long chunk-size line is
   mishandled.

2. Trace the full path from your streaming loop to that origin: the client-side
   response body stream that yields the chunks, the transitive HTTP library's
   body value it wraps and polls, and the HTTP/1 chunk-size reader that
   accumulates the hex digits into an integer size. Name the file and the type or
   routine responsible at each hop, and explain why the mis-framing site lives in
   a different repository than the crate your code calls.

3. Fix the defect at its real origin. The chunk-size reader accumulates each hex
   digit into a 64-bit size with plain multiply-and-add and never checks for
   overflow, so a long-enough size line wraps the accumulator around to a small or
   attacker-chosen value; the connection then reads the wrong number of body bytes
   and the stream desynchronizes. Make the accumulation reject a size that would
   overflow, surfacing it as the same kind of invalid-chunk framing error the
   reader already produces for other malformed size lines. Leave the change as an
   uncommitted diff in the workspace; do not paper over it in the streaming loop
   with a length check.

## Deliverables

- In `answer.json`, alongside your analysis, include a top-level `citations` list
  with one entry per cited file: `{"repo": ..., "file": ..., "evidence_span": ...}`
  where `evidence_span` is a verbatim excerpt (at least 20 characters) copied from
  the cited file. Cover every file on the failure path across both repositories.
- An uncommitted code change at the true origin that converts an overflowing
  chunk size into a normal invalid-chunk framing error.
