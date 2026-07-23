# Schema Change: Move Avatar Source from User to Realm Level

## Context

We're changing how default avatar sources work. Currently `default_avatar_source` lives on each `UserProfile`, but it should really be a realm-level setting — organizations choose whether to use Gravatar or generated avatars, not individual users.

The migration `zerver/migrations/0776_realm_default_avatar_source.py` adds the field to Realm and the corresponding removal happens from UserProfile.

## What I Need

This field is referenced in more places than you'd expect. I need the full picture:

1. **Schema change**: The migration file and both model files (`realms.py` for the addition, `users.py` for the removal).

2. **Direct references**: The core avatar logic in `lib/avatar.py` that reads this field. Also check the API examples — some may reference avatar source in example payloads.

3. **Indirect references**: This is where it gets interesting. We have importers for migrating from other chat platforms (Slack, Mattermost, Rocket.Chat) and each one sets `default_avatar_source` during import. All of them need updating. Also check frontend templates and any dependency changes (we're patching a JavaScript library for avatar generation).

4. **Test coverage**: We need updates in upload tests, importer tests, and event tests.

Write your findings to `/workspace/agent_output/SCHEMA_IMPACT.md`.
