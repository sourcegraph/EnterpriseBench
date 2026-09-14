# Schema Change: Migrate Export Tracking from JSON to Database Model

## Context

Our realm export system currently tracks export jobs using a JSON file stored on disk. This has been a pain point — it's fragile, doesn't survive server restarts cleanly, and makes it hard to query export history. We're replacing it with a proper `RealmExport` database model.

A new migration under `zerver/migrations/` creates the table and backfills existing export records from the on-disk JSON state — find it and confirm what it does.

## What I Need

Before we merge this, I need a complete picture of what code touches the export tracking system:

1. **Schema change**: Confirm the new model definition and how the backfill migration works.

2. **Direct references**: Find the model registration (where it's imported in `__init__`), the actions layer that creates/updates export records, and the views that serve export status to the frontend.

3. **Indirect references**: The export system has tentacles — trace every place that depends on export state without importing the model directly:
   - The background worker that actually runs the export job asynchronously
   - Management commands for CLI-based exports
   - The underlying export/tarball-building logic that the worker invokes
   - Anywhere the old JSON-based tracking was read or written

4. **Test coverage**: Which test files need updating? We have dedicated export tests, import/export round-trip tests, event tests, and management command tests.

Write your findings to `/workspace/zulip/SCHEMA_IMPACT.md`.
