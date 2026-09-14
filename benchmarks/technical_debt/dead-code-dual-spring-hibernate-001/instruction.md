# Identify dead Hibernate 5 compatibility code paths in Spring Boot 3.x JPA auto-configuration

Spring Boot 3.x requires Hibernate 6.x (Jakarta EE namespace). However, Spring
Boot's JPA auto-configuration still contains compatibility branches and property
mappings that were needed for Hibernate 5.x support. These are dead code on
Spring Boot 3.x since Hibernate 5 is no longer a supported runtime.

Your task:
1. Find all Spring Boot auto-configuration code that branches on Hibernate version
   detection — look for version checks, class existence probes (e.g.,
   ClassUtils.isPresent for Hibernate 5-specific classes), and conditional
   bean registration that only activates for Hibernate 5.
2. Find the Hibernate 6 classes that replaced Hibernate 5 equivalents — identify
   which Spring Boot compatibility bridges reference classes that no longer exist
   in Hibernate 6 (e.g., org.hibernate.dialect.* changes, criteria API changes).
3. Trace the Spring Boot JPA auto-configuration chain from
   HibernateJpaAutoConfiguration through EntityManagerFactory creation to identify
   dead property mappings that reference removed Hibernate 5 configuration keys.
4. Document each dead code location with: file, class/method, the version check
   condition, and what Hibernate 6 replacement exists.

Document in your answer (use these JSON fields):
{
  "dead_code_locations": [
    {
      "file": "<file path>",
      "class_or_function": "<name>",
      "dead_branch_condition": "<Hibernate 5 version check that always fails>",
      "hibernate6_replacement": "<what replaced it in Hibernate 6>",
      "explanation": "<why this is dead on Spring Boot 3.x>"
    }
  ]
}
