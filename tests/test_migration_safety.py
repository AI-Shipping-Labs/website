"""Deterministic contracts for the database-free migration-safety check."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from django.db import connection, models
from django.db.migrations import Migration, operations
from django.db.migrations.graph import MigrationGraph
from django.db.models.functions import Now
from django.test import SimpleTestCase

from scripts.check_migration_safety import (
    FROZEN_BASELINE_NODES,
    check_migration_graph,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_BASELINES = (
    ("accounts", "0026_deletion_request_lifecycle"),
    ("analytics", "0006_alter_useractivity_event_type"),
    ("bookclub", "0008_alter_book_required_level"),
    ("comments", "0001_initial"),
    ("community", "0019_alter_callhost_options_alter_bookedcall_host_and_more"),
    ("content", "0060_article_image_manifest"),
    ("crm", "0008_slack_ingest_lease_and_refresh_count"),
    ("email_app", "0021_reconcile_emaillog_subject_default"),
    ("events", "0045_seriesoccurrenceoptout"),
    ("integrations", "0028_maven_enrollment_notification_step"),
    ("notifications", "0011_alter_notification_notification_type"),
    ("payments", "0014_stripewebhookdeliveryattempt_refund_review"),
    ("plans", "0029_sprint_audience_sprint_description_sprint_outcomes"),
    ("questionnaires", "0008_response_review_queue"),
    ("studio", "0001_initial"),
    ("triggers", "0003_r1_expand_reconciliation"),
    ("voting", "0001_initial"),
)


class NoColumnField(models.Field):
    def db_type(self, connection):
        return None


def _migration(app_label, name, migration_operations, *, dependencies=()):
    migration = Migration(name, app_label)
    migration.operations = list(migration_operations)
    migration.dependencies = list(dependencies)
    return migration


def _initial_migration(app_label, name="0001_baseline", *, options=None):
    return _migration(
        app_label,
        name,
        [
            operations.CreateModel(
                name="Widget",
                fields=[
                    ("id", models.BigAutoField(primary_key=True)),
                    ("source", models.IntegerField(default=0)),
                ],
                options=options,
            ),
            operations.CreateModel(
                name="Target",
                fields=[("id", models.BigAutoField(primary_key=True))],
            ),
        ],
    )


def _check(migrations, *, baselines):
    migration_map = {
        (migration.app_label, migration.name): migration for migration in migrations
    }
    graph = MigrationGraph()
    for node, migration in migration_map.items():
        graph.add_node(node, migration)
    for node, migration in migration_map.items():
        for dependency in migration.dependencies:
            graph.add_dependency(migration, node, dependency)
    graph.validate_consistency()
    return check_migration_graph(
        graph=graph,
        migrations=migration_map,
        state_factory=lambda node: graph.make_state([node], at_end=False),
        first_party_apps=frozenset(migration.app_label for migration in migrations),
        connection=connection,
        baselines=tuple(baselines),
    )


class MigrationSafetyDetectionTest(SimpleTestCase):
    def test_python_only_defaults_and_field_flags_are_all_unsafe(self):
        app = "events_fixture"
        baseline = _initial_migration(app)
        additions = _migration(
            app,
            "0002_add_fields",
            [
                operations.AddField(
                    "widget",
                    "recap_notes",
                    models.TextField(default="", blank=True),
                    preserve_default=False,
                ),
                operations.AddField(
                    "widget",
                    "preserved_python_default",
                    models.CharField(default="value", max_length=20),
                    preserve_default=True,
                ),
                operations.AddField(
                    "widget", "explicit_none", models.CharField(db_default=None)
                ),
                operations.AddField(
                    "widget", "updated_at", models.DateTimeField(auto_now=True)
                ),
                operations.AddField(
                    "widget", "created_at", models.DateTimeField(auto_now_add=True)
                ),
            ],
            dependencies=[(app, baseline.name)],
        )

        result = _check([baseline, additions], baselines=[(app, baseline.name)])

        self.assertFalse(result.ok)
        self.assertEqual(
            [finding.field_name for finding in result.findings],
            [
                "created_at",
                "explicit_none",
                "preserved_python_default",
                "recap_notes",
                "updated_at",
            ],
        )
        output = result.render()
        self.assertIn(f"{app}.0002_add_fields", output)
        self.assertIn("widget.recap_notes", output)
        self.assertIn("Old-image inserts can omit", output)
        self.assertIn("Python default", output)
        self.assertIn("persistent db_default", output)
        self.assertIn("keeps the column nullable", output)

    def test_multiple_findings_have_complete_stable_sorted_output(self):
        alpha = _initial_migration("alpha")
        zebra = _initial_migration("zebra")
        alpha_add = _migration(
            "alpha",
            "0003_unsafe",
            [
                operations.AddField("widget", "z_field", models.TextField()),
                operations.AddField("widget", "a_field", models.TextField()),
            ],
            dependencies=[("alpha", alpha.name)],
        )
        zebra_add = _migration(
            "zebra",
            "0002_unsafe",
            [operations.AddField("widget", "middle", models.TextField())],
            dependencies=[("zebra", zebra.name)],
        )

        first = _check(
            [zebra_add, alpha, zebra, alpha_add],
            baselines=[("alpha", alpha.name), ("zebra", zebra.name)],
        )
        second = _check(
            [alpha_add, zebra, alpha, zebra_add],
            baselines=[("zebra", zebra.name), ("alpha", alpha.name)],
        )

        expected = [
            ("alpha", "0003_unsafe", "widget", "a_field"),
            ("alpha", "0003_unsafe", "widget", "z_field"),
            ("zebra", "0002_unsafe", "widget", "middle"),
        ]
        self.assertEqual(
            [
                (
                    finding.app_label,
                    finding.migration_name,
                    finding.model_name,
                    finding.field_name,
                )
                for finding in first.findings
            ],
            expected,
        )
        self.assertEqual(first.render(), second.render())
        self.assertIn("Migration safety check failed: 3 violation(s).", first.render())

    def test_literal_and_expression_database_defaults_pass(self):
        app = "defaults_fixture"
        baseline = _initial_migration(app)
        additions = _migration(
            app,
            "0002_defaults",
            [
                operations.AddField(
                    "widget",
                    "literal",
                    models.CharField(default="python", db_default=""),
                ),
                operations.AddField(
                    "widget",
                    "expression",
                    models.DateTimeField(db_default=Now()),
                ),
            ],
            dependencies=[(app, baseline.name)],
        )

        result = _check([baseline, additions], baselines=[(app, baseline.name)])

        self.assertTrue(result.ok, result.render())

    def test_nullable_physical_column_passes(self):
        app = "nullable_fixture"
        baseline = _initial_migration(app)
        addition = _migration(
            app,
            "0002_nullable",
            [operations.AddField("widget", "notes", models.TextField(null=True))],
            dependencies=[(app, baseline.name)],
        )

        result = _check([baseline, addition], baselines=[(app, baseline.name)])

        self.assertTrue(result.ok, result.render())

    def test_non_column_and_database_generated_fields_pass(self):
        app = "generated_fixture"
        baseline = _initial_migration(app)
        addition = _migration(
            app,
            "0002_generated",
            [
                operations.AddField(
                    "widget", "targets", models.ManyToManyField(to=f"{app}.target")
                ),
                operations.AddField(
                    "widget",
                    "computed",
                    models.GeneratedField(
                        expression=models.F("source") + 1,
                        output_field=models.IntegerField(),
                        db_persist=True,
                    ),
                ),
                operations.AddField(
                    "widget", "sequence", models.AutoField(primary_key=True)
                ),
                operations.AddField("widget", "virtual", NoColumnField()),
            ],
            dependencies=[(app, baseline.name)],
        )

        result = _check([baseline, addition], baselines=[(app, baseline.name)])

        self.assertTrue(result.ok, result.render())

    def test_model_created_earlier_in_same_migration_passes(self):
        app = "new_model_fixture"
        baseline = _initial_migration(app)
        addition = _migration(
            app,
            "0002_new_model",
            [
                operations.CreateModel(
                    name="BrandNew",
                    fields=[("id", models.BigAutoField(primary_key=True))],
                ),
                operations.AddField("brandnew", "required", models.TextField()),
            ],
            dependencies=[(app, baseline.name)],
        )

        result = _check([baseline, addition], baselines=[(app, baseline.name)])

        self.assertTrue(result.ok, result.render())

    def test_separate_database_and_state_inspects_only_database_operations(self):
        state_app = "state_only_fixture"
        database_app = "database_fixture"
        state_baseline = _initial_migration(state_app)
        database_baseline = _initial_migration(database_app)
        state_only = _migration(
            state_app,
            "0002_separate",
            [
                operations.SeparateDatabaseAndState(
                    state_operations=[
                        operations.AddField("widget", "state_only", models.TextField())
                    ]
                )
            ],
            dependencies=[(state_app, state_baseline.name)],
        )
        database_operation = _migration(
            database_app,
            "0002_separate",
            [
                operations.SeparateDatabaseAndState(
                    database_operations=[
                        operations.AddField("widget", "physical", models.TextField())
                    ]
                )
            ],
            dependencies=[(database_app, database_baseline.name)],
        )

        result = _check(
            [state_baseline, database_baseline, state_only, database_operation],
            baselines=[
                (state_app, state_baseline.name),
                (database_app, database_baseline.name),
            ],
        )

        self.assertEqual(
            [(finding.app_label, finding.field_name) for finding in result.findings],
            [(database_app, "physical")],
        )

    def test_unmanaged_and_proxy_models_pass(self):
        unmanaged_app = "unmanaged_fixture"
        proxy_app = "proxy_fixture"
        unmanaged = _initial_migration(unmanaged_app, options={"managed": False})
        proxy = _initial_migration(proxy_app, options={"proxy": True})
        unmanaged_add = _migration(
            unmanaged_app,
            "0002_add",
            [operations.AddField("widget", "required", models.TextField())],
            dependencies=[(unmanaged_app, unmanaged.name)],
        )
        proxy_add = _migration(
            proxy_app,
            "0002_add",
            [operations.AddField("widget", "required", models.TextField())],
            dependencies=[(proxy_app, proxy.name)],
        )

        result = _check(
            [unmanaged, proxy, unmanaged_add, proxy_add],
            baselines=[
                (unmanaged_app, unmanaged.name),
                (proxy_app, proxy.name),
            ],
        )

        self.assertTrue(result.ok, result.render())


class MigrationSafetyBaselineTest(SimpleTestCase):
    def test_frozen_baseline_is_exactly_the_groomed_issue_contract(self):
        self.assertEqual(FROZEN_BASELINE_NODES, EXPECTED_BASELINES)

    def test_ancestors_are_grandfathered_but_divergent_future_branch_is_not(self):
        app = "branch_fixture"
        root = _initial_migration(app, name="0001_root")
        baseline = _migration(
            app,
            "0002_baseline",
            [operations.AddField("widget", "historical", models.TextField())],
            dependencies=[(app, root.name)],
        )
        divergent = _migration(
            app,
            "0003_divergent",
            [operations.AddField("widget", "future", models.TextField())],
            dependencies=[(app, root.name)],
        )

        result = _check([root, baseline, divergent], baselines=[(app, baseline.name)])

        self.assertEqual(
            [(finding.migration_name, finding.field_name) for finding in result.findings],
            [("0003_divergent", "future")],
        )

    def test_missing_frozen_baseline_fails_closed(self):
        app = "missing_fixture"
        root = _initial_migration(app)

        result = _check([root], baselines=[(app, "0099_missing")])

        self.assertFalse(result.ok)
        self.assertEqual(result.findings, ())
        self.assertIn("frozen baseline node missing_fixture.0099_missing", result.render())
        self.assertIn("restore it instead of advancing", result.render())

    def test_first_migration_in_new_first_party_app_is_fully_in_scope(self):
        app = "future_app"
        first = _migration(
            app,
            "0001_initial",
            [
                operations.CreateModel(
                    name="NewTable",
                    fields=[("id", models.BigAutoField(primary_key=True))],
                ),
                operations.AddField("newtable", "required", models.TextField()),
            ],
        )

        result = _check([first], baselines=[])

        self.assertTrue(result.ok, result.render())
        self.assertEqual(result.checked_migrations, 1)


class MigrationSafetyRepositoryContractTest(SimpleTestCase):
    def test_real_graph_check_passes_without_creating_or_opening_a_database(self):
        tmp_root = REPO_ROOT / ".tmp"
        tmp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmp_root) as directory:
            database_path = Path(directory) / "must-not-be-created.sqlite3"
            env = os.environ.copy()
            env.update(
                {
                    "DATABASE_URL": f"sqlite:///{database_path}",
                    "SECRET_KEY": "migration-safety-test-only",
                }
            )

            completed = subprocess.run(
                [sys.executable, "scripts/check_migration_safety.py"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
            self.assertRegex(
                completed.stdout.strip(),
                r"^Migration safety check passed: 0 violations across \d+ "
                r"in-scope first-party migration\(s\)\.$",
            )
            self.assertFalse(database_path.exists())

    def test_deploy_dev_runs_both_checks_in_order_without_error_suppression(self):
        workflow_path = REPO_ROOT / ".github" / "workflows" / "deploy-dev.yml"
        workflow = yaml.safe_load(workflow_path.read_text())
        step = next(
            item
            for item in workflow["jobs"]["checks"]["steps"]
            if item.get("name") == "Check migration safety"
        )
        command = step["run"]

        self.assertLess(
            command.index("uv run python manage.py makemigrations --check --dry-run"),
            command.index("uv run python scripts/check_migration_safety.py"),
        )
        self.assertNotIn("continue-on-error", step)
        self.assertIn("CI_TIMING phase=makemigrations_check", command)
        self.assertIn("CI_TIMING phase=migration_safety_check", command)
        self.assertNotIn(
            "scripts/check_migration_safety.py",
            (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(),
        )
