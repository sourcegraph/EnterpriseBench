# Schema Change: Replace Channel Creation Policy with Group-Based Permission

## Context

We're migrating our permission model from simple integer policies to group-based permissions. The first target is channel (stream) creation: replacing `create_public_stream_policy` (an integer enum) with `can_create_public_channel_group` (a FK to `NamedUserGroup`).

This is a multi-step migration:
1. `0532` — Add the new FK column `can_create_public_channel_group`
2. `0533` — Backfill: map old integer policy values to corresponding system groups
3. `0534` — Alter column constraints (make non-nullable)
4. `0535` — Remove the old `create_public_stream_policy` column

## What I Need

This change crosses the full stack — backend model through API through frontend settings UI. I need a complete map before we ship:

1. **Migration sequence**: Verify all 4 migrations and the model change in `realms.py`.

2. **Backend references**: Find all Python code that reads or writes `create_public_stream_policy` or will need to use `can_create_public_channel_group` — realm settings actions, realm creation, views, the event system (both `events.py` and `event_schema.py`).

3. **Frontend references**: This is the part I'm most worried about. The settings UI in `web/src/` has multiple files that render and handle channel creation permissions:
   - Settings components and data files
   - The Handlebars template for organization permissions
   - State management and initialization
   - Event dispatch handlers

   Find all of them.

4. **Test coverage**: We need test updates on both sides — Python tests for events, realm settings, subscriptions; JS tests for settings UI, dispatch, and the admin E2E test.

Write your complete analysis to `/workspace/agent_output/SCHEMA_IMPACT.md`.
