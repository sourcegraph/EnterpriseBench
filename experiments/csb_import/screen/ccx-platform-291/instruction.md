# Elasticsearch Index Lifecycle Management Policy Engine

## Your Task

Find all Java source files in elastic/elasticsearch under x-pack/plugin/ilm/src/ and server/src/main/java/org/elasticsearch/cluster/metadata/ that implement Index Lifecycle Management (ILM). Identify the components that implement: the central lifecycle-management service that triggers policy evaluation, the lifecycle-policy model class and its named-stage model class, the transport handlers for the policy create and delete REST actions, the step execution framework (the cluster-state-mutating action step, the async wait step, and the async action step base classes), and the per-index lifecycle execution-state record stored in cluster metadata. Report each file path and key class.

## Context

You are working on a codebase task involving repos from the platform domain.

## Available Resources

## Output Format

Use the published task contract:

- `TASK_WORKDIR=/workspace`
- `TASK_REPO_ROOT=/workspace`
- `TASK_OUTPUT=/workspace/answer.json`

Create a file at `TASK_OUTPUT` (`/workspace/answer.json`) with your findings in the following structure:

```json
{
  "files": [
    {"repo": "org/repo-name", "path": "relative/path/to/file.go"}
  ],
  "symbols": [
    {"repo": "org/repo-name", "path": "relative/path/to/file.go", "symbol": "SymbolName"}
  ],
  "chain": [
    {"repo": "org/repo-name", "path": "relative/path/to/file.go", "symbol": "FunctionName"}
  ],
  "text": "Narrative explanation of your findings, citing repos and file paths."
}
```

Include only the fields relevant to this task. Your answer is evaluated against a closed-world oracle — completeness matters.

## Evaluation

Your answer will be scored on:
- **File recall and precision**: Did you find all relevant files?
