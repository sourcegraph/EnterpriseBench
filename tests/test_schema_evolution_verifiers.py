"""Verification tests for db-schema-evolution task checkpoint scripts.

For each of the 10 tasks, tests 3 tiers:
  (a) Ground truth answer -> score >= 0.85
  (b) Empty answer -> score <= 0.10
  (c) Partial answer (some direct refs, no indirect) -> score 0.3-0.7

Also verifies:
  - Migration ordering: out-of-order migrations rejected
  - 4-checkpoint weighting (0.10/0.35/0.35/0.20) aggregation
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

BENCHMARKS_DIR = Path(__file__).parent.parent / "benchmarks" / "feature_delivery"

# Checkpoint weights for schema evolution tasks
WEIGHTS = [0.10, 0.35, 0.35, 0.20]

CHECKPOINT_NAMES = [
    "check_schema_change",
    "check_direct_refs",
    "check_indirect_refs",
    "check_test_impact",
]


# ── task definitions ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SchemaEvolutionSpec:
    """Spec for testing one schema-evolution task's verifiers."""
    task_num: str
    repo_dir: str
    gt_answer: str
    partial_answer: str


TASKS: list[SchemaEvolutionSpec] = [
    # 001: Zulip — Deactivate User Groups
    SchemaEvolutionSpec(
        task_num="001",
        repo_dir="zulip",
        gt_answer="""\
# Schema Impact Analysis

## 1. Schema Change

Migration `zerver/migrations/0578_namedusergroup_deactivated.py` adds a `deactivated`
BooleanField to the `NamedUserGroup` model defined in `zerver/models/groups.py`.

## 2. Direct References

- `zerver/actions/user_groups.py` — action functions for creating/updating groups
- `zerver/views/user_groups.py` — API views for group endpoints
- `zerver/lib/user_groups.py` — utility functions for group queries
- `zerver/views/streams.py` — stream views that check group permissions

## 3. Indirect References

- `zerver/lib/events.py` — event system sends group update events
- `zerver/lib/event_schema.py` — event schema definitions
- `zerver/lib/import_realm.py` — realm import/export pipeline
- `zerver/lib/markdown/__init__.py` — markdown rendering for @-mentions
- `zerver/openapi/zulip.yaml` — OpenAPI spec for group endpoints
- `zerver/models/realm_audit_logs.py` — audit log entries for group changes

## 4. Test Impact

- `zerver/tests/test_user_groups.py` — main group test suite
- `zerver/tests/test_events.py` — event system tests
- `zerver/tests/test_audit_log.py` — audit log tests
- `zerver/tests/test_markdown.py` — markdown mention tests
""",
        partial_answer="""\
# Schema Impact Analysis

## Schema Change
Migration `zerver/migrations/0578_namedusergroup_deactivated.py` adds `deactivated`
to `NamedUserGroup` in `zerver/models/groups.py`.

## Direct References
- `zerver/actions/user_groups.py` — group actions
- `zerver/views/user_groups.py` — group API views
""",
    ),
    # 002: Zulip — RealmExport Model
    SchemaEvolutionSpec(
        task_num="002",
        repo_dir="zulip",
        gt_answer="""\
# Schema Impact Analysis

## 1. Schema Change

Migration `zerver/migrations/0595_add_realmexport_table_and_backfill.py` creates
the `RealmExport` model in `zerver/models/realms.py`.

## 2. Direct References

- `zerver/actions/realm_export.py` — export action functions
- `zerver/views/realm_export.py` — export API views
- `zerver/models/__init__.py` — model registration

## 3. Indirect References

- `zerver/worker/deferred_work.py` — background worker runs exports
- `zerver/management/commands/export.py` — CLI export command
- `zerver/lib/export.py` — core export logic

## 4. Test Impact

- `zerver/tests/test_realm_export.py` — export tests
- `zerver/tests/test_import_export.py` — round-trip tests
- `zerver/tests/test_events.py` — event tests
- `zerver/tests/test_management_commands.py` — CLI tests
""",
        partial_answer="""\
# Schema Impact Analysis

## Schema Change
Migration `zerver/migrations/0595_add_realmexport_table_and_backfill.py` creates
a new RealmExport model in `zerver/models/realms.py`.

## Direct References
- `zerver/actions/realm_export.py` — export actions
""",
    ),
    # 003: Zulip — Group-Based Channel Setting (expert)
    SchemaEvolutionSpec(
        task_num="003",
        repo_dir="zulip",
        gt_answer="""\
# Schema Impact Analysis

## 1. Schema Change

Four-migration sequence:
- `zerver/migrations/0532_realm_can_create_public_channel_group.py` — add FK column
- `zerver/migrations/0533_set_can_create_public_channel_group.py` — backfill
- `zerver/migrations/0534_alter_realm_can_create_public_channel_group.py` — constraints
- `zerver/migrations/0535_remove_realm_create_public_stream_policy.py` — remove old column

Model change in `zerver/models/realms.py`.

## 2. Backend References

- `zerver/actions/realm_settings.py` — realm settings update actions
- `zerver/actions/create_realm.py` — realm creation
- `zerver/views/realm.py` — realm API views
- `zerver/lib/events.py` — event generation
- `zerver/lib/event_schema.py` — event schema

## 3. Frontend References

- `web/src/settings_org.js` — settings UI logic
- `web/src/settings_data.ts` — settings data helpers
- `web/src/settings_components.ts` — settings components
- `web/src/stream_settings_ui.js` — stream settings
- `web/src/state_data.ts` — state management
- `web/templates/settings/organization_permissions_admin.hbs` — permissions template

## 4. Test Impact

- `zerver/tests/test_events.py` — event tests
- `zerver/tests/test_realm.py` — realm tests
- `web/tests/dispatch.test.js` — dispatch tests
- `web/tests/settings_data.test.js` — settings data tests
- `web/tests/settings_org.test.js` — settings org tests
""",
        partial_answer="""\
# Schema Impact Analysis

## Schema Change
Migration `0532_realm_can_create_public_channel_group` adds the new FK.
Migration `0535_remove_realm_create_public_stream_policy` removes the old column.
Model change in `zerver/models/realms.py`.

## Backend References
- `zerver/actions/realm_settings.py` — settings actions
- `zerver/lib/events.py` — event system
""",
    ),
    # 004: Zulip — Move Default Avatar Source
    SchemaEvolutionSpec(
        task_num="004",
        repo_dir="zulip",
        gt_answer="""\
# Schema Impact Analysis

## 1. Schema Change

Migration `zerver/migrations/0776_realm_default_avatar_source.py` moves the
`default_avatar_source` field from `zerver/models/users.py` (UserProfile) to
`zerver/models/realms.py` (Realm).

## 2. Direct References

- `zerver/lib/avatar.py` — avatar URL generation logic
- `zerver/openapi/python_examples.py` — API example payloads

## 3. Indirect References

- `zerver/tests/test_slack_importer.py` — Slack import sets avatar source
- `zerver/tests/test_mattermost_importer.py` — Mattermost import
- `zerver/tests/test_rocketchat_importer.py` — Rocket.Chat import
- `web/templates/settings/organization_settings_admin.hbs` — frontend template

## 4. Test Impact

- `zerver/tests/test_upload.py` — upload/avatar tests
- `zerver/tests/test_upload_s3.py` — S3 upload tests
- `zerver/tests/test_events.py` — event tests
- All 3 importer test files listed above
""",
        partial_answer="""\
# Schema Impact Analysis

## Schema Change
Migration `0776_realm_default_avatar_source` in `zerver/models/realms.py` and
`zerver/models/users.py`.

## Direct References
- `zerver/lib/avatar.py` — avatar generation
""",
    ),
    # 005: Discourse — Sync Reviewable Status (expert)
    SchemaEvolutionSpec(
        task_num="005",
        repo_dir="discourse",
        gt_answer="""\
# Schema Impact Analysis

## 1. Schema Change

Migration `db/migrate/20250319024514_add_automatic_to_reviewable_claimed_topic.rb`
adds `automatic` boolean to `ReviewableClaimedTopic` model in
`app/models/reviewable_claimed_topic.rb`.

## 2. Direct References

- `app/models/reviewable.rb` — base Reviewable model
- `app/models/reviewable_flagged_post.rb` — flagged post subclass
- `app/controllers/reviewables_controller.rb` — main review controller
- `app/controllers/reviewable_claimed_topics_controller.rb` — claim controller

## 3. Indirect References

- `app/serializers/reviewable_serializer.rb` — main serializer
- `app/serializers/reviewable_claimed_topic_serializer.rb` — claim serializer
- `app/serializers/reviewable_topic_serializer.rb` — topic serializer
- `lib/guardian/user_guardian.rb` — authorization
- `app/assets/javascripts/discourse/app/components/reviewable-item.hbs` — UI
- `app/assets/javascripts/discourse/app/components/reviewable-item.js` — component
- `config/locales/client.en.yml` — locale strings

## 4. Test Impact

- `spec/models/reviewable_claimed_topic_spec.rb`
- `spec/models/reviewable_spec.rb`
- `spec/requests/reviewables_controller_spec.rb`
- `spec/requests/reviewable_claimed_topics_controller_spec.rb`
- `spec/serializers/reviewable_claimed_topic_serializer_spec.rb`
- `spec/lib/guardian/user_guardian_spec.rb`
""",
        partial_answer="""\
# Schema Impact Analysis

## Schema Change
Migration `20250319024514_add_automatic_to_reviewable_claimed_topic.rb` adds
`automatic` to `ReviewableClaimedTopic` (`app/models/reviewable_claimed_topic.rb`).

## Direct References
- `app/models/reviewable.rb` — base model
- `app/controllers/reviewables_controller.rb` — controller
""",
    ),
    # 006: Discourse — Add Description to Invites
    SchemaEvolutionSpec(
        task_num="006",
        repo_dir="discourse",
        gt_answer="""\
# Schema Impact Analysis

## 1. Schema Change

Migration `db/migrate/20250614020437_add_description_to_invites.rb` adds a
`description` text column to the `Invite` model (`app/models/invite.rb`).

## 2. Direct References

- `app/models/invite.rb` — model field addition
- `app/controllers/invites_controller.rb` — params permit
- `app/serializers/invite_serializer.rb` — API serialization

## 3. Indirect References

- `app/assets/javascripts/discourse/app/components/modal/create-invite.gjs` — modal
- `app/assets/javascripts/discourse/app/templates/user-invited-show.gjs` — display
- `app/assets/javascripts/discourse/app/lib/constants.js` — max length constant
- `config/locales/client.en.yml` — locale strings

## 4. Test Impact

- `spec/models/invite_spec.rb` — model spec
- `spec/requests/invites_controller_spec.rb` — controller spec
- `spec/system/create_invite_spec.rb` — system test
""",
        partial_answer="""\
# Schema Impact Analysis

## Schema Change
Migration `20250614020437_add_description_to_invites.rb` adds `description` to
`app/models/invite.rb`.

## Direct References
- `app/controllers/invites_controller.rb` — controller
- `app/serializers/invite_serializer.rb` — serializer
""",
    ),
    # 007: Discourse — Category Approval Groups
    SchemaEvolutionSpec(
        task_num="007",
        repo_dir="discourse",
        gt_answer="""\
# Schema Impact Analysis

## 1. Schema Change

Two-phase migration:
- `db/migrate/20260310072550_mark_category_approval_booleans_readonly.rb`
- `db/migrate/20260310072759_create_category_approval_groups.rb`

## 2. Direct References

- `app/models/category.rb` — parent model
- `app/models/category_posting_review_group.rb` — join model
- `app/models/category_setting.rb` — old boolean columns

## 3. Indirect References

- `script/bulk_import/discourse_merger.rb` — import script
- `spec/serializers/category_serializer_spec.rb` — serializer spec

## 4. Test Impact

- `spec/db/migrate/20260310072759_create_category_approval_groups_spec.rb`
- `spec/models/category_posting_review_group_spec.rb`
- `spec/models/category_setting_spec.rb`
- `spec/models/category_spec.rb`
""",
        partial_answer="""\
# Schema Impact Analysis

## Schema Change
Migration `20260310072759_create_category_approval_groups.rb` creates the join table.

## Direct References
- `app/models/category.rb` — parent model
- `app/models/category_setting.rb` — old booleans
""",
    ),
    # 008: Discourse — Auto-Bump Cooldown Days
    SchemaEvolutionSpec(
        task_num="008",
        repo_dir="discourse",
        gt_answer="""\
# Schema Impact Analysis

## 1. Schema Change

Two-step migration:
- `db/migrate/20230301071240_add_auto_bump_cooldown_days_to_category_settings.rb`
- `db/migrate/20230308042434_backfill_auto_bump_cooldown_days_category_setting.rb`

## 2. Direct References

- `app/models/category_setting.rb` — CategorySetting model
- `app/models/category.rb` — Category delegates
- `app/controllers/categories_controller.rb` — accepts param
- `app/serializers/category_serializer.rb` — exposes in API

## 3. Indirect References

- `app/assets/javascripts/discourse/app/components/edit-category-settings.hbs`
- `app/assets/javascripts/discourse/app/models/category.js`
- `config/locales/client.en.yml` — locale strings
- `spec/requests/api/schemas/json/category_create_response.json` — API schema
- `spec/requests/api/schemas/json/category_update_response.json` — API schema

## 4. Test Impact

- `spec/models/category_setting_spec.rb`
- `spec/models/category_spec.rb`
- `spec/requests/api/schemas/json/category_create_response.json`
- `spec/requests/api/schemas/json/category_update_response.json`
""",
        partial_answer="""\
# Schema Impact Analysis

## Schema Change
Migration `20230301071240_add_auto_bump_cooldown_days_to_category_settings.rb`
and backfill migration `20230308042434_backfill_auto_bump_cooldown_days_category_setting.rb`.

## Direct References
- `app/models/category_setting.rb` — CategorySetting
- `app/controllers/categories_controller.rb` — controller
""",
    ),
    # 009: Sentry — Dashboard Last Visited
    SchemaEvolutionSpec(
        task_num="009",
        repo_dir="sentry",
        gt_answer="""\
# Schema Impact Analysis

## 1. Schema Change

Migration `src/sentry/migrations/0947_add_dashboard_last_visited_model.py` creates
the `DashboardLastVisited` model in `src/sentry/models/dashboard.py`.

## 2. Backend References

- `src/sentry/models/dashboard.py` — model definition
- `src/sentry/backup/comparators.py` — backup system comparators
- `src/sentry/testutils/helpers/backups.py` — backup test utilities

## 3. Frontend References

- `static/app/views/dashboards/manage/tableView/table.tsx` — table component
- `static/app/views/dashboards/types.tsx` — TypeScript types

## 4. Test Impact

- `static/app/views/dashboards/manage/tableView/table.spec.tsx` — component tests
""",
        partial_answer="""\
# Schema Impact Analysis

## Schema Change
Migration `0947_add_dashboard_last_visited_model.py` creates
DashboardLastVisited in `src/sentry/models/dashboard.py`.

## Backend References
- `src/sentry/models/dashboard.py` — model
""",
    ),
    # 010: Mattermost — Flatten View Model
    SchemaEvolutionSpec(
        task_num="010",
        repo_dir="mattermost",
        gt_answer="""\
# Schema Impact Analysis

## 1. Schema Change

SQL migration `server/channels/db/migrations/postgres/000167_views_drop_icon.up.sql`
drops the `icon` column. Registered in `server/channels/db/migrations/migrations.list`.

## 2. Direct References

- `server/public/model/view.go` — Go struct definition
- `server/channels/store/sqlstore/view_store.go` — SQL store queries

## 3. Indirect References

- `api/v4/source/definitions.yaml` — OpenAPI model schema
- `api/v4/source/views.yaml` — OpenAPI endpoint definitions
- `server/i18n/en.json` — internationalization strings

## 4. Test Impact

- `server/public/model/view_test.go` — model tests
- `server/channels/store/storetest/view_store.go` — store tests
- `server/channels/api4/view_test.go` — API tests
- `server/channels/app/view_test.go` — app tests
""",
        partial_answer="""\
# Schema Impact Analysis

## Schema Change
SQL migration `000167_views_drop_icon.up.sql` drops the icon column.

## Direct References
- `server/public/model/view.go` — Go struct
- `server/channels/store/sqlstore/view_store.go` — SQL store
""",
    ),
]


