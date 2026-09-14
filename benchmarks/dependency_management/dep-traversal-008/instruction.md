# CVE Blast Radius Analysis: requests Proxy-Authorization Header Leak

## Context

CVE-2023-32681 affects the `requests` Python HTTP library: when making requests through an HTTP proxy, the `Proxy-Authorization` header can be incorrectly forwarded to the destination server on redirects. This is a credential leak vulnerability, publicly disclosed with an affected version range of `>= 2.3.0, < 2.31.0` (fixed in 2.31.0).

Our AWS infrastructure automation is built on boto3 and awscli. Both sit somewhere downstream of `requests` in the Python dependency graph — how far downstream, and through which intermediate package(s), is something you need to trace from the actual manifests and source trees in this workspace, not from general familiarity with the AWS SDK ecosystem. If our AWS tooling is running through a proxy (which it is in our corporate environment), a vulnerable chain here could leak proxy credentials.

## What I Need

1. **CVE Identification**: Confirm the CVE, the affected package, and the vulnerable version range. Then check the actual version of `requests` present in this workspace and state whether it falls in the vulnerable range.

2. **Direct Dependents**: Which repos in this workspace declare a dependency on `requests` — directly or via a vendored/in-tree copy — and where exactly is that declaration made? Check setup.py, setup.cfg, and requirements files, but don't assume every dependency here is declared the same way.

3. **Transitive Chain**: Map the path(s) by which each repo in the workspace ends up depending on `requests`, however many hops that takes. For each hop, cite the specific file that declares (or vendors) it.

4. **Version Analysis**: For each consumer, determine what version (or version range) of the upstream package it actually pins or vendors, and whether that puts it in the affected range.

## Output

Write findings to `/workspace/BLAST_RADIUS.md`.
