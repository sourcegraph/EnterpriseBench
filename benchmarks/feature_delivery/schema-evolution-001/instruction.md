# Schema Change: Add Deactivation Support to User Groups

## Context

We've been getting requests from large organizations to be able to deactivate user groups without deleting them. Right now, deleting a group is permanent and breaks any references to it in stream permissions, message mentions, etc.

The plan is to add a `deactivated` boolean field to the `NamedUserGroup` model. The migration is already written (`zerver/migrations/0578_namedusergroup_deactivated.py`), but before we proceed I need a full impact analysis of what code needs to change.

## What I Need

1. **Schema change identification**: Confirm the migration and model change.

2. **Direct code references**: Find all Python code that queries, creates, updates, or filters `NamedUserGroup` objects. This includes the actions layer, views, and utility functions. We need to make sure deactivated groups are excluded from active queries but still accessible for admin operations.

3. **Indirect references**: This is the tricky part. The `NamedUserGroup` model touches:
   - The real-time event system (events sent to clients when groups change)
   - The API schema (OpenAPI spec in `zerver/openapi/`)
   - Realm import/export
   - Audit logging
   - Markdown rendering (group mentions like `@*groupname*`)
   - URL routing for group endpoints

   I need to know every place that needs to learn about the `deactivated` state.

4. **Test coverage**: Which test files exercise user group functionality and would need new test cases for deactivation scenarios?

Write your complete analysis to `/workspace/agent_output/SCHEMA_IMPACT.md` with sections for each category above, including file paths and brief descriptions of required changes.
