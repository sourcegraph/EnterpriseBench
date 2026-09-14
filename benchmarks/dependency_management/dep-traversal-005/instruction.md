# CVE Blast Radius Analysis: HPACK DoS in golang.org/x/net

## Context

CVE-2022-41723 is a denial-of-service vulnerability in the HPACK header compression implementation in golang.org/x/net. A malicious HTTP/2 client can craft headers that cause the server to consume excessive memory.

Our observability and infrastructure stack is all Go-based: Prometheus for monitoring, Consul for service discovery, Vault for secrets management. I don't yet know which of these actually depend on golang.org/x/net, directly or transitively, or which resolved versions they run.

## What I Need

1. **CVE Identification**: CVE, module, version range.

2. **Direct Dependents**: For each workspace repo, check its manifest to see whether it declares a dependency on the affected module.

3. **Dependency Paths**: Map how the affected module reaches each repo — directly, or transitively through an intermediary module (e.g. an HTTP/RPC framework the repo depends on). Some repos may pull it in only indirectly.

4. **Version Analysis**: For each repo, determine the version of the affected module it actually resolves to, and classify that repo as affected or patched relative to the CVE's version range.

## Output

Write findings to `/workspace/BLAST_RADIUS.md`.