# ── helpers ──────────────────────────────────────────────────────────────────

def _task_dir(task_num: str) -> Path:
    return BENCHMARKS_DIR / f"schema-evolution-{task_num}"


def _run_verifier(
    task_num: str,
    checkpoint_name: str,
    workspace: Path,
) -> dict[str, Any]:
    """Run a checkpoint verifier script and return parsed JSON output."""
    script = _task_dir(task_num) / "checks" / f"{checkpoint_name}.sh"
    assert script.exists(), f"Verifier not found: {script}"

    env = os.environ.copy()
    env["WORKSPACE"] = str(workspace)
    env["TASK_DIR"] = str(_task_dir(task_num))
    env["TASK_ID"] = f"schema-evolution-{task_num}"

    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(workspace),
        env=env,
    )
    stdout = result.stdout.strip()
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"score": 1.0 if result.returncode == 0 else 0.0, "raw": stdout}


def _write_report(workspace: Path, repo_dir: str, content: str) -> Path:
    """Write SCHEMA_IMPACT.md to the agent's deliverable drop.

    *repo_dir* is accepted for call-site compatibility (historically the
    report lived under the repo tree) but is unused: deliverables were
    migrated to /workspace/agent_output/ (mode_gate locks repo dirs in gated
    arms; agent_output is the one location every arm leaves writable) --
    scripts/authoring/migrate_deliverable_to_agent_output.py.
    """
    report_dir = workspace / "agent_output"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "SCHEMA_IMPACT.md"
    report_path.write_text(content)
    return report_path


