# Escalation: one vendor's API responses come back as binary garbage

## Reported issue

A data-integration service pulls JSON from dozens of third-party vendor APIs
using the `requests` library with an otherwise stock configuration:

    resp = session.get(vendor_url)
    payload = resp.json()

Every vendor works except one. For that single vendor, `resp.json()` raises

    json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)

and `resp.text` is unreadable mojibake. When the team dumps `resp.content` for the
failing vendor, the first two bytes are:

    1f 8b

They recognize those as the gzip magic number: the body is still compressed. The
service is receiving the raw compressed bytes instead of the decompressed JSON.

Two clues narrow it down:

- The exact same URL returns clean, decoded JSON in `curl` and in a browser, so
  the payload itself is fine and the server is behaving.
- Every *other* vendor's compressed responses decode correctly through the same
  client. Only this vendor's responses come back compressed.

Inspecting the failing vendor's response headers against a working vendor's, the
only material difference on the compressed responses is the value the failing
vendor puts in its `Content-Encoding` header: it advertises a slightly
non-standard, but legitimate and widely accepted, spelling of the gzip encoding.
The client is treating that value as "no known compression" and handing the body
back untouched, so nothing ever decompresses it.

The service never touches the raw socket or the transfer encoding itself; it just
calls the client and reads `resp.json()`.

## Your task

1. Determine why the compressed body is returned undecoded for this one vendor.
   The `requests` call is only the surface: the object whose attributes the team
   reads is built and populated by a lower-level transport library, and the
   decision about whether a response body is decompressed at all is made there, by
   a small hardcoded table of recognized content-encodings and a dispatcher that
   maps an encoding value to a decompressor. Identify the exact repository, file,
   and the two places (the dispatcher and the recognized-encodings list) where the
   vendor's `Content-Encoding` value is not recognized as gzip.

2. Trace the full path across the repository boundary: the `requests` adapter that
   opens the connection and calls into the transport library's connection pool, the
   pool method that constructs the response object, and the `requests` model
   attribute the customer reads that pulls the (still-compressed) bytes back out.
   Name the file and the function/class responsible at each hop, and explain why
   the defect lives in the transport library rather than in the `requests` layer
   whose API the customer is calling.

3. Fix the defect at its real origin so the vendor's content-encoding value is
   decoded exactly like the standard gzip encoding: it must route through the gzip
   decompressor in the dispatcher and appear in the list of encodings the response
   treats as decodable. Make the change in the workspace and leave it as an
   uncommitted diff; do not special-case this vendor or re-decompress the body in
   the integration service.

## Deliverables

- In `answer.json`, alongside your analysis, include a top-level `citations` list
  with one entry per cited file: `{"repo": ..., "file": ..., "evidence_span": ...}`
  where `evidence_span` is a verbatim excerpt (at least 20 characters) copied from
  the cited file. Cover every file on the failure path across both repositories.
- An uncommitted code change at the true origin that makes the vendor's
  content-encoding value decode as gzip.
