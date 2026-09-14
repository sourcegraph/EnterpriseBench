# Map customer Alembic autogenerate producing empty migrations after SQLAlchemy 2.x model refactor to the Alembic env.py / SQLAlchemy MetaData boundary

A customer reports that after upgrading SQLAlchemy from 1.4 to 2.0
and refactoring their models into separate modules, alembic
revision --autogenerate produces empty migrations. The models clearly
differ from the database schema, but Alembic detects no changes.

Your task:
1. Find where Alembic's env.py imports the customer's MetaData and
   passes target_metadata to context.configure.
2. Find where SQLAlchemy 2.0's DeclarativeBase registers tables on a
   shared MetaData, and how splitting models across modules can
   create multiple unrelated MetaData instances.
3. Trace the cross-repo path: alembic command -> env.py import ->
   SQLAlchemy DeclarativeBase.metadata -> MetaData.tables -> Alembic
   autogenerate compare_metadata. Cite each file and the function
   responsible.
4. Identify why the customer's split-module refactor (each file
   declares its own DeclarativeBase) produces multiple MetaData
   instances, and only the one referenced in env.py is compared.
