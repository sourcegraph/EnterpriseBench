# CVE Blast Radius Analysis: protobufjs Prototype Pollution

## Context

CVE-2022-25878 is a prototype pollution vulnerability in the `protobufjs` npm package. The affected versions are 6.10.0-6.10.2 and 6.11.0-6.11.2. This is the JavaScript protobuf implementation underlying our gRPC stack.

Our Node.js services use gRPC for inter-service communication, and our Kubernetes tooling also talks to the API server over gRPC. I need to understand how deep this goes.

## What I Need

1. **CVE Identification**: CVE, package, affected version ranges (there are two affected ranges).

2. **Direct Dependents**: Search the workspace repos for manifests that declare `protobufjs` as a dependency. Name each dependent and cite the manifest file where you found it.

3. **Transitive Paths**: Some consumers don't depend on `protobufjs` directly — they pull it in through an intermediary. Trace at least one full chain from `protobufjs` through to a downstream consumer, citing the manifest at each hop. (One of the repos here is a monorepo with several package manifests — you'll need to find the one that actually declares the dependency.)

4. **Version Analysis**: For each consumer you find, check the resolved `protobufjs` version in its lock file and classify it as affected or already patched.

## Output

Write findings to `/workspace/BLAST_RADIUS.md`.
