# CVE Blast Radius Analysis: axios ReDoS

## Context

A ReDoS (Regular Expression Denial of Service) vulnerability was reported in
the `axios` HTTP client library. Security wants the full blast radius across
our frontend infrastructure before we prioritize patching.

## Environment

`/workspace/axios` is the vulnerable library. The workspace also contains
every repo in our frontend ecosystem that might consume it — some declare it
directly, some only pull it in through an intermediary package. You will need
to search each repo's manifests and lockfiles to work out which is which.

## What I Need

1. **CVE Identification**: Confirm the CVE identifier, the affected package,
   and the vulnerable version range (and the version it was fixed in).

2. **Direct Dependents**: Which workspace repos declare `axios` directly?
   Cite the manifest file where the dependency is declared for each, and note
   whether it's a runtime or dev/test dependency.

3. **Transitive Paths**: For any repo that does *not* declare `axios` itself,
   trace how it actually gets pulled in — name every intermediary package in
   the chain from `axios` to that repo (A -> B -> C), and be explicit about
   which dependents are direct vs. transitive.

4. **Version Analysis**: For each consumer, check the resolved/pinned version
   in its lockfile and decide whether it falls inside the vulnerable range.

## Output

Write findings to `/workspace/BLAST_RADIUS.md`.
