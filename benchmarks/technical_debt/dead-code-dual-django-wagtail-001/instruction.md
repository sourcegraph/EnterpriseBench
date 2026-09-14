# Identify dead code paths in Wagtail's StreamField that reference deprecated Django JSONField internals

Wagtail's StreamField is a complex rich content field that stores structured
data as JSON in a Django JSONField. When Django 3.1 moved JSONField from
django.contrib.postgres to django.db.models, Wagtail maintained backward
compatibility shims. Now on Django 5.0, these compatibility paths are dead code.

Your task:
1. Find all Wagtail code paths that reference the old django.contrib.postgres.fields.JSONField
   import or its associated form field, widget, and lookup classes. These are dead
   code on Django 5.0+.
2. Trace the StreamField initialization chain in Wagtail — from field declaration
   through form widget rendering to identify which intermediate layers still contain
   compatibility branches for the old JSONField location.
3. Find all Django 5.0 deprecation removals related to JSONField — identify which
   django.contrib.postgres paths were removed and what Wagtail code would break if
   the compatibility shims were removed without updating.
4. Document each dead code location with: file path, function/class, the dead branch
   condition, and whether removing it would require a Wagtail migration.

Document in your answer (use these JSON fields):
{
  "dead_code_locations": [
    {
      "file": "<file path>",
      "class_or_function": "<name>",
      "dead_branch_condition": "<condition that is always true/false on Django 5.0>",
      "removal_requires_migration": true/false,
      "explanation": "<why this is dead code>"
    }
  ]
}
