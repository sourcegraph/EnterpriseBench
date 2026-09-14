# Incident: JDBC Sink Connector Transaction Timeout and Restart Loop

## Alert

A production Kafka Connect cluster running JDBC Sink Connectors is experiencing periodic task failures with transaction timeout errors. Tasks enter a restart loop after processing large batches.

## Symptoms

- JDBC Sink Connector tasks fail with: "org.apache.kafka.connect.errors.ConnectException: java.sql.SQLException: transaction timeout"
- The failure occurs during offset commit after a large batch is processed
- Task restarts and reprocesses the same batch, causing duplicates
- The issue is intermittent and correlates with batch sizes > 3000 records
- Connector status shows tasks cycling between RUNNING and FAILED
- The JDBC connection pool shows connections in "idle in transaction" state

## Environment

- Apache Kafka 3.6.0 at `/workspace/kafka/`
- Confluent JDBC Connector v10.7.4 at `/workspace/kafka-connect-jdbc/`
- Focus on Java code in both repos
- Connector config: `batch.size=5000`, PostgreSQL `statement_timeout=30s`

## What I Need

1. **Root Cause**: Investigate both codebases and identify, by file and function, the Kafka Connect component that drives a sink task's poll/put/flush/offset-commit cycle, and the JDBC connector component that owns the database transaction around a batch insert. Explain how the two interact to produce this failure.

2. **Error Chain**: Trace the full failure path, from the large batch being handed to the connector through to the task entering a restart loop, tying each hop to the actual code you found.

3. **Affected Configurations**: Which connector and Connect framework settings interact to cause this?

4. **Remediation**: How should batch size, timeouts, or transaction management be configured?

## Output

Write your findings to `/workspace/kafka/INCIDENT_REPORT.md`.
