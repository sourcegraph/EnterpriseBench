# Incident: Cilium agent intermittently fails to load BPF programs during high pod churn

## Alert

Cilium 1.16.4 agents on a Kubernetes cluster log intermittent BPF program
load failures during periods of high pod churn:

```
level=error msg="Failed to load policy program" error="program too large"
level=error msg="failed to attach lxc_egress" error="map create: invalid argument"
fatal: panic: BPF map definition corrupt: max_entries=2147483647
```

## Symptoms

- Errors only occur when multiple endpoint regenerations happen in the same
  second.
- The agent recovers when the workload calms; restarting the agent clears
  the failure transiently.
- A debug build with the Go race detector confirms a `DATA RACE` somewhere
  in the BPF loading path when two regenerations run concurrently — the
  detector output alone does not pinpoint which function or data structure
  is being torn.

## Environment

- `/workspace/cilium/` — `cilium/cilium` v1.16.4
- `/workspace/ebpf/` — `cilium/ebpf` v0.16.0

Both Go projects. The failure lives at the seam between Cilium's datapath
loading code and the underlying `cilium/ebpf` library; you will need to read
both repos to locate it.

## What I need

Investigate the two codebases and write an incident report grounded in the
actual code — cite the specific files and functions you found, not general
eBPF theory.

1. **Root cause** — name the specific file and function on each side of the
   race: the Cilium code that prepares and hands off BPF objects during
   endpoint regeneration, and the `cilium/ebpf` code that consumes them when
   creating a map. Explain what shared, mutable data structure is actually
   being torn.
2. **Race trace** — trace, hop by hop, how two concurrent endpoint
   regenerations reach the same shared resource and produce the observed
   failure. Tie each hop to a code path you identified.
3. **Affected components** — list every component across both repos that
   participates in this failure path.
4. **Remediation** — propose a concrete, code-grounded fix and say where it
   belongs (which repo and file).

## Output

Write your incident report to `/workspace/INCIDENT_REPORT.md`.
