# Customer escalation: our home page started returning HTTP 500 for a small set of clients after a routine dependency bump

## What the customer reports

A team runs a Spring Boot web application. The landing page at `/` is served by the
framework's built-in welcome-page mechanism (they drop a static `index.html` on the
classpath and the framework serves it for browsers that ask for HTML). They did a
routine patch-level upgrade of their Spring dependencies and, since then, a small
but steady fraction of requests to `/` return HTTP 500 instead of the page.

The 500s correlate with one thing: the value of the incoming `Accept` header. Normal
browser requests (`Accept: text/html,application/xhtml+xml,...`) are fine. The
failing requests come from assorted API clients, health-checkers, and a security
scanner, and they all send an `Accept` header that is *unusual but not illegal* — in
particular a value wrapped in double quotes with no `;` parameter section, for
example:

    Accept: "text/html"

The response body on failure is an unhandled-exception page, and the log shows a
`java.lang.StringIndexOutOfBoundsException` bubbling out of the request with no
message the team recognizes:

    java.lang.StringIndexOutOfBoundsException: ...
        at java.base/java.lang.String.charAt(String.java:...)
        ...
        (framework request handling for the welcome page)
        ...

Two things make this an escalation rather than a shrug:

- Before the upgrade, the very same requests worked. A weird `Accept` header used to
  be tolerated: the app fell back to serving the page (or returned a clean 406), it
  never threw a 500. Something in the upgraded dependency changed how a malformed
  header is handled.
- The application code does nothing special here. The team never wrote the welcome-
  page handler and never wrote any `Accept`-header parsing; both are supplied by the
  framework. The exception is an *unchecked* index error coming from deep inside the
  framework's own header parsing, not from anything they can see.

## Your task

1. Explain how a request for `/` reaches code that parses the `Accept` header into
   media types. Identify the file and method in the web application framework layer
   that reads the header and parses it, and note that it deliberately catches only a
   *checked* "this media type string is invalid" exception — so any *unchecked*
   exception from the parse escapes as a 500. Note that the actual parsing routine it
   calls lives in a different, lower-level module and repository.

2. Find the true root cause: the exact repository, file, and code that scans the
   media-type text and, on encountering a double-quote character, looks at the
   character immediately before it to decide whether the quote is escaped. Explain
   precisely why a value that *begins* with a quote breaks this: what index the scan
   reads, why that index is out of range, and why the result is an unchecked
   `StringIndexOutOfBoundsException` rather than the checked "invalid media type"
   error every other malformed value produces. This is a genuine off-by-one; describe
   the boundary condition concretely.

3. Fix the defect at its real origin so that a media-type string beginning with a
   quote is rejected as a normal invalid-media-type error (the checked exception the
   upstream handler already tolerates) instead of throwing an unchecked index
   exception. Leave the change as an uncommitted diff in the workspace; do not paper
   over it by adding a broad `catch` in the welcome-page path or in the app.

## Deliverables

- In `answer.json`, alongside your analysis, include a top-level `citations` list
  with one entry per cited file: `{"repo": ..., "file": ..., "evidence_span": ...}`
  where `evidence_span` is a verbatim excerpt (at least 20 characters) copied from
  the cited file. Cover every file on the failure path across both repositories:
  where the header is read and parsed, and where the index goes out of bounds.
- An uncommitted code change at the true origin that converts the out-of-bounds read
  into the normal invalid-media-type parse error.
