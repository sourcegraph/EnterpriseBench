# Incident: Grafana Dashboards Show Incomplete Prometheus Data Without Errors

## Alert

Multiple users have reported that Grafana dashboards are displaying incomplete or empty data from Prometheus queries. No error messages are shown -- panels simply appear with partial results or empty charts.

## Symptoms

- Prometheus query panels show partial data or empty results
- No error banner, no red exclamation mark, no error in the query inspector
- The issue is intermittent and correlates with large query responses
- Both Prometheus and Loki datasources are affected
- The issue appeared after setting `[dataproxy] response_limit` in Grafana configuration

## Investigation So Far

The team has narrowed the issue to the response parsing pipeline:

1. Prometheus returns a valid, complete JSON response
2. Grafana's data proxy enforces `response_limit`, truncating the response body
3. The truncated JSON is passed to the response parser
4. The parser processes whatever valid JSON it can extract and silently stops
5. Partial data frames are returned to the frontend with no error

The JSON parser uses the `json-iterator/go` library (jsoniter), which has an unusual error handling pattern: methods like `ReadString()`, `ReadFloat64()`, etc. do NOT return errors. Instead, they set `iter.Error` which must be checked separately after each operation.

## Environment

- Grafana v10.1.0 at `/workspace/grafana/`
- Focus on the Go backend, specifically the Prometheus response converter. Prometheus itself
  returns a complete, valid JSON response (see the investigation notes above) -- the bug is
  entirely in how Grafana parses that response, so you will not need a Prometheus checkout.

## What I Need

1. **Root Cause**: Find the exact file and code where JSON parsing errors from jsoniter are not being checked. Where does `iter.Error` go unchecked?

2. **Error Chain**: Trace the full data flow: Prometheus HTTP API response -> Grafana data proxy (response_limit) -> response converter -> JSON parser. How does the truncated response become silent partial data?

3. **Affected Components**: Which Grafana datasources and code paths are affected by this unchecked error?

4. **Remediation**: How should the code be fixed? What should happen when `iter.Error` is set?

## Output

Write your findings to `/workspace/agent_output/INCIDENT_REPORT.md`.
