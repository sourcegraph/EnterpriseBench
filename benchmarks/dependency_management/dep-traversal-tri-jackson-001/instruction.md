# Dependency Trace: Jackson ObjectMapper through Spring Framework to Spring Boot

## Context

Jackson is the default JSON library in the Spring ecosystem. When a developer
registers a custom Jackson Module bean in a Spring Boot application, it should
be auto-detected and applied to all REST endpoints. A developer just reported
that their custom module's serializers are not being picked up by any REST
endpoint, even though the bean is registered in the ApplicationContext. The
path from bean definition to actual serialization crosses three repositories.

## Task

Trace how a custom Jackson Module bean flows from Spring Boot's
autoconfiguration layer, through the Spring Framework code that builds and
configures the shared ObjectMapper, into Jackson's own module-registration
machinery. Identify every hop in that chain and where a module bean could fail
to be picked up.

## Repos in Workspace

- `/workspace/jackson-databind/` -- jackson-databind 2.15.2 (upstream library)
- `/workspace/spring-framework/` -- Spring Framework 6.0.11 (intermediary)
- `/workspace/spring-boot/` -- Spring Boot 3.1.2 (consumer/autoconfigurer)

## Expected Output

Write `/workspace/DEPENDENCY_TRACE.md` containing, with each claim cited to a
specific file path and class/method name you found in the repos above:

1. The jackson-databind classes and methods responsible for module
   registration and resolution during JSON processing.
2. The Spring Framework class(es) that build and configure the ObjectMapper
   used for REST message conversion, and how they hook into Jackson's
   module-registration API.
3. The Spring Boot autoconfiguration class that auto-detects Module beans from
   the ApplicationContext and wires them into the builder -- including the
   annotations that gate when it activates and any intermediary bean(s) that
   carry module registrations from Spring Boot into the builder.
4. The complete class/method chain from Module bean definition to REST
   serialization, naming every class the call passes through.
