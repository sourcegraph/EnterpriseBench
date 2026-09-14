# Customer escalation: valid gzip responses from one upstream fail to decompress in our reactive HTTP client

## What the customer reports

A customer runs a Spring WebFlux gateway that calls a handful of upstream HTTP
APIs through the reactive HTTP client, with automatic response decompression
enabled (the client advertises `Accept-Encoding: gzip` and transparently inflates
the body). Their configuration is boilerplate:

    HttpClient.create()
        .compress(true)          // accept + auto-decompress gzip
        .get()
        .uri("https://data.vendor-cdn.example/exports/latest")
        .responseSingle((res, body) -> body.asByteArray());

For almost every upstream this is flawless. But responses from one particular
vendor CDN intermittently blow up on the reactive stream instead of yielding a
body:

    io.netty.handler.codec.DecompressionException: decompression failure
        ... (reactive operators) ...

The customer has already ruled out the obvious explanations, and this is why they
are escalating:

- The bytes are valid gzip. If they capture the raw response and pipe it through
  the JDK's own `java.util.zip.GZIPInputStream`, it decodes perfectly.
- If they turn OFF automatic decompression and inflate the raw bytes themselves,
  there is no error.
- The responses that fail all share one property: the gzip header sets its
  optional "extra field" flag (the responses that succeed do not). The vendor
  produces these with a tool that writes an application-specific extra subfield
  into the gzip header, which is entirely legal per the gzip spec.

So: valid gzip, decodes with the JDK, breaks only through our client's automatic
decompression, only when the gzip header carries an extra field. The customer's
own code contains nothing about gzip — the decompression is inserted for them
somewhere down the stack.

## Your task

1. Explain how enabling automatic decompression on the reactive HTTP client
   causes a gzip inflater to be inserted into the connection's processing
   pipeline. Identify the file and method in the client library that wires this
   in, and note that the inflater it constructs lives in a different, lower-level
   dependency.

2. Find the true root cause: the exact repository, file, and code that parses the
   gzip header's optional-extra-field length and then skips that many bytes.
   Explain precisely why a header carrying an extra field is mishandled — what the
   length value ends up being versus what it should be, and why the extra field is
   therefore never skipped, which corrupts the rest of the stream and yields the
   decompression failure. This is genuine byte-level arithmetic; describe the
   defect concretely (the sentinel/initial value, the bit-combination operation,
   and the byte order the spec requires).

3. Fix the defect at its real origin so that gzip streams carrying an extra field
   inflate correctly, matching the JDK's behavior. Leave the change as an
   uncommitted diff in the workspace; do not disable decompression or work around
   it in the client library.

## Deliverables

- In `answer.json`, alongside your analysis, include a top-level `citations` list
  with one entry per cited file: `{"repo": ..., "file": ..., "evidence_span": ...}`
  where `evidence_span` is a verbatim excerpt (at least 20 characters) copied from
  the cited file. Cover every file on the failure path across both repositories:
  where the client wires in decompression, and where the header is mis-parsed.
- An uncommitted code change at the true origin that corrects the extra-field
  length parsing.