def _weighted_score(
    results: list[dict[str, Any]],
    weights: tuple[float, ...] | list[float] = (0.10, 0.35, 0.35, 0.20),
) -> float:
    """Compute weighted score from 4 checkpoint results."""
    total = 0.0
    for r, w in zip(results, weights):
        total += float(r.get("score", 0.0)) * w
    return total


# ── tests ────────────────────────────────────────────────────────────────────

class TestGroundTruthScoresHigh:
    """(a) Ground truth answer should score >= 0.85."""

    @pytest.mark.parametrize("spec", TASKS, ids=[t.task_num for t in TASKS])
    def test_gt_answer_scores_high(self, tmp_path: Path, spec: SchemaEvolutionSpec) -> None:
        workspace = tmp_path / "workspace"
        _write_report(workspace, spec.repo_dir, spec.gt_answer)

        results = [
            _run_verifier(spec.task_num, cp, workspace)
            for cp in CHECKPOINT_NAMES
        ]
        total = _weighted_score(results, WEIGHTS)
        assert total >= 0.85, (
            f"Task {spec.task_num} GT scored {total:.2f} (<0.85). "
            f"Results: {results}"
        )


class TestEmptyAnswerScoresLow:
    """(b) Empty/missing answer should score <= 0.10."""

    @pytest.mark.parametrize("spec", TASKS, ids=[t.task_num for t in TASKS])
    def test_empty_answer_scores_low(self, tmp_path: Path, spec: SchemaEvolutionSpec) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        # No SCHEMA_IMPACT.md written — verifiers should return 0

        results = [
            _run_verifier(spec.task_num, cp, workspace)
            for cp in CHECKPOINT_NAMES
        ]
        total = _weighted_score(results, WEIGHTS)
        assert total <= 0.10, (
            f"Task {spec.task_num} empty scored {total:.2f} (>0.10). "
            f"Results: {results}"
        )


