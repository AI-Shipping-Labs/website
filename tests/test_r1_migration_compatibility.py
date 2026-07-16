"""Executable production-baseline compatibility matrix for issue #1266 R1."""

import importlib
from datetime import timedelta

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag
from django.utils import timezone

from community.models import BookedCall, UnmatchedBookedCall
from events.models import Event as CurrentEvent
from integrations.models import MavenEnrollmentEvent
from tests.r1_original_dev_drift_fixture import apply_original_1e5_dev_drift
from triggers.models import EventEmission, TriggerSubscription
from triggers.secrets import encrypt_secret

# Frozen exact leaves represented by production SHA 524153b6. Never replace
# this with leaf_nodes(): doing so would silently move the compatibility floor.
PRODUCTION_524153B6_LEAVES = (
    ("account", "0009_emailaddress_unique_primary_email"),
    ("accounts", "0022_privacyrequestlog"),
    ("admin", "0003_logentry_add_action_flag_choices"),
    ("analytics", "0006_alter_useractivity_event_type"),
    ("auth", "0012_alter_user_first_name_max_length"),
    ("comments", "0001_initial"),
    ("community", "0014_alter_communityauditlog_action"),
    ("content", "0053_merge_0052_marketingpage_0052_workshop_core_tools"),
    ("contenttypes", "0002_remove_content_type_name"),
    ("crm", "0007_slackthread_interview_note"),
    ("django_q", "0018_task_success_index"),
    ("email_app", "0018_emailcampaign_target_event"),
    ("events", "0039_backfill_inline_bullet_description_html"),
    ("integrations", "0023_seed_cloudflare_workshop_redirect"),
    ("notifications", "0009_add_sprint_recap_notification_type"),
    ("payments", "0008_privacy_retention_user_fks"),
    ("plans", "0028_merge_0027_firstsprintplandraft_0027_merge_sprint_cadence_and_end_delivery"),
    ("questionnaires", "0006_update_onboarding_questionnaire_copy_1099"),
    ("sessions", "0001_initial"),
    ("sites", "0002_alter_domain_unique"),
    ("socialaccount", "0006_alter_socialaccount_extra_data"),
    ("studio", "0001_initial"),
    ("triggers", "0001_initial"),
    ("voting", "0001_initial"),
)

R1_EXPAND_LEAVES = (
    ("account", "0009_emailaddress_unique_primary_email"),
    ("accounts", "0025_alter_user_signup_source"),
    ("admin", "0003_logentry_add_action_flag_choices"),
    ("analytics", "0006_alter_useractivity_event_type"),
    ("auth", "0012_alter_user_first_name_max_length"),
    ("comments", "0001_initial"),
    ("community", "0017_unmatchedbookedcall"),
    ("content", "0054_download_private_storage"),
    ("contenttypes", "0002_remove_content_type_name"),
    ("crm", "0008_slack_ingest_lease_and_refresh_count"),
    ("django_q", "0018_task_success_index"),
    ("email_app", "0019_emaillog_dedupe_key"),
    ("events", "0042_event_host_access_version_hostinvitedelivery"),
    ("integrations", "0025_webhooklog_delivery_state"),
    ("notifications", "0009_add_sprint_recap_notification_type"),
    ("payments", "0009_alter_paymentaccountmismatch_reason_and_more"),
    ("plans", "0029_sprint_audience_sprint_description_sprint_outcomes"),
    ("questionnaires", "0007_onboarding_turn_attempt"),
    ("sessions", "0001_initial"),
    ("sites", "0002_alter_domain_unique"),
    ("socialaccount", "0006_alter_socialaccount_extra_data"),
    ("studio", "0001_initial"),
    ("triggers", "0003_r1_expand_reconciliation"),
    ("voting", "0001_initial"),
)

ORIGINAL_1E5_DEV_LEAVES = tuple(
    (app, "0002_secure_delivery_state")
    if app == "triggers"
    else (app, "0016_bookedcall_host_nullable_last_event_at")
    if app == "community"
    else (app, migration)
    for app, migration in R1_EXPAND_LEAVES
)


