# Escalation: async client cannot reach an IPv6 link-local peer

## Reported issue

A platform team runs an async data-collection service built on an `asyncio`
HTTP client. Most of the fleet is reached over ordinary DNS names and works
fine. A subset of appliances sits on an isolated IPv6 segment and is only
addressable by its **link-local** address, so the operators configure those
targets with an address that carries a scope/zone identifier, for example:

    http://[fe80::1%25eth0]:8080/telemetry

(The `%25` is how the zone separator is written inside a URL — it is the
percent-encoding of a literal `%`, per the RFC that defines zone identifiers in
URLs.) The exact same appliance is reachable from a shell on the collector host:

    $ ping6 fe80::1%eth0        # works
    $ curl "http://[fe80::1%25eth0]:8080/telemetry"   # works

But every request the async service makes to a zoned target fails at name
resolution before a single byte goes out:

    aiohttp.client_exceptions.ClientConnectorError:
        Cannot connect to host fe80::1%25eth0:8080 ssl:default
        [Name or service not known]

The operators note two clues:

- Requests to the very same appliance by its **global** IPv6 address (no zone)
  succeed.
- The hostname in the error message is `fe80::1%25eth0`. When they resolve that
  string by hand with `socket.getaddrinfo`, it also fails; but resolving
  `fe80::1%eth0` (with a bare `%`) succeeds. Something is handing the resolver a
  zone of `25eth0` instead of `eth0`.

The service never manipulates the host string itself — it hands the configured
URL to the client and lets the client take it from there.

## Your task

1. Determine where the zone identifier is being mangled. The failing request path
   runs from the client's connection setup, which pulls a host out of the parsed
   URL and passes it to the resolver, down into the URL library that actually
   parsed the authority and decides how an IPv6 host with a zone is stored and
   handed back. Identify the exact repository, file, and function where the
   `%25` -> `%` decoding is supposed to happen and does not, so the resolver
   receives a zone that begins with a stray `25`.

2. Trace the full path across the repository boundary: the client code that reads
   the host off the request URL and calls the resolver, and — in the URL library
   — the routine that split the bracketed IPv6 authority during parsing and the
   accessor that returns the host to the client without normalizing the zone
   separator. Name the file and the function responsible at each hop, and explain
   why the defect lives in the URL library rather than in the client whose name is
   on the exception.

3. Fix the defect at its real origin so that an IPv6 host carrying an RFC-encoded
   zone separator is normalized to the form the OS resolver expects (the zone
   after a single `%`, not `25...`), while a host built programmatically with a
   bare `%` keeps working. Make the change in the workspace and leave it as an
   uncommitted diff; do not paper over it in the client by string-rewriting the
   host before resolution.

## Deliverables

- In `answer.json`, alongside your analysis, include a top-level `citations` list
  with one entry per cited file: `{"repo": ..., "file": ..., "evidence_span": ...}`
  where `evidence_span` is a verbatim excerpt (at least 20 characters) copied from
  the cited file. Cover every file on the failure path across both repositories.
- An uncommitted code change at the true origin that decodes the encoded zone
  separator so the resolver receives the correct zone.