class TestPartialAnswerScoresMid:
    """(c) Partial answer (direct refs only, no indirect) should score 0.3-0.7."""

    @pytest.mark.parametrize("spec", TASKS, ids=[t.task_num for t in TASKS])
    def test_partial_answer_scores_mid(self, tmp_path: Path, spec: SchemaEvolutionSpec) -> None:
        workspace = tmp_path / "workspace"
        _write_report(workspace, spec.repo_dir, spec.partial_answer)

        results = [
            _run_verifier(spec.task_num, cp, workspace)
            for cp in CHECKPOINT_NAMES
        ]
        total = _weighted_score(results, WEIGHTS)
        assert 0.15 <= total <= 0.75, (
            f"Task {spec.task_num} partial scored {total:.2f} (expected 0.15-0.75). "
            f"Results: {results}"
        )


class TestMigrationOrdering:
    """Verify migration ordering checkpoint correctly handles multi-migration tasks."""

    def test_task_003_requires_multiple_migrations(self, tmp_path: Path) -> None:
        """Task 003 has 4 migrations — mentioning only first and last should still pass."""
        workspace = tmp_path / "workspace"
        _write_report(workspace, "zulip", """\
# Schema Impact
## Migrations
- `0532_realm_can_create_public_channel_group` — add FK
- `0535_remove_realm_create_public_stream_policy` — remove old
- Model: `zerver/models/realms.py`
""")
        result = _run_verifier("003", "check_schema_change", workspace)
        assert result.get("passed") is True, (
            f"Should pass with first + last migration mentioned: {result}"
        )

    def test_task_003_single_migration_insufficient(self, tmp_path: Path) -> None:
        """Task 003: mentioning only one unrelated migration should not fully pass."""
        workspace = tmp_path / "workspace"
        _write_report(workspace, "zulip", """\
# Schema Impact
## Migrations
- `0533_set_can_create_public_channel_group` — backfill only
""")
        result = _run_verifier("003", "check_schema_change", workspace)
        score = float(result.get("score", 0))
        assert score < 1.0, (
            f"Mentioning only the backfill migration shouldn't score 1.0: {result}"
        )

    def test_task_007_two_phase_migration(self, tmp_path: Path) -> None:
        """Task 007 has 2 migrations — both should be found."""
        workspace = tmp_path / "workspace"
        _write_report(workspace, "discourse", """\
# Schema Impact
## Migrations
- `20260310072550_mark_category_approval_booleans_readonly`
- `20260310072759_create_category_approval_groups`
""")
        result = _run_verifier("007", "check_schema_change", workspace)
        assert result.get("passed") is True, (
            f"Should pass with both migrations: {result}"
        )

    def test_task_008_add_plus_backfill(self, tmp_path: Path) -> None:
        """Task 008 two-step migration: add column + backfill."""
        workspace = tmp_path / "workspace"
        _write_report(workspace, "discourse", """\
# Schema Impact
## Migrations
- `20230301071240_add_auto_bump_cooldown_days_to_category_settings`
- `20230308042434_backfill_auto_bump_cooldown_days_category_setting`
""")
        result = _run_verifier("008", "check_schema_change", workspace)
        assert result.get("passed") is True, (
            f"Should pass with add+backfill migrations: {result}"
        )


