# Refactor Orchestration: Spring Kafka Listener Container Threading Model

## Context

Spring Kafka's listener container needs to support configurable concurrency
for message consumption — the current default is single-threaded. This
change spans three repositories with a real dependency chain:

- spring-framework provides the core messaging abstractions Spring Kafka
  builds on
- spring-kafka owns the Kafka-specific listener container implementation(s)
- spring-boot autoconfigures and exposes properties for listener containers

The dependency chain: spring-framework -> spring-kafka -> spring-boot

## Task

Investigate each repository to find the specific classes and configuration
surfaces that this change actually touches, then produce a topologically
sorted execution plan for the refactoring across all three repos, identifying
ordering constraints, parallelization opportunities, and breaking changes.
Ground each step in the actual code you find — a plan that only restates the
repo names and roles above is not sufficient.

## Repos in Workspace

- `/workspace/spring-framework/` -- Spring Framework 6.0.11 (upstream abstractions)
- `/workspace/spring-kafka/` -- Spring Kafka 3.0.9 (primary implementation)
- `/workspace/spring-boot/` -- Spring Boot 3.1.2 (autoconfiguration consumer)

## Expected Output

Write `/workspace/REFACTOR_PLAN.md` containing:

1. A numbered list of repos in the order they should be updated, each entry
   citing the specific class(es) or file(s) in that repo the change touches
2. Dependency graph (who depends on whom)
3. Parallelization annotations (which steps can run concurrently)
4. Breaking vs. compatible change annotations per step, grounded in the
   actual container/config classes involved
5. Risk assessment for each repo change
