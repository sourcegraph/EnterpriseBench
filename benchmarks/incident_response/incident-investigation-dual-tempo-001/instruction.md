# Incident: One Tempo ingester saturates "live traces" while peers stay flat

## Alert

A Grafana Tempo 2.6.0 distributed cluster on Kubernetes (12 ingesters, N
distributors) is paging on a single hot ingester. The other 11 are flat:

```
ingester-7  tempo_ingester_live_traces ~ 250K -> 2.97M (3M cap)
ingester-*  tempo_ingester_live_traces ~ 240K-260K (steady)
distributor level=warn msg="pusher failed" err="TRACE_TOO_LARGE limit hit on ingester-7"
distributor level=warn msg="ring has 1 unhealthy instance" instance=ingester-3
ingester-7  level=warn msg="flush queue length high" queue=1024 lock_wait_ms=18000
```

## Symptoms

- Skew only appears while at least one peer ingester is unresponsive but
  has not yet been evicted from the ring (heartbeat still inside the
  unhealthy timeout window).
- Once heartbeats time out and the unhealthy instance is dropped, traffic
  rebalances across all live ingesters within ~30s.
- Restarting ingester-7 OR removing the unhealthy peer from the ring KV
  store immediately clears the hot spot.
- The slow ingester logs long lock waits in its flush loop while the
  distributor keeps shipping it more traces.

## Environment

- `/workspace/tempo/` — `grafana/tempo` v2.6.0
- `/workspace/dskit/` — `grafana/dskit` pinned via tempo's go.mod
  (pseudoversion `v0.0.0-20240801171758-736c44c85382`)

Both are Go projects.

## What I need

Investigate both codebases and write an incident report grounded in the
actual code — cite the specific files and functions you found, not
general ring/gossip theory.

1. **Root cause** — name the specific file and function on each side of
   the failure: the dskit code that decides whether a ring member still
   counts as a valid target, and the Tempo ingester code responsible for
   accumulating and draining traces. Cite each by path.
2. **Error chain** — trace from "a peer ingester goes unresponsive but
   isn't evicted yet" to "one ingester's live-trace count spikes to the
   cap while its peers stay flat," at least four hops, each tied to a
   code path you identified.
3. **Affected components** — identify every component across both repos
   that participates in the failure path.
4. **Remediation** — propose a concrete, code-grounded fix and say where
   it belongs (which repo and file).

## Output

Write your incident report to `/workspace/agent_output/INCIDENT_REPORT.md`.
