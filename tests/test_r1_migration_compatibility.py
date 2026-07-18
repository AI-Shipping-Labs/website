"""Executable production-baseline compatibility matrix for issue #1266 R1."""

import copy
import importlib
import threading
import time
from datetime import timedelta

from django.core.management import call_command
from django.db import close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.fields import NOT_PROVIDED
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

# Exact 24 migration leaves deployed by dc07564604f3b2e329a19ab4e11375e6c7813480
# (immutable production tag 20260716-162837-dc07564). Keep this literal: the
# old-image compatibility floor must never move with graph.leaf_nodes().
PRODUCTION_DC075646_LEAVES = R1_EXPAND_LEAVES

POST_R1_CORRECTED_LEAVES = tuple(
    (app, "0018_questionnaire_response_audit_actions")
    if app == "community"
    else (app, "0056_reconcile_workshop_preview_tokens")
    if app == "content"
    else (app, "0021_reconcile_emaillog_subject_default")
    if app == "email_app"
    else (app, "0027_reconcile_synclog_observability_indexes")
    if app == "integrations"
    else (app, "0008_response_review_queue")
    if app == "questionnaires"
    else (app, migration)
    for app, migration in PRODUCTION_DC075646_LEAVES
)

ORIGINAL_CB4_MIGRATION_LEAVES = tuple(
    (app, "0018_questionnaire_response_audit_actions")
    if app == "community"
    else (app, "0055_workshop_preview_token")
    if app == "content"
    else (app, "0020_emaillog_recipient_subject_snapshots")
    if app == "email_app"
    else (app, "0026_synclog_observability_indexes")
    if app == "integrations"
    else (app, "0008_response_review_queue")
    if app == "questionnaires"
    else (app, migration)
    for app, migration in PRODUCTION_DC075646_LEAVES
)

ORIGINAL_1E5_DEV_LEAVES = tuple(
    (app, "0002_secure_delivery_state")
    if app == "triggers"
    else (app, "0016_bookedcall_host_nullable_last_event_at")
    if app == "community"
    else (app, migration)
    for app, migration in R1_EXPAND_LEAVES
)


@tag("postgres_migration")
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
        MigrationExecutor(connection).migrate(POST_R1_CORRECTED_LEAVES)
        super().tearDown()

    def test_historical_production_models_write_after_expand(self):
        old = self.production_apps
        User = old.get_model("accounts", "User")
        Tier = old.get_model("payments", "Tier")
        user = User.objects.create(email="legacy-r1@example.com", password="!")
        tier = Tier.objects.order_by("level").first()

        self.executor = MigrationExecutor(connection)
        self.executor.migrate(POST_R1_CORRECTED_LEAVES)

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

        MigrationExecutor(connection).migrate(POST_R1_CORRECTED_LEAVES)

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
        self.executor.migrate(POST_R1_CORRECTED_LEAVES)
        r1_apps = self.executor.loader.project_state(POST_R1_CORRECTED_LEAVES).apps

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


