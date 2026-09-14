# big-code-camel-arch-001: Apache Camel Message Routing Architecture

## Task

Trace how Apache Camel routes a message from endpoint reception through its internal message-processing machinery to a destination. Map the complete Component→Endpoint→Consumer→Processor→Producer hierarchy: how a component creates addressable endpoints, how those endpoints hand inbound messages to consumers, how the message moves step-by-step through processing, and how it finally reaches a downstream endpoint via a producer.

## Context

- **Repository**: apache/camel (Java, ~2.8M LOC)
- **Codebase**: Available at `/workspace/camel/`
- **Category**: Architectural Understanding
- **Difficulty**: hard
- **Subsystem Focus**: core/ — the Camel core routing engine (camel-api, camel-support, camel-core-processor, camel-base-engine, camel-core-reifier)

## Requirements

1. Identify all relevant components in the Component→Endpoint→Consumer→Processor→Producer hierarchy (API interfaces + base implementations) and explain each one's responsibility.
2. Trace the dependency chain from how a developer declares a route in code to how that becomes the live, running structure that actually processes messages at runtime — name every class/module the chain passes through.
3. Document how the individual message-processing steps are strung together into a single execution path, and how cross-cutting concerns (error handling, instrumentation, interception) get layered around each step without the steps themselves knowing about it.
4. Explain the specific mechanism (name the class and the module it lives in) that converts the declarative route model into the runtime objects that actually process messages.

## Expected Output

Write your analysis to `/logs/agent/solution.md` with the following structure:

```
## Files Examined
- path/to/file1.ext — role in architecture
- path/to/file2.ext — role in architecture
...

## Dependency Chain
1. Entry point: path/to/entry.ext
2. Calls: path/to/next.ext (via function/method name)
3. Delegates to: path/to/impl.ext
...

## Analysis
[Detailed architectural analysis including:
- Design patterns identified
- Component responsibilities
- Data flow description
- Interface contracts between components]

## Summary
[Concise 2-3 sentence summary answering the task question]
```

## Evaluation Criteria

- File recall: Did you find the correct set of architecturally relevant files?
- Dependency accuracy: Did you trace the correct dependency/call chain?
- Architectural coherence: Did you correctly identify the design patterns and component relationships?