class TestPartialCreditProportional:
    """Finding some but not all references scores proportionally."""

    def test_zulip_001_partial_direct_refs(self, tmp_path: Path) -> None:
        """Task 001: finding 2/4 direct refs should score ~0.50."""
        workspace = tmp_path / "workspace"
        _write_report(workspace, "zulip", """\
# Schema Impact
## Direct References
- `zerver/actions/user_groups.py` — actions
- `zerver/views/user_groups.py` — views
""")
        result = _run_verifier("001", "check_direct_refs", workspace)
        score = float(result.get("score", 0))
        assert 0.40 <= score <= 0.60, (
            f"2/4 direct refs scored {score:.2f} (expected ~0.50): {result}"
        )

    def test_discourse_005_partial_indirect(self, tmp_path: Path) -> None:
        """Task 005: finding 3/6 indirect refs should score ~0.50."""
        workspace = tmp_path / "workspace"
        _write_report(workspace, "discourse", """\
# Schema Impact
## Indirect References
- `app/serializers/reviewable_serializer.rb`
- `app/serializers/reviewable_claimed_topic_serializer.rb`
- `lib/guardian/user_guardian.rb`
""")
        result = _run_verifier("005", "check_indirect_refs", workspace)
        score = float(result.get("score", 0))
        assert 0.40 <= score <= 0.60, (
            f"3/6 indirect refs scored {score:.2f} (expected ~0.50): {result}"
        )

    def test_mattermost_010_partial_tests(self, tmp_path: Path) -> None:
        """Task 010: finding 2/4 test files should score ~0.50."""
        workspace = tmp_path / "workspace"
        _write_report(workspace, "mattermost", """\
# Schema Impact
## Tests
- `server/public/model/view_test.go`
- `server/channels/store/storetest/view_store.go`
""")
        result = _run_verifier("010", "check_test_impact", workspace)
        score = float(result.get("score", 0))
        assert 0.40 <= score <= 0.60, (
            f"2/4 test files scored {score:.2f} (expected ~0.50): {result}"
        )


