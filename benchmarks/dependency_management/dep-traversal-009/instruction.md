# Dependency Vulnerability Blast Radius Analysis

## Alert

A software composition analysis (SCA) scan flagged a security advisory
against a Python HTTP library used somewhere underneath our AWS tooling. The
advisory describes a cookie-header leak: when the library follows a
cross-origin redirect, it fails to strip the `Cookie` header, so a session
cookie set for the original host can be replayed against a different host at
the far end of the redirect.

The scanner's match is unreliable at the edges — it can't tell us for certain
which side of the fix a given consumer actually resolves to, or how many hops
deep the dependency reaches into our stack. That part is on us.

## Environment

- `/workspace/urllib3` — the flagged package
- `/workspace/requests` — a consumer pinned in this workspace
- `/workspace/boto3` — a consumer pinned in this workspace

## What I Need

1. **CVE Identification**: Identify the specific CVE (and advisory ID, if you
   can find one) that matches this description. Confirm the affected package
   and state both affected version ranges — there are two disjoint vulnerable
   ranges across different major version lines — along with the version(s)
   where each was fixed.

2. **Direct Dependents**: Which repo(s) in the workspace directly depend on
   the flagged package? Cite the dependency declaration you found and whether
   it looks like a runtime or a dev/test dependency.

3. **Transitive Chain**: Trace the full dependency path from the flagged
   package through every intermediary to the final consumer in the workspace,
   even where an intermediary isn't itself checked out as a repo here.
   Investigate how the dependency is managed at each hop — vendored, pinned
   exactly, or a version range — and cite what you found at each hop.

4. **Version Branch Analysis**: For each consumer, determine the actual
   resolved or pinned version of the flagged package — don't just take the
   top-level constraint at face value — and classify that consumer as
   affected or already patched relative to the vulnerable ranges from (1).

## Output

Write your analysis to `/workspace/BLAST_RADIUS.md`.
