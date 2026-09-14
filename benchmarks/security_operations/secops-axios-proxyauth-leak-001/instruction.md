# Security incident: proxy credentials leak to a third-party host on redirect

## Reported issue

A backend team routes outbound HTTP through an authenticated forward proxy. They
use a popular Node HTTP client and pass the proxy credentials in the request
config:

    await client.get("https://api.partner.example/orders", {
      proxy: {
        host: "proxy.internal",
        port: 8080,
        auth: { username: "svc-egress", password: "s3cr3t-proxy-pw" }
      }
    });

Their proxy adds a `Proxy-Authorization: Basic ...` header, as expected. During a
security review, an engineer captured traffic and found that when the partner
endpoint answers with a redirect to a **different host** — for example:

    HTTP/1.1 302 Found
    Location: https://collector.thirdparty.example/orders

the follow-up request the client issues to `collector.thirdparty.example`
**still carries the `Proxy-Authorization` header** containing the base64 proxy
credentials. The internal proxy password is thus disclosed to an arbitrary host
chosen by the upstream response.

The team verified:

- Ordinary end-user credentials are handled correctly: an `Authorization` header
  set on the original request is dropped when the redirect crosses to a new host.
  Only the *proxy* credential header survives the cross-host hop.
- The client code sets no headers manually; it only passes the `proxy` option.
  The `Proxy-Authorization` header is synthesized somewhere inside the client's
  networking layer.
- With redirects disabled entirely, nothing leaks — the disclosure only happens
  when a redirect is followed to a new host.

The application code contains none of the redirect or header logic. The header is
added in one dependency and the decision of which headers to keep across a
redirect is made in another.

## Your task

1. Identify the exact repository, file, and routine where confidential headers
   are stripped when a redirect crosses to a new host, and explain why the proxy
   credential header is not among them while the ordinary end-user credential
   header is. The client library the team uses is not where the retention
   decision is made; the redirect is followed by a separate transport the client
   substitutes at request time. Name that origin precisely.

2. Trace the full path from the client call to that stripping routine: the
   client's request-dispatch layer that selects its Node networking backend, the
   backend helper that synthesizes the proxy credential header from the `proxy`
   option, and the point at which that backend hands the request to the
   redirect-following transport instead of the raw Node transport. Name the file
   and the routine at each hop across both repositories, and explain why following
   imports from the application does not reveal the transport substitution.

3. Fix the defect at its true origin so that the proxy credential header is
   stripped on a cross-host redirect just like the ordinary credential header
   already is, matching how the library handles this in its fixed releases. Make
   the change in the workspace as an uncommitted diff. Do **not** work around it by
   disabling redirects or by deleting the header in the client; the stripping site
   itself must cover the proxy credential header.

## Deliverables

- In `answer.json`, alongside your analysis, include a top-level `citations` list
  with one entry per cited file: `{"repo": ..., "file": ..., "evidence_span": ...}`
  where `evidence_span` is a verbatim excerpt (at least 20 characters) copied from
  the cited file. Cover every file on the leak path across both repositories.
- An uncommitted code change at the true origin that strips the proxy credential
  header on a cross-host redirect.