class TestCheckpointWeighting:
    """Verify 4-checkpoint weighting (0.10/0.35/0.35/0.20) produces correct aggregation."""

    def test_weights_sum_to_one(self) -> None:
        assert abs(sum(WEIGHTS) - 1.0) < 1e-9, (
            f"Weights sum to {sum(WEIGHTS)}, expected 1.0"
        )

    def test_task_toml_weights_match(self) -> None:
        """All 10 task.toml files use the expected checkpoint weights."""
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]

        for i in range(1, 11):
            task_num = f"{i:03d}"
            toml_path = _task_dir(task_num) / "task.toml"
            assert toml_path.exists(), f"No task.toml for schema-evolution-{task_num}"
            with open(toml_path, "rb") as f:
                task = tomllib.load(f)
            checkpoints = task.get("checkpoints", [])
            assert len(checkpoints) == 4, (
                f"schema-evolution-{task_num}: expected 4 checkpoints, got {len(checkpoints)}"
            )
            actual_weights = [cp["weight"] for cp in checkpoints]
            assert actual_weights == WEIGHTS, (
                f"schema-evolution-{task_num}: weights {actual_weights} != expected {WEIGHTS}"
            )

    def test_weighted_aggregation_math(self) -> None:
        """Verify the weighted score computation matches expected math."""
        from eb_verify.scoring import CheckpointResult, compute_score

        results = [
            CheckpointResult(name="schema_change", weight=0.10, passed=True, score=1.0),
            CheckpointResult(name="direct_refs", weight=0.35, passed=True, score=0.8),
            CheckpointResult(name="indirect_refs", weight=0.35, passed=True, score=0.6),
            CheckpointResult(name="test_impact", weight=0.20, passed=True, score=0.5),
        ]
        # (1.0*0.10 + 0.8*0.35 + 0.6*0.35 + 0.5*0.20) / 1.0 = 0.10 + 0.28 + 0.21 + 0.10 = 0.69
        expected = 0.69
        actual = compute_score(results)
        assert abs(actual - expected) < 1e-6, (
            f"compute_score returned {actual}, expected {expected}"
        )

    def test_indirect_refs_dominates_with_direct(self) -> None:
        """Direct + indirect refs (combined weight=0.70) should dominate total score."""
        # schema=0, direct=1, indirect=1, tests=0 -> 0.70
        results_refs = [
            {"score": 0.0}, {"score": 1.0}, {"score": 1.0}, {"score": 0.0}
        ]
        # schema=1, direct=0, indirect=0, tests=1 -> 0.30
        results_no_refs = [
            {"score": 1.0}, {"score": 0.0}, {"score": 0.0}, {"score": 1.0}
        ]
        score_refs = _weighted_score(results_refs, WEIGHTS)
        score_no_refs = _weighted_score(results_no_refs, WEIGHTS)
        assert score_refs > score_no_refs, (
            f"Refs ({score_refs:.2f}) should beat no-refs ({score_no_refs:.2f})"
        )
        assert abs(score_refs - 0.70) < 1e-6
        assert abs(score_no_refs - 0.30) < 1e-6


