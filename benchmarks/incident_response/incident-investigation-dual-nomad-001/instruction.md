# Incident: Nomad allocations stuck pending after a routine leader transition

## Alert

A HashiCorp Nomad 1.9.3 cluster reports allocations stuck indefinitely in
`pending` after the leader was drained for OS patching and a new leader was
elected. New job submissions for the same set of jobs are also stuck.

```
[INFO]  nomad: cluster leadership acquired
[WARN]  nomad.plan: plan for node rejected: node_id=... reason="node is not ready for placements"
[ERROR] worker: failed to dequeue evaluation: error="eval broker disabled"
[WARN]  nomad: plan submitted before last plan applied; refusing
[INFO]  client.alloc_runner: waiting for allocation: alloc_id=... status=pending
```

## Symptoms

- Allocations are committed to raft (visible via operator tooling) but the
  scheduler workers behave as if they don't exist.
- Restarting the new leader clears the failure transiently — it returns
  during the next leadership change under load.
- A clean handover with no plan churn does not reproduce.
- The bug only appears when there is a backlog of recently committed
  plan-apply entries at the moment of leadership transfer.

## Environment

- `/workspace/nomad/` — `hashicorp/nomad` v1.9.3
- `/workspace/raft/`  — `hashicorp/raft`  v1.7.1

Both Go projects. The failure lives at the seam between Nomad's
leader-establishment / scheduling code and the underlying raft library's
log-commit machinery; you will need to read both repos to locate it.

## What I need

Investigate the two codebases and write an incident report grounded in the
actual code — cite the specific files and functions you found, not general
raft theory.

1. **Root cause** — name the specific file and function on each side of the
   timing gap: the Nomad code that stands up leadership duties and applies
   scheduler plans, and the raft-library code responsible for marking log
   entries committed versus actually applying them to the state machine.
   Cite each by path.
2. **Incident chain** — trace at least four hops from the leadership
   transition to scheduler workers rejecting plans against stale state, each
   hop tied to a code path you identified.
3. **Affected components** — identify every component across both repos that
   participates in the leadership-establishment-to-plan-apply path.
4. **Remediation** — propose a concrete, code-grounded fix and say where it
   belongs (which repo and file).

## Output

Write your incident report to `/workspace/agent_output/INCIDENT_REPORT.md`.
