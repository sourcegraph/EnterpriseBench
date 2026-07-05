# config-drift-tri-javax-jakarta-spring-001

**Suite:** platform_engineering | **Type:** config_drift | **Difficulty:** expert
**Repos:** spring-boot + hibernate-orm + tomcat

## Context

The Jakarta EE migration changed the namespace from javax.* to jakarta.*
across the entire Java ecosystem. Spring Boot 3.0 adopted jakarta, requiring
all dependencies to also migrate.

Your task:
1. Find where Spring Boot 3.0 switched from javax to jakarta imports
2. Find where Hibernate 6 made the jakarta migration
3. Find where Tomcat 10 adopted the jakarta namespace
4. Identify any configuration drift where one component uses javax
   while another expects jakarta, causing ClassNotFoundException

## Expected Output

Write your analysis to `/workspace/analysis/IMPACT_REPORT.md`.

Your report should include:
- File paths from each repository that are relevant
- Specific code symbols, functions, or types involved
- The nature of each change (removal, rename, signature change, behavioral)
- Migration recommendations

## Hints

- All repos are cloned under `/workspace/`
- Focus on import/dependency declarations first, then trace into implementation
- Check version constraints in dependency manifests (pom.xml / build.gradle)