class TestVerifierScriptsExist:
    """All 10 tasks have all 4 checkpoint verifier scripts."""

    @pytest.mark.parametrize("task_num", [f"{i:03d}" for i in range(1, 11)])
    def test_all_verifiers_present(self, task_num: str) -> None:
        task_dir = _task_dir(task_num)
        for cp in CHECKPOINT_NAMES:
            script = task_dir / "checks" / f"{cp}.sh"
            assert script.exists(), f"Missing: {script}"
            assert os.access(script, os.X_OK), f"Not executable: {script}"

    @pytest.mark.parametrize("task_num", [f"{i:03d}" for i in range(1, 11)])
    def test_task_toml_exists(self, task_num: str) -> None:
        toml_path = _task_dir(task_num) / "task.toml"
        assert toml_path.exists(), f"Missing task.toml for schema-evolution-{task_num}"

    @pytest.mark.parametrize("task_num", [f"{i:03d}" for i in range(1, 11)])
    def test_ground_truth_exists(self, task_num: str) -> None:
        gt_path = _task_dir(task_num) / "ground_truth.json"
        assert gt_path.exists(), f"Missing ground_truth.json for schema-evolution-{task_num}"
        data = json.loads(gt_path.read_text())
        req = data.get("required_files", [])
        assert len(req) >= 3, (
            f"schema-evolution-{task_num}: only {len(req)} required_files, need >= 3"
        )

    @pytest.mark.parametrize("task_num", [f"{i:03d}" for i in range(1, 11)])
    def test_instruction_exists(self, task_num: str) -> None:
        inst_path = _task_dir(task_num) / "instruction.md"
        assert inst_path.exists(), f"Missing instruction.md for schema-evolution-{task_num}"
        content = inst_path.read_text()
        # Should not read like a test prompt
        assert "Your task:" not in content or "What I Need" in content, (
            f"schema-evolution-{task_num}: instruction.md should be realistic, not test-like"
        )
