# Incident: NATS JetStream pull consumer reports "bad pending entry" and silently drops messages

## Alert

A production FORWARDER service consuming a high-volume JetStream stream
through a durable pull-based subscription is logging intermittent errors,
and a downstream auditor is reporting gaps in delivery:

```
[WRN] Consumer 'STX_DATA > FORWARDER' error on write store state from
      check pending: bad pending entry, sequence [7762827] out of range
nats: pull subscribe: no messages available
audit: gap detected: stream seq 7762820..7762830 missing from sink
```

## Symptoms

- Errors only appear once the stream has been running long enough for its
  size/age retention limits to start reclaiming space.
- Disabling retention (or raising the limits) makes the failure go away.
- Restarting the consumer does not heal the state — only deleting and
  recreating the durable consumer clears the error.
- The client gets `no messages available` and retries; subsequent fetches
  succeed but the application never sees the missing sequence range, and
  no error is ever surfaced to it.

## Environment

- `/workspace/nats-server/` — `nats-io/nats-server` v2.10.22
- `/workspace/nats.go/` — `nats-io/nats.go` v1.37.0

Both Go projects. The failure spans the wire boundary between the two —
you will need to read both repos to find it.

## What I need

Investigate both codebases and write an incident report grounded in the
actual code — cite the specific files and functions you found, not general
JetStream theory.

1. **Root cause** — name the specific file and function on each side of
   the client/server boundary that participate in the failure, and explain
   why the interaction between them produces "bad pending entry, sequence
   out of range".
2. **Error chain** — trace the cross-repo path, at least four hops, from
   the client's pull request through whatever the server does with
   outstanding deliveries, to the piece of the storage layer that reclaims
   old data, and back to the silent gap observed by the client.
3. **Affected components** — identify every component across both repos
   that participates in this path (do not just repeat the repo names).
4. **Remediation** — propose a concrete, code-grounded fix and say where
   it belongs (which repo and file).

## Output

Write your incident report to `/workspace/agent_output/INCIDENT_REPORT.md`.
