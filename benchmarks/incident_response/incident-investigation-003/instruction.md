# Incident: Grafana Dashboards Show Incomplete Prometheus Data Without Errors

## Alert

Multiple users have reported that Grafana dashboards are displaying incomplete or empty data from Prometheus queries. No error messages are shown -- panels simply appear with partial results or empty charts.

## Symptoms

- Prometheus query panels show partial data or empty results
- No error banner, no red exclamation mark, no error in the query inspector
- The issue is intermittent and correlates with large query responses
- Both Prometheus and Loki datasources are affected
- The issue appeared after setting `[dataproxy] response_limit` in Grafana configuration

## Environment

- Grafana v10.1.0 at `/workspace/grafana/`
- Prometheus v2.46.0 at `/workspace/prometheus/` (for understanding the API response format)
- Both repos are Go; the failure lives somewhere in the response-handling path
  between the two, so you will likely need to read both to understand the
  data flow.

## What I Need

1. **Root Cause**: Investigate why parsing errors from the response body are not surfacing as errors. Find the specific file and function(s) responsible, name the JSON parsing mechanism involved, and explain exactly how a truncated or malformed response ends up as silent partial data instead of a visible error.

2. **Error Chain**: Trace the full data flow, hop by hop, from the Prometheus HTTP API response through Grafana's data proxy (`response_limit` enforcement) to wherever the parsing error is ultimately dropped. Cite the file/function at each hop.

3. **Affected Components**: Which Grafana datasources and code paths share this bug? Cite the specific source files involved, not just the datasource names.

4. **Remediation**: Propose a concrete, code-grounded fix. Where exactly should error checks be added, and what should happen when a parsing error is detected?

## Output

Write your findings to `/workspace/grafana/INCIDENT_REPORT.md`.
