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

## Investigation So Far

The team has narrowed the issue to the interaction between batch processing and database transactions:

1. Kafka Connect's WorkerSinkTask handles the put() -> flush() -> commit cycle
2. The JDBC Sink Connector wraps batch inserts in a database transaction
3. The offset commit happens after the JDBC transaction is committed
4. When the batch is large, the JDBC transaction takes longer than the DB timeout (PostgreSQL `statement_timeout` = 30s)
5. The error propagation from JDBC driver through the connector to the Connect framework obscures the root cause
6. The WorkerSinkTask's `offset.flush.timeout.ms` interacts with the DB timeout

## Environment

- Apache Kafka 3.6.0 at `/workspace/kafka/`
- Confluent JDBC Connector v10.7.4 at `/workspace/kafka-connect-jdbc/`
- Focus on Java code in both repos
- Connector config: `batch.size=5000`, PostgreSQL `statement_timeout=30s`

## What I Need

1. **Root Cause**: Find where Kafka Connect's WorkerSinkTask orchestrates the put/flush/commit cycle and where the JDBC connector manages database transactions.

2. **Error Chain**: Trace the full failure: large batch -> long JDBC transaction -> DB timeout -> connector exception -> task failure -> restart loop.

3. **Affected Configurations**: Which connector and Connect framework settings interact to cause this?

4. **Remediation**: How should batch size, timeouts, or transaction management be configured?

## Output

Write your findings to `/workspace/agent_output/INCIDENT_REPORT.md`.