@tag("postgres_migration")
class PostR1ProductionOverlapCompatibilityTest(TransactionTestCase):
    """Exact dc075646 old-model overlap through the corrected candidate."""

    serialized_rollback = True

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(PRODUCTION_DC075646_LEAVES)
        self.old = self.executor.loader.project_state(
            PRODUCTION_DC075646_LEAVES,
        ).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate(POST_R1_CORRECTED_LEAVES)
        super().tearDown()

    def _legacy_user(self, suffix):
        return self.old.get_model("accounts", "User").objects.create(
            email=f"post-r1-{suffix}@example.com",
            password="!",
        )

    def test_exact_production_models_read_write_all_five_families(self):
        old = self.old
        user = self._legacy_user("primary")
        reviewer = self._legacy_user("reviewer")
        questionnaire = old.get_model(
            "questionnaires", "Questionnaire",
        ).objects.create(title="R1 review", slug="r1-review")

        self.executor = MigrationExecutor(connection)
        self.executor.migrate(POST_R1_CORRECTED_LEAVES)
        current = self.executor.loader.project_state(POST_R1_CORRECTED_LEAVES).apps

        HistoricalWorkshop = old.get_model("content", "Workshop")
        workshops = [
            HistoricalWorkshop.objects.create(
                slug=f"legacy-preview-{index}",
                title=f"Legacy preview {index}",
                date=timezone.now().date(),
            )
            for index in range(2)
        ]
        workshops[0].title = "Legacy preview updated"
        workshops[0].save(update_fields=["title", "updated_at"])
        self.assertEqual(
            HistoricalWorkshop.objects.get(pk=workshops[0].pk).title,
            "Legacy preview updated",
        )
        CurrentWorkshop = current.get_model("content", "Workshop")
        self.assertEqual(
            CurrentWorkshop.objects.filter(
                pk__in=[row.pk for row in workshops],
                preview_token__isnull=True,
            ).count(),
            2,
        )

        Campaign = old.get_model("email_app", "EmailCampaign")
        campaign = Campaign.objects.create(subject="Recoverable R1 subject", body="Body")
        HistoricalEmailLog = old.get_model("email_app", "EmailLog")
        event = old.get_model("events", "Event").objects.create(
            slug="post-r1-email-event",
            title="Post-R1 email event",
            start_datetime=timezone.now(),
        )
        logs = [
            HistoricalEmailLog.objects.create(user_id=user.pk, email_type="welcome"),
            HistoricalEmailLog.objects.create(
                user_id=user.pk,
                campaign_id=campaign.pk,
                email_type="campaign",
            ),
            HistoricalEmailLog.objects.create(
                user_id=user.pk,
                event_id=event.pk,
                email_type="event_reminder",
            ),
        ]
        logs[0].email_type = "welcome_updated"
        logs[0].save(update_fields=["email_type"])
        CurrentEmailLog = current.get_model("email_app", "EmailLog")
        self.assertEqual(
            list(
                CurrentEmailLog.objects.filter(pk__in=[row.pk for row in logs])
                .order_by("pk")
                .values_list("subject", flat=True)
            ),
            ["", "", ""],
        )

        HistoricalAudit = old.get_model("community", "CommunityAuditLog")
        legacy_audit = HistoricalAudit.objects.create(user_id=user.pk, action="invite")
        legacy_audit.details = "updated by exact R1"
        legacy_audit.save(update_fields=["details"])
        CurrentAudit = current.get_model("community", "CommunityAuditLog")
        reviewed_audit = CurrentAudit.objects.create(
            user_id=user.pk,
            action="questionnaire_response_reviewed",
        )
        historical_legacy_audit = HistoricalAudit.objects.get(pk=legacy_audit.pk)
        self.assertEqual(historical_legacy_audit.action, "invite")
        self.assertEqual(historical_legacy_audit.get_action_display(), "Invite")
        self.assertEqual(
            str(historical_legacy_audit),
            f"CommunityAuditLog object ({legacy_audit.pk})",
        )
        self.assertEqual(
            historical_legacy_audit.details,
            "updated by exact R1",
        )
        historical_reviewed_audit = HistoricalAudit.objects.get(pk=reviewed_audit.pk)
        self.assertEqual(
            historical_reviewed_audit.action,
            "questionnaire_response_reviewed",
        )
        self.assertEqual(
            historical_reviewed_audit.get_action_display(),
            "questionnaire_response_reviewed",
        )
        self.assertEqual(
            str(historical_reviewed_audit),
            f"CommunityAuditLog object ({reviewed_audit.pk})",
        )

        Source = old.get_model("integrations", "ContentSource")
        source = Source.objects.create(repo_name="AI-Shipping-Labs/r1-overlap")
        HistoricalSyncLog = old.get_model("integrations", "SyncLog")
        sync = HistoricalSyncLog.objects.create(source_id=source.pk, status="running")
        sync.status = "success"
        sync.save(update_fields=["status"])
        self.assertEqual(HistoricalSyncLog.objects.get(pk=sync.pk).status, "success")

        HistoricalResponse = old.get_model("questionnaires", "Response")
        draft, draft_created = HistoricalResponse.objects.get_or_create(
            questionnaire_id=questionnaire.pk,
            respondent_id=user.pk,
        )
        self.assertTrue(draft_created)
        same_draft, duplicate_created = HistoricalResponse.objects.get_or_create(
            questionnaire_id=questionnaire.pk,
            respondent_id=user.pk,
        )
        self.assertFalse(duplicate_created)
        self.assertEqual(same_draft.pk, draft.pk)

        # Exercise the production R1 draft-save path separately from the
        # later transition into the corrected submitted/reviewable schema.
        draft.save(update_fields=["updated_at"])
        historical_draft = HistoricalResponse.objects.get(pk=draft.pk)
        self.assertEqual(historical_draft.status, "draft")
        self.assertIsNone(historical_draft.submitted_at)

        draft.status = "submitted"
        draft.submitted_at = timezone.now()
        draft.save(update_fields=["status", "submitted_at", "updated_at"])
        pending = HistoricalResponse.objects.create(
            questionnaire_id=questionnaire.pk,
            respondent_id=reviewer.pk,
            status="submitted",
            submitted_at=timezone.now(),
        )
        CurrentResponse = current.get_model("questionnaires", "Response")
        self.assertEqual(
            CurrentResponse.objects.filter(
                pk__in=[draft.pk, pending.pk],
                reviewed_at__isnull=True,
                reviewed_by__isnull=True,
            ).count(),
            2,
        )
        CurrentResponse.objects.filter(pk=draft.pk).update(
            reviewed_at=timezone.now(),
            reviewed_by_id=reviewer.pk,
        )
        self.assertEqual(HistoricalResponse.objects.get(pk=draft.pk).status, "submitted")
        CurrentResponse.objects.filter(pk=draft.pk).update(
            reviewed_at=None,
            reviewed_by_id=None,
        )
        self.assertEqual(HistoricalResponse.objects.get(pk=draft.pk).status, "submitted")

        call_command("reconcile_r1_expand", verbosity=0)
        first_tokens = list(
            CurrentWorkshop.objects.filter(pk__in=[row.pk for row in workshops])
            .order_by("pk")
            .values_list("preview_token", flat=True)
        )
        first_subjects = list(
            CurrentEmailLog.objects.filter(pk__in=[row.pk for row in logs])
            .order_by("pk")
            .values_list("subject", flat=True)
        )
        self.assertTrue(all(first_tokens))
        self.assertEqual(len(set(first_tokens)), 2)
        self.assertEqual(first_subjects, ["", "Recoverable R1 subject", ""])
        call_command("reconcile_r1_expand", verbosity=0)
        self.assertEqual(
            first_tokens,
            list(
                CurrentWorkshop.objects.filter(pk__in=[row.pk for row in workshops])
                .order_by("pk")
                .values_list("preview_token", flat=True)
            ),
        )
        self.assertEqual(
            first_subjects,
            list(
                CurrentEmailLog.objects.filter(pk__in=[row.pk for row in logs])
                .order_by("pk")
                .values_list("subject", flat=True)
            ),
        )

    def test_postgresql_new_physical_contract_and_exact_indexes(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL catalog assertions run in the deploy gate")

        MigrationExecutor(connection).migrate(POST_R1_CORRECTED_LEAVES)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name, column_name, is_nullable, column_default
                  FROM information_schema.columns
                 WHERE table_schema = current_schema()
                   AND (table_name, column_name) IN (
                       ('content_workshop', 'preview_token'),
                       ('email_app_emaillog', 'subject')
                   )
                """,
            )
            columns = {
                (table, column): (nullable, default)
                for table, column, nullable, default in cursor.fetchall()
            }
            cursor.execute(
                """
                SELECT indexname, indexdef
                  FROM pg_indexes
                 WHERE schemaname = current_schema()
                   AND indexname IN (
                       'sync_src_status_started_idx',
                       'sync_batch_started_idx'
                   )
                """,
            )
            indexes = dict(cursor.fetchall())

        self.assertEqual(columns[("content_workshop", "preview_token")][0], "YES")
        self.assertEqual(columns[("email_app_emaillog", "subject")][0], "NO")
        self.assertIn("''", columns[("email_app_emaillog", "subject")][1])
        self.assertEqual(set(indexes), {
            "sync_src_status_started_idx",
            "sync_batch_started_idx",
        })
        self.assertIn("source_id, status, started_at DESC", indexes["sync_src_status_started_idx"])
        self.assertIn("batch_id, started_at DESC", indexes["sync_batch_started_idx"])

        index_migration = importlib.import_module(
            "integrations.migrations.0026_synclog_observability_indexes",
        )
        Source = self.old.get_model("integrations", "ContentSource")
        HistoricalSyncLog = self.old.get_model("integrations", "SyncLog")
        source = Source.objects.create(repo_name="AI-Shipping-Labs/index-overlap")

        # A wrong-definition same-name object must be replaced, and the
        # concurrent historical transaction must be allowed to write/read
        # while PostgreSQL's online index build waits for it to finish.
        with connection.cursor() as cursor:
            cursor.execute('DROP INDEX CONCURRENTLY "sync_src_status_started_idx"')
            cursor.execute(
                'CREATE INDEX "sync_src_status_started_idx" '
                'ON "integrations_synclog" ("status")',
            )

        overlap_started = threading.Event()
        overlap_finished = threading.Event()
        overlap_error = []

        def historical_writer_reader():
            close_old_connections()
            try:
                with transaction.atomic():
                    row = HistoricalSyncLog.objects.create(
                        source_id=source.pk,
                        status="running",
                    )
                    self.assertEqual(
                        HistoricalSyncLog.objects.get(pk=row.pk).status,
                        "running",
                    )
                    overlap_started.set()
                    time.sleep(0.5)
                    row.status = "success"
                    row.save(update_fields=["status"])
            except Exception as exc:  # pragma: no cover - reported in main thread
                overlap_error.append(exc)
                overlap_started.set()
            finally:
                close_old_connections()
                overlap_finished.set()

        thread = threading.Thread(target=historical_writer_reader)
        thread.start()
        self.assertTrue(overlap_started.wait(timeout=5))
        with connection.schema_editor(atomic=False) as schema_editor:
            index_migration.create_observability_indexes(None, schema_editor)
        self.assertTrue(overlap_finished.wait(timeout=5))
        thread.join(timeout=5)
        self.assertEqual(overlap_error, [])

        def index_fingerprint(name):
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT i.indisvalid,
                           i.indisunique,
                           i.indpred IS NULL,
                           access_method.amname,
                           i.indnkeyatts,
                           i.indnatts,
                           ARRAY(
                               SELECT pg_get_indexdef(
                                   i.indexrelid,
                                   position,
                                   true
                               )
                                 FROM generate_series(
                                     1,
                                     i.indnkeyatts
                                 ) AS position
                                ORDER BY position
                           ),
                           ARRAY(
                               SELECT i.indoption[position - 1]
                                 FROM generate_series(
                                     1,
                                     i.indnkeyatts
                                 ) AS position
                                ORDER BY position
                           )
                      FROM pg_index AS i
                      JOIN pg_class AS index_class
                        ON index_class.oid = i.indexrelid
                      JOIN pg_namespace AS namespace
                        ON namespace.oid = index_class.relnamespace
                      JOIN pg_am AS access_method
                        ON access_method.oid = index_class.relam
                     WHERE namespace.nspname = current_schema()
                       AND index_class.relname = %s
                    """,
                    [name],
                )
                return cursor.fetchone()

        expected_source_fingerprint = (
            True,
            False,
            True,
            "btree",
            3,
            3,
            ["source_id", "status", "started_at"],
            [0, 0, 3],
        )

        # Matching columns are not sufficient: a UNIQUE same-name index has
        # different write semantics and must be replaced by the intended
        # ordinary observability index.
        with connection.cursor() as cursor:
            cursor.execute('DROP INDEX CONCURRENTLY "sync_src_status_started_idx"')
            cursor.execute(
                'CREATE UNIQUE INDEX "sync_src_status_started_idx" '
                'ON "integrations_synclog" '
                '("source_id", "status", "started_at" DESC)',
            )
        with connection.schema_editor(atomic=False) as schema_editor:
            index_migration.create_observability_indexes(None, schema_editor)
        self.assertEqual(
            index_fingerprint("sync_src_status_started_idx"),
            expected_source_fingerprint,
        )

        # A partial same-name index also cannot serve all observability
        # queries, even when its ordered key expressions are identical.
        with connection.cursor() as cursor:
            cursor.execute('DROP INDEX CONCURRENTLY "sync_src_status_started_idx"')
            cursor.execute(
                'CREATE INDEX "sync_src_status_started_idx" '
                'ON "integrations_synclog" '
                '("source_id", "status", "started_at" DESC) '
                "WHERE status = 'running'",
            )
        with connection.schema_editor(atomic=False) as schema_editor:
            index_migration.create_observability_indexes(None, schema_editor)
        self.assertEqual(
            index_fingerprint("sync_src_status_started_idx"),
            expected_source_fingerprint,
        )

        # Simulate PostgreSQL's interrupted CREATE INDEX CONCURRENTLY
        # fingerprint. IF NOT EXISTS would preserve it; convergence must
        # detect indisvalid=false, drop it concurrently, and recreate it.
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE pg_index
                   SET indisvalid = false
                 WHERE indexrelid = 'sync_src_status_started_idx'::regclass
                """,
            )
        with connection.schema_editor(atomic=False) as schema_editor:
            index_migration.create_observability_indexes(None, schema_editor)
            index_migration.create_observability_indexes(None, schema_editor)
        self.assertEqual(
            index_fingerprint("sync_src_status_started_idx"),
            expected_source_fingerprint,
        )
        self.assertEqual(
            index_fingerprint("sync_batch_started_idx"),
            (
                True,
                False,
                True,
                "btree",
                2,
                2,
                ["batch_id", "started_at"],
                [0, 3],
            ),
        )

    def test_original_cb4_physical_fingerprint_converges_forward(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(ORIGINAL_CB4_MIGRATION_LEAVES)
        drift = self.executor.loader.project_state(ORIGINAL_CB4_MIGRATION_LEAVES).apps
        Workshop = drift.get_model("content", "Workshop")
        EmailLog = drift.get_model("email_app", "EmailLog")

        # Reproduce the two unsafe physical properties produced by the
        # original cb4eb3d migration bytes while leaving their recorder rows
        # applied. The forward migrations must repair, not fake/unapply, them.
        with connection.schema_editor() as schema_editor:
            workshop_field = Workshop._meta.get_field("preview_token")
            old_workshop_field = copy.copy(workshop_field)
            old_workshop_field.null = False
            schema_editor.alter_field(
                Workshop,
                workshop_field,
                old_workshop_field,
                strict=False,
            )
            subject_field = EmailLog._meta.get_field("subject")
            no_default = copy.copy(subject_field)
            no_default.db_default = NOT_PROVIDED
            schema_editor.alter_field(EmailLog, subject_field, no_default, strict=False)

        MigrationExecutor(connection).migrate(POST_R1_CORRECTED_LEAVES)
        with connection.cursor() as cursor:
            workshop_columns = {
                column.name: column
                for column in connection.introspection.get_table_description(
                    cursor,
                    "content_workshop",
                )
            }
            sync_constraints = connection.introspection.get_constraints(
                cursor,
                "integrations_synclog",
            )
        self.assertTrue(workshop_columns["preview_token"].null_ok)
        self.assertIn("sync_src_status_started_idx", sync_constraints)
        self.assertIn("sync_batch_started_idx", sync_constraints)
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT column_default
                      FROM information_schema.columns
                     WHERE table_schema = current_schema()
                       AND table_name = 'email_app_emaillog'
                       AND column_name = 'subject'
                    """,
                )
                self.assertIn("''", cursor.fetchone()[0])

        # Reproduce rollback-era legacy sentinels after the forward schema
        # repair. This is the data half of convergence, not merely an empty
        # schema fingerprint comparison.
        legacy_user = self._legacy_user("cb4-convergence")
        HistoricalWorkshop = self.old.get_model("content", "Workshop")
        legacy_workshop = HistoricalWorkshop.objects.create(
            slug="cb4-null-preview",
            title="CB4 null preview",
            date=timezone.now().date(),
        )
        Campaign = self.old.get_model("email_app", "EmailCampaign")
        campaign = Campaign.objects.create(subject="CB4 recoverable", body="Body")
        HistoricalEmailLog = self.old.get_model("email_app", "EmailLog")
        recoverable = HistoricalEmailLog.objects.create(
            user_id=legacy_user.pk,
            campaign_id=campaign.pk,
            email_type="campaign",
        )
        irrecoverable = HistoricalEmailLog.objects.create(
            user_id=legacy_user.pk,
            email_type="welcome",
        )

        call_command("reconcile_r1_expand", verbosity=0)
        Workshop.objects.get(pk=legacy_workshop.pk).refresh_from_db()
        first_tokens = list(
            Workshop.objects.filter(pk=legacy_workshop.pk).values_list(
                "preview_token",
                flat=True,
            )
        )
        first_subjects = list(
            EmailLog.objects.filter(pk__in=[recoverable.pk, irrecoverable.pk])
            .order_by("pk")
            .values_list("subject", flat=True)
        )
        self.assertTrue(first_tokens[0])
        self.assertEqual(first_subjects, ["CB4 recoverable", ""])
        call_command("reconcile_r1_expand", verbosity=0)
        self.assertEqual(
            first_tokens,
            list(
                Workshop.objects.filter(pk=legacy_workshop.pk).values_list(
                    "preview_token",
                    flat=True,
                )
            ),
        )
        self.assertEqual(
            first_subjects,
            list(
                EmailLog.objects.filter(pk__in=[recoverable.pk, irrecoverable.pk])
                .order_by("pk")
                .values_list("subject", flat=True)
            ),
        )
