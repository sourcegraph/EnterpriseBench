# CVE Blast Radius Analysis: OpenSSL BN_mod_sqrt Infinite Loop

## Alert

Security scanning flagged CVE-2022-0778 in your fleet: a denial-of-service in
OpenSSL's `BN_mod_sqrt()` function. A crafted certificate with an invalid
explicit curve parameter can trigger an infinite loop during certificate
verification. OpenSSL is a system-level C library, so the actual blast radius
depends on *how* each downstream project links against it — a detail that
differs per repo and isn't visible from the advisory alone.

## Context

Three workspace repos are known to sit somewhere downstream of OpenSSL:
`curl`, `git`, and `kubernetes`. Some of these link against OpenSSL directly,
some only transitively through another consumer, and some may not touch
OpenSSL's C implementation at all (e.g. a pure-language TLS stack would be
immune regardless of the system OpenSSL version). You don't yet know which is
which — that's what you need to establish by reading each repo's own build
configuration and dependency declarations.

## What I Need

1. **CVE identification**: confirm the CVE and determine, from OpenSSL's own
   release history, the full set of affected version branches and the
   version(s) that carry the fix.

2. **Cross-ecosystem consumers**: for each of the three workspace repos (and
   any other language ecosystems you find pulled in along the way — e.g. a
   scripting language's standard TLS module, or a package manager that
   depends on it), identify whether and how it depends on OpenSSL. Cite the
   actual build/dependency file(s) in each repo where you found this (its
   build system config, module/package manifest, or vendoring directory —
   whichever applies to that repo).

3. **Linking strategy analysis**: for each consumer, determine from its build
   configuration whether it:
   - Dynamically links (uses whatever OpenSSL is on the system — affected if
     system OpenSSL is vulnerable)
   - Statically links / vendors a specific OpenSSL version (check which
     version, and whether that pinned version falls in the vulnerable range)
   - Doesn't use OpenSSL's C implementation at all (immune regardless of
     system OpenSSL version)

4. **Transitive paths**: trace at least one full chain showing how a
   downstream consumer pulls in OpenSSL indirectly (not just "uses OpenSSL"
   but through which intermediary package/tool).

5. **Actual exposure**: combine linking strategy with resolved/pinned OpenSSL
   version to classify each repo as affected, conditionally affected, or not
   affected — and say why, citing the file that justifies the classification.

## Output

Write findings to `/workspace/BLAST_RADIUS.md`.
