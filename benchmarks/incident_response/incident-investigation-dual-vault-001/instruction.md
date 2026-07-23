# Incident: Vault follower stuck in candidate loop after backup-seeded rejoin

## Alert

A Vault 1.18.1 cluster using Integrated Storage reports a follower that never
catches up after the SRE on call replaced a failed node and seeded its data
directory from a recent backup. Leader logs:

```
[WARN]  storage.raft: failed to install snapshot: error="..."
[ERROR] storage.raft: failed to copy snapshot: error="short read"
[INFO]  storage.raft: ignoring installSnapshot request with older term than current term: request-term=14 current-term=17
[INFO]  storage.raft: entering candidate state: node="follower-3" term=18
```

## Symptoms

- The new follower receives the snapshot-install RPC from the leader, but the
  restore never lands and it never becomes a healthy voter.
- The follower loops between follower and candidate state, term monotonically
  increasing, committed index frozen.
- Restarting the follower does not help; cluster is otherwise healthy.
- The seeded backup predates a leadership change that happened roughly two
  hours before the backup was captured.

## Environment

- `/workspace/vault/` — `hashicorp/vault` v1.18.1
- `/workspace/raft/`  — `hashicorp/raft` v1.7.1

Both Go projects. The failure lives at the seam between Vault's storage layer
and the underlying raft library; you will need to read both repos to locate it.

## What I need

Investigate the two codebases and write an incident report grounded in the
actual code — cite the specific files and functions you found, not general
raft theory.

1. **Root cause** — name the specific file and function on each side of the
   failure: the Vault storage code that persists and restores raft snapshots,
   and the raft-library code that receives and applies an incoming snapshot.
   Cite each by path.
2. **Error chain** — trace from "failed node replaced from backup" to the
   candidate loop, at least four hops, each hop tied to a code path you
   identified.
3. **Affected components** — identify every component across both repos that
   participates in the snapshot restore-and-apply path.
4. **Remediation** — propose a concrete, code-grounded fix and say where it
   belongs (which repo and file).

## Output

Write your incident report to `/workspace/agent_output/INCIDENT_REPORT.md`.
