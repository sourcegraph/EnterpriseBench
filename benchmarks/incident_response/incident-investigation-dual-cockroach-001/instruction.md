# Incident: CockroachDB tenant range returns deleted rows after TRUNCATE

## Alert

A CockroachDB v24.2.5 cluster (Pebble v1.1.2) is reporting consistency
warnings and customer-visible read corruption on a single tenant range
after a TRUNCATE was issued against a large table:

```
ERROR: consistency check failed: range r4217 has 1432 keys above MVCC GC
       threshold that should have been removed by TRUNCATE
WARN:  pebble: range key invariant violated during compaction of L6 sstable
WARN:  replica r4217: read returned 1432 deleted rows; expected 0
```

## Symptoms

- Reads on the affected range return rows that the TRUNCATE was supposed
  to delete.
- The corruption appears only after a background L6 compaction runs across
  the sstable that holds the TRUNCATE's bulk delete.
- New ranges and untouched ranges are unaffected.
- Restarting the node does not clear the data; only manual range repair
  removes the resurrected keys.

## Environment

- `/workspace/cockroach/` — `cockroachdb/cockroach` v24.2.5
- `/workspace/pebble/` — `cockroachdb/pebble` v1.1.2

Both Go projects. CockroachDB embeds Pebble directly as its storage engine.
The team suspects a storage-engine bug rather than a SQL or transactional
issue, but has not yet located it in either codebase.

## What I need

Investigate both codebases and write an incident report grounded in the
actual code — cite the specific files and functions you found, not general
LSM-tree or MVCC theory.

1. **Root cause** — name the specific file and function on each side of the
   bug: the CockroachDB code that carries the TRUNCATE's bulk SST across the
   KV/Raft apply path, and the Pebble code that decides, during compaction,
   whether to drop or carry forward a given key. Cite each by path.
2. **Corruption chain** — trace from the TRUNCATE statement to the
   resurrected rows, at least four hops, each hop tied to a code path you
   identified.
3. **Affected components** — identify every component across both repos
   that participates in the path from SST ingest through the compaction
   that corrupts the data.
4. **Remediation** — propose a concrete, code-grounded fix and say where it
   belongs (which repo and file).

## Output

Write your incident report to `/workspace/agent_output/INCIDENT_REPORT.md`.