@tag("postgres_migration", "core")
class R1ProductionMigrationCompatibilityTest(TransactionTestCase):
    serialized_rollback = True

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(PRODUCTION_524153B6_LEAVES)
        self.production_apps = self.executor.loader.project_state(
            PRODUCTION_524153B6_LEAVES,
        ).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate(R1_EXPAND_LEAVES)
        super().tearDown()

    def test_historical_production_models_write_after_expand(self):
        old = self.production_apps
        User = old.get_model("accounts", "User")
        Tier = old.get_model("payments", "Tier")
        user = User.objects.create(email="legacy-r1@example.com", password="!")
        tier = Tier.objects.order_by("level").first()

        self.executor = MigrationExecutor(connection)
        self.executor.migrate(R1_EXPAND_LEAVES)

        old.get_model("accounts", "TierOverride").objects.create(
            user_id=user.pk,
            override_tier_id=tier.pk,
            expires_at=timezone.now() + timedelta(days=1),
        )
        old.get_model("content", "Download").objects.create(
            title="Legacy download",
            slug="legacy-download-r1",
            file_url="https://example.com/legacy.pdf",
        )
        SlackIngest = old.get_model("crm", "SlackChannelIngest")
        SlackIngest.objects.create(channel_id="C-R1", status="running")
        SlackIngest.objects.create(channel_id="C-R1", status="running")
        old.get_model("crm", "SlackThread").objects.create(
            channel_id="C-R1",
            thread_ts="1.000001",
            posted_at=timezone.now(),
        )
        Event = old.get_model("events", "Event")
        Event.objects.create(
            slug="legacy-event-r1-a",
            title="Legacy event A",
            start_datetime=timezone.now(),
        )
        Event.objects.create(
            slug="legacy-event-r1-b",
            title="Legacy event B",
            start_datetime=timezone.now(),
        )
        Maven = old.get_model("integrations", "MavenEnrollmentEvent")
        Maven.objects.create(dedupe_key="legacy-maven-r1-a")
        Maven.objects.create(dedupe_key="legacy-maven-r1-b")
        old.get_model("integrations", "WebhookLog").objects.create(service="legacy")
        old.get_model("plans", "Sprint").objects.create(
            name="Legacy R1 sprint",
            slug="legacy-r1-sprint",
            start_date=timezone.now().date(),
        )
        questionnaire = old.get_model("questionnaires", "Questionnaire").objects.create(
            title="Legacy questionnaire",
            slug="legacy-questionnaire-r1",
        )
        response = old.get_model("questionnaires", "Response").objects.create(
            questionnaire_id=questionnaire.pk,
            respondent_id=user.pk,
        )
        old.get_model("questionnaires", "OnboardingConversation").objects.create(
            response_id=response.pk,
        )
        HistoricalSubscription = old.get_model("triggers", "TriggerSubscription")
        HistoricalSubscription.objects.create(
            target_url="https://example.com/hook",
            secret="legacy-secret-r1",
        )
        # Simulate an old web image updating the retained plaintext column
        # after the expand migration has landed but before worker handoff.
        historical_subscription = HistoricalSubscription.objects.get()
        historical_subscription.secret = "legacy-secret-r1-updated"
        historical_subscription.save(update_fields=["secret"])
        old.get_model("triggers", "EventEmission").objects.create(
            event_name="custom",
            envelope_id="evt_legacy_r1",
        )

        call_command("reconcile_r1_expand", verbosity=0)
        first = list(
            TriggerSubscription.objects.values_list(
                "encrypted_secret", "legacy_secret", "secret_version",
            ),
        )
        call_command("reconcile_r1_expand", verbosity=0)
        self.assertEqual(
            first,
            list(TriggerSubscription.objects.values_list(
                "encrypted_secret", "legacy_secret", "secret_version",
            )),
        )
        self.assertFalse(CurrentEvent.objects.filter(calendar_uid__isnull=True).exists())
        self.assertFalse(CurrentEvent.objects.filter(host_access_version__isnull=True).exists())
        self.assertFalse(EventEmission.objects.filter(envelope={}).exists())
        self.assertEqual(
            MavenEnrollmentEvent.objects.filter(lifecycle="legacy", identity_hash="").count(),
            0,
        )
        subscription = TriggerSubscription.objects.get()
        self.assertEqual(subscription.secret, "legacy-secret-r1-updated")
        self.assertEqual(subscription.legacy_secret, "legacy-secret-r1-updated")

    def test_original_already_migrated_dev_fingerprint_reconciles_forward(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(ORIGINAL_1E5_DEV_LEAVES)
        self.executor = MigrationExecutor(connection)
        self.assertIn(
            ("community", "0016_bookedcall_host_nullable_last_event_at"),
            self.executor.recorder.applied_migrations(),
        )
        drift_apps = self.executor.loader.project_state(ORIGINAL_1E5_DEV_LEAVES).apps

        drift_apps.get_model("triggers", "TriggerSubscription").objects.create(
            target_url="https://example.com/dev-drift-hook",
            encrypted_secret=encrypt_secret("dev-drift-secret-r1"),
            legacy_secret="dev-drift-secret-r1",
            secret_version=1,
        )

        with connection.schema_editor() as schema_editor:
            apply_original_1e5_dev_drift(drift_apps, schema_editor)
        self.assertIn(
            ("community", "0016_bookedcall_host_nullable_last_event_at"),
            self.executor.recorder.applied_migrations(),
        )

        CallHost = drift_apps.get_model("community", "CallHost")
        BookedCallAtDrift = drift_apps.get_model("community", "BookedCall")
        host = CallHost.objects.create(
            name="R1 real host",
            slug="r1-real-host",
            booking_url="https://calendly.example/r1-real",
        )
        visible = BookedCallAtDrift.objects.create(
            host_id=host.pk,
            invitee_email="visible-r1@example.com",
            calendly_event_uri="https://calendly.example/r1-visible",
        )
        # The original target admitted unmatched Calendly rows. R1 moves them
        # non-lossily outside the table read by the rollback image.
        unmatched = BookedCallAtDrift.objects.create(
            host_id=None,
            invitee_email="unmatched-r1@example.com",
            calendly_event_uri="https://calendly.example/r1-unmatched",
        )

        MigrationExecutor(connection).migrate(R1_EXPAND_LEAVES)

        self.assertFalse(BookedCall.objects.filter(host_id__isnull=True).exists())
        staged = UnmatchedBookedCall.objects.get(
            calendly_event_uri="https://calendly.example/r1-unmatched",
        )
        self.assertEqual(staged.source_booked_call_id, unmatched.pk)
        self.assertEqual(staged.invitee_email, unmatched.invitee_email)
        self.assertEqual(staged.source_created_at, unmatched.created_at)
        self.assertEqual(staged.source_updated_at, unmatched.updated_at)

        # Reproduce the exact 524 image's host dereference/operator path. Every
        # BookedCall it can select retains a real host; staged rows are hidden.
        current_visible = BookedCall.objects.select_related("host").get(pk=visible.pk)
        self.assertEqual(current_visible.host.name, "R1 real host")
        self.assertIn("with R1 real host", str(current_visible))
        historical = self.production_apps.get_model("community", "BookedCall")
        historical_visible = historical.objects.select_related("host").get(pk=visible.pk)
        self.assertEqual(historical_visible.host.name, "R1 real host")
        self.assertFalse(historical.objects.filter(host_id__isnull=True).exists())
        columns = {
            column.name
            for column in connection.introspection.get_table_description(
                connection.cursor(), TriggerSubscription._meta.db_table,
            )
        }
        self.assertIn("secret", columns)

        subscription = TriggerSubscription.objects.get(
            target_url="https://example.com/dev-drift-hook",
        )
        self.assertEqual(subscription.secret, "dev-drift-secret-r1")
        self.assertEqual(subscription.legacy_secret, "dev-drift-secret-r1")
        call_command("reconcile_r1_expand", verbosity=0)
        snapshot = (
            subscription.encrypted_secret,
            subscription.legacy_secret,
            subscription.secret_version,
        )
        call_command("reconcile_r1_expand", verbosity=0)
        subscription.refresh_from_db()
        self.assertEqual(
            snapshot,
            (
                subscription.encrypted_secret,
                subscription.legacy_secret,
                subscription.secret_version,
            ),
        )

        with connection.cursor() as cursor:
            slack_constraints = connection.introspection.get_constraints(
                cursor, "crm_slackchannelingest",
            )
            maven_constraints = connection.introspection.get_constraints(
                cursor, "integrations_mavenenrollmentevent",
            )
        self.assertNotIn("unique_running_slack_ingest_per_channel", slack_constraints)
        self.assertNotIn("uniq_active_maven_occurrence", maven_constraints)

    def test_postgresql_r1_physical_schema_contract(self):
        """Assert the deploy-gating database contract in PostgreSQL metadata.

        SQLite exercises the historical write matrix above, but cannot prove
        PostgreSQL's stored defaults, nullable-unique semantics, or partial
        index removal.  The deploy workflow runs this test against Postgres.
        """
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL catalog assertions run in the deploy gate")

        self.executor = MigrationExecutor(connection)
        self.executor.migrate(R1_EXPAND_LEAVES)
        r1_apps = self.executor.loader.project_state(R1_EXPAND_LEAVES).apps

        reconciliation = importlib.import_module(
            "triggers.migrations.0003_r1_expand_reconciliation",
        )
        expected_columns = {}
        for app_label, model_name, field_name in reconciliation.DEFAULT_FIELDS:
            model = r1_apps.get_model(app_label, model_name)
            expected_columns[(model._meta.db_table, model._meta.get_field(field_name).column)] = (
                app_label,
                model_name,
                field_name,
            )

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name, column_name, column_default
                  FROM information_schema.columns
                 WHERE table_schema = current_schema()
                """,
            )
            column_defaults = {
                (table_name, column_name): default
                for table_name, column_name, default in cursor.fetchall()
            }

        missing_defaults = [
            identity
            for physical_column, identity in expected_columns.items()
            if column_defaults.get(physical_column) is None
        ]
        self.assertEqual(missing_defaults, [])

        # Check representative expressions as well as their presence, so the
        # gate catches a stored default with the wrong value or lifecycle.
        self.assertIn(
            "''",
            column_defaults[("accounts_tieroverride", "source")],
        )
        self.assertEqual(
            column_defaults[("crm_slackchannelingest", "known_threads_checked")],
            "0",
        )
        self.assertEqual(
            column_defaults[("crm_slackchannelingest", "advances_watermark")],
            "true",
        )
        self.assertIn(
            "'legacy'",
            column_defaults[("integrations_mavenenrollmentevent", "lifecycle")],
        )
        self.assertIn(
            "statement_timestamp()",
            column_defaults[("integrations_mavenenrollmentevent", "updated_at")].lower(),
        )
        self.assertEqual(
            column_defaults[("triggers_triggersubscription", "secret_version")],
            "1",
        )
        self.assertIn(
            "statement_timestamp()",
            column_defaults[("triggers_eventemission", "occurred_at")].lower(),
        )
        self.assertIn(
            "'{}'",
            column_defaults[("triggers_eventemission", "envelope")],
        )

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name, is_nullable
                  FROM information_schema.columns
                 WHERE table_schema = current_schema()
                   AND table_name = 'events_event'
                   AND column_name IN ('calendar_uid', 'host_access_version')
                """,
            )
            event_nullability = dict(cursor.fetchall())
            cursor.execute(
                """
                SELECT is_nullable
                  FROM information_schema.columns
                 WHERE table_schema = current_schema()
                   AND table_name = 'community_bookedcall'
                   AND column_name = 'host_id'
                """,
            )
            booked_call_host_nullability = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT a.attname
                  FROM pg_constraint AS c
                  JOIN pg_class AS t ON t.oid = c.conrelid
                  JOIN pg_namespace AS n ON n.oid = t.relnamespace
                  JOIN unnest(c.conkey) AS key(attnum) ON true
                  JOIN pg_attribute AS a
                    ON a.attrelid = t.oid AND a.attnum = key.attnum
                 WHERE n.nspname = current_schema()
                   AND t.relname = 'events_event'
                   AND c.contype = 'u'
                """,
            )
            unique_event_columns = {row[0] for row in cursor.fetchall()}
            cursor.execute(
                """
                SELECT indexname
                  FROM pg_indexes
                 WHERE schemaname = current_schema()
                   AND indexname IN (
                       'unique_running_slack_ingest_per_channel',
                       'uniq_active_maven_occurrence'
                   )
                """,
            )
            deferred_indexes = [row[0] for row in cursor.fetchall()]

        self.assertEqual(event_nullability["calendar_uid"], "YES")
        self.assertEqual(event_nullability["host_access_version"], "YES")
        self.assertEqual(booked_call_host_nullability, "NO")
        self.assertIn("calendar_uid", unique_event_columns)
        self.assertEqual(deferred_indexes, [])

        # PostgreSQL permits multiple NULLs in the retained unique calendar
        # UID. These are production-image writes: both new R1 columns are
        # omitted by the frozen historical model.
        HistoricalEvent = self.production_apps.get_model("events", "Event")
        HistoricalEvent.objects.create(
            slug="postgres-null-event-r1-a",
            title="Postgres NULL event A",
            start_datetime=timezone.now(),
        )
        HistoricalEvent.objects.create(
            slug="postgres-null-event-r1-b",
            title="Postgres NULL event B",
            start_datetime=timezone.now(),
        )
        R1Event = r1_apps.get_model("events", "Event")
        null_events = R1Event.objects.filter(
            slug__startswith="postgres-null-event-r1-",
            calendar_uid__isnull=True,
            host_access_version__isnull=True,
        )
        self.assertEqual(null_events.count(), 2)
