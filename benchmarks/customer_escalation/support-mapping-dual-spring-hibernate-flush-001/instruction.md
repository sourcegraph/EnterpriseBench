# Map customer Spring Boot @Transactional updates not flushing before save to the Spring TransactionTemplate / Hibernate Session.flush boundary

A customer reports that their Spring Boot 3 service writes are
occasionally invisible to a downstream read inside the same
@Transactional method. The trace shows a save() call followed by a
JDBC query against the same row that returns the OLD value, even though
Hibernate's first-level cache shows the new entity. The customer
expected the dirty entity to be flushed before the query.

Your task:
1. Find where Spring's TransactionTemplate / @Transactional configures
   the JpaTransactionManager and how it triggers flush at synchronization
   points.
2. Find where Hibernate's Session.flush decides whether to write
   pending changes to the database (FlushMode.AUTO vs MANUAL) and how
   it interacts with native query execution.
3. Trace the cross-repo path: Spring TransactionTemplate.execute ->
   PlatformTransactionManager beforeCommit -> JpaTransactionManager
   -> EntityManager flush -> Hibernate SessionImpl.flush ->
   ActionQueue execute. Cite each file and the function or attribute
   responsible.
4. Identify why a native SQL query inside the same transaction does
   not auto-flush pending changes the way HQL does, and what
   FlushMode setting closes the gap.
