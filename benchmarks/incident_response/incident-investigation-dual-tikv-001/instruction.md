# Incident: TiKV region peers flap during low-traffic periods, PD schedules removals

## Alert

A TiKV 8.5 cluster fronted by PD 8.5 shows region peers flapping during
low-traffic windows. Peers are marked down by the leader, PD schedules them
for removal, then the same peers re-appear seconds later only to be reported
down again. No underlying node failure, no network partition.

```
pd  | level=info msg="region heartbeat" region-id=4521 down-peers=[7]
pd  | level=info msg="schedule peer remove" region-id=4521 peer-id=7
tikv| INFO peer 7 awaken from hibernation, store=3
pd  | level=info msg="region heartbeat" region-id=4521 down-peers=[]
```

## Symptoms

- Flap correlates strictly with low-traffic periods (no client proposals for
  several seconds).
- Peers reported down come back online a beat later — the underlying TiKV
  process never crashed.
- The flap stops while the cluster is busy and resumes when traffic drops.
- Restarting a TiKV node clears the flap transiently while regions are
  awake; it returns once regions hibernate again.

## Environment

- `/workspace/tikv/` — `tikv/tikv` v8.5.0 (Rust)
- `/workspace/pd/`   — `tikv/pd` v8.5.0 (Go)

Both projects are part of the same cluster. The failure lives at the seam
between the two: something in TiKV's leader-side peer bookkeeping produces a
signal that PD's scheduler then acts on. You'll need to read both repos to
find where, and to explain why the signal is wrong in the first place.

## What I need

Investigate the two codebases and write an incident report grounded in the
actual code — cite the specific files and functions you found, not general
Raft/PD theory.

1. **Root cause** — name the specific file and function on each side: the
   TiKV code that decides whether a peer is "down," and the PD code that
   ingests that verdict and hands it to the scheduler. Cite each by path.
2. **Error chain** — trace from "cluster goes idle" to "peer removed and
   reappears a moment later," at least four hops, each tied to a code path
   you identified in the repos.
3. **Affected components** — identify every component across both repos
   that participates in the down-peer reporting and scheduling path.
4. **Remediation** — propose a concrete, code-grounded fix and say where it
   belongs (which repo and file).

## Output

Write your incident report to `/workspace/agent_output/INCIDENT_REPORT.md`.
