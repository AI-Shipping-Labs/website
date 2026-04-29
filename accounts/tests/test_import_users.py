from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from django_q.models import Schedule

from accounts.admin.import_batch import ImportBatchAdmin
from accounts.models import ImportBatch, TierOverride
from accounts.services import import_users
from accounts.services.import_users import (
    ImportRow,
    register_import_adapter,
    run_import_batch,
)
from email_app.models import EmailLog
from email_app.tasks.welcome_imported import send_imported_welcome_email
from payments.models import Tier

User = get_user_model()


def rows(*items):
    return lambda: iter(items)


class ImportUsersServiceTest(TestCase):
    def setUp(self):
        self.main_tier = Tier.objects.get(slug="main")

    def test_new_user_is_created_with_import_fields_tags_metadata_and_queue(self):
        batch = run_import_batch(
            "slack",
            rows(
                ImportRow(
                    email=" Ada@Example.com ",
                    name="Ada Lovelace",
                    source_metadata={"slack_user_id": "U123"},
                    tags=["AI Alumni", "ai-alumni"],
                    extra_user_fields={"slack_user_id": "U123"},
                )
            ),
            default_tags=["Imported"],
        )

        user = User.objects.get(email="ada@example.com")
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.import_source, "slack")
        self.assertIsNotNone(user.imported_at)
        self.assertEqual(user.import_metadata, {"slack": {"slack_user_id": "U123"}})
        self.assertEqual(user.tags, ["imported", "ai-alumni"])
        self.assertEqual(user.first_name, "Ada")
        self.assertEqual(user.last_name, "Lovelace")
        self.assertEqual(user.slack_user_id, "U123")
        self.assertEqual(batch.users_created, 1)
        self.assertEqual(batch.emails_queued, 1)
        self.assertEqual(Schedule.objects.count(), 1)

    def test_existing_manual_user_is_updated_case_insensitively(self):
        user = User.objects.create_user(
            email="ada@example.com",
            password="testpass",
            tags=["existing-tag"],
        )

        batch = run_import_batch(
            "stripe",
            rows(
                ImportRow(
                    email=" ADA@example.com ",
                    source_metadata={"customer": "cus_123"},
                    tags=["Existing Tag", "Paid"],
                    extra_user_fields={"stripe_customer_id": "cus_123"},
                )
            ),
        )

        self.assertEqual(User.objects.filter(email__iexact="ada@example.com").count(), 1)
        user.refresh_from_db()
        self.assertEqual(user.import_source, "stripe")
        self.assertEqual(user.stripe_customer_id, "cus_123")
        self.assertEqual(user.tags, ["existing-tag", "paid"])
        self.assertEqual(user.import_metadata, {"stripe": {"customer": "cus_123"}})
        self.assertEqual(batch.users_updated, 1)
        self.assertEqual(batch.users_created, 0)
        self.assertEqual(batch.emails_queued, 0)

    def test_existing_same_source_user_updates_without_blank_overwrite(self):
        user = User.objects.create_user(
            email="same@example.com",
            import_source="slack",
            imported_at=timezone.now(),
            slack_user_id="UOLD",
            import_metadata={"slack": {"old": True}},
            tags=["member"],
        )

        run_import_batch(
            "slack",
            rows(
                ImportRow(
                    email="same@example.com",
                    source_metadata={"new": True},
                    tags=["member", "active"],
                    extra_user_fields={"slack_user_id": ""},
                )
            ),
        )

        user.refresh_from_db()
        self.assertEqual(user.import_source, "slack")
        self.assertEqual(user.slack_user_id, "UOLD")
        self.assertEqual(user.import_metadata["slack"], {"old": True, "new": True})
        self.assertEqual(user.tags, ["member", "active"])

    def test_existing_different_source_preserves_earliest_non_manual_source(self):
        user = User.objects.create_user(
            email="multi@example.com",
            import_source="stripe",
            imported_at=timezone.now(),
            import_metadata={"stripe": {"customer": "cus_old"}},
        )

        run_import_batch(
            "slack",
            rows(
                ImportRow(
                    email="multi@example.com",
                    source_metadata={"slack_user_id": "U999"},
                    extra_user_fields={"slack_user_id": "U999"},
                )
            ),
        )

        user.refresh_from_db()
        self.assertEqual(user.import_source, "stripe")
        self.assertEqual(user.slack_user_id, "U999")
        self.assertEqual(
            user.import_metadata,
            {
                "stripe": {"customer": "cus_old"},
                "slack": {"slack_user_id": "U999"},
            },
        )

    def test_dry_run_records_counts_but_writes_no_users_or_jobs(self):
        existing = User.objects.create_user(email="dry@example.com", tags=["before"])

        batch = run_import_batch(
            "course_db",
            rows(
                ImportRow(email="dry@example.com", tags=["after"]),
                ImportRow(email="newdry@example.com", tags=["after"]),
            ),
            dry_run=True,
            default_tags=["batch-tag"],
        )

        self.assertEqual(batch.users_updated, 1)
        self.assertEqual(batch.users_created, 1)
        self.assertEqual(batch.emails_queued, 0)
        existing.refresh_from_db()
        self.assertEqual(existing.tags, ["before"])
        self.assertFalse(User.objects.filter(email="newdry@example.com").exists())
        self.assertEqual(Schedule.objects.count(), 0)

    def test_invalid_email_and_unknown_tier_are_row_errors(self):
        batch = run_import_batch(
            "course_db",
            rows(
                ImportRow(email="valid@example.com"),
                ImportRow(email="not-an-email"),
                ImportRow(email="tier@example.com", tier_slug="missing-tier"),
            ),
        )

        self.assertEqual(batch.status, ImportBatch.STATUS_COMPLETED)
        self.assertEqual(batch.users_created, 1)
        self.assertEqual(batch.users_skipped, 2)
        self.assertEqual(len(batch.errors), 2)
        self.assertTrue(User.objects.filter(email="valid@example.com").exists())

    def test_tier_slug_creates_active_override_without_changing_user_tier(self):
        expiry = timezone.now() + timedelta(days=30)
        batch = run_import_batch(
            "course_db",
            rows(
                ImportRow(
                    email="tiered@example.com",
                    tier_slug="main",
                    tier_expiry=expiry,
                )
            ),
        )

        user = User.objects.get(email="tiered@example.com")
        self.assertEqual(user.tier.slug, "free")
        override = TierOverride.objects.get(user=user)
        self.assertEqual(override.override_tier, self.main_tier)
        self.assertEqual(override.expires_at, expiry)
        self.assertEqual(batch.users_created, 1)

    @override_settings(IMPORT_WELCOME_EMAILS_PER_HOUR=50)
    def test_large_import_queues_throttled_schedules_without_sending_ses(self):
        import_rows = [
            ImportRow(email=f"user{i}@example.com")
            for i in range(1000)
        ]

        with patch("email_app.services.email_service.EmailService._send_ses") as send_ses:
            batch = run_import_batch("slack", rows(*import_rows))

        self.assertEqual(batch.users_created, 1000)
        self.assertEqual(batch.emails_queued, 1000)
        self.assertEqual(Schedule.objects.count(), 1000)
        send_ses.assert_not_called()
        first = Schedule.objects.order_by("next_run").first()
        fiftieth = Schedule.objects.order_by("next_run")[50]
        self.assertGreaterEqual((fiftieth.next_run - first.next_run).total_seconds(), 3600)


class WelcomeImportedEmailTaskTest(TestCase):
    @patch("email_app.services.email_service.EmailService._send_ses", return_value="ses-1")
    def test_welcome_email_renders_and_is_idempotent(self, _mock_send):
        user = User.objects.create_user(
            email="welcome@example.com",
            import_source="slack",
            tags=["slack-member"],
        )

        first = send_imported_welcome_email(user.pk)
        second = send_imported_welcome_email(user.pk)

        self.assertEqual(first["status"], "sent")
        self.assertEqual(second["status"], "skipped")
        self.assertEqual(second["reason"], "already_sent")
        self.assertEqual(
            EmailLog.objects.filter(user=user, email_type="welcome_imported").count(),
            1,
        )
        html_body = _mock_send.call_args.args[2]
        self.assertIn("Set your password", html_body)
        self.assertIn("/api/password-reset?token=", html_body)
        self.assertIn("Sign in to AI Shipping Labs", html_body)
        self.assertIn("/api/unsubscribe?token=", html_body)

    @patch("email_app.services.email_service.EmailService._send_ses")
    def test_unsubscribed_user_is_skipped(self, mock_send):
        user = User.objects.create_user(email="skip@example.com", unsubscribed=True)
        result = send_imported_welcome_email(user.pk)
        self.assertEqual(result["status"], "skipped")
        mock_send.assert_not_called()


class ImportUsersCommandTest(TestCase):
    def tearDown(self):
        import_users.ADAPTERS.clear()

    def test_unregistered_source_exits_nonzero(self):
        with self.assertRaises(CommandError):
            call_command("import_users", "slack")

    def test_command_dispatches_registered_adapter_and_prints_counts(self):
        register_import_adapter("slack", rows(ImportRow(email="cmd@example.com")))
        out = StringIO()

        call_command("import_users", "slack", "--tags", "Cmd Tag", stdout=out)

        self.assertIn("Import batch", out.getvalue())
        self.assertIn("1 created", out.getvalue())
        user = User.objects.get(email="cmd@example.com")
        self.assertEqual(user.tags, ["cmd-tag"])

    def test_command_supports_dry_run_and_no_send_welcome(self):
        register_import_adapter("stripe", rows(ImportRow(email="drycmd@example.com")))
        out = StringIO()

        call_command("import_users", "stripe", "--dry-run", "--no-send-welcome", stdout=out)

        self.assertIn("1 created", out.getvalue())
        self.assertFalse(User.objects.filter(email="drycmd@example.com").exists())
        self.assertEqual(Schedule.objects.count(), 0)


class ImportBatchAdminTest(TestCase):
    def test_import_batch_admin_registered_with_readonly_audit_fields(self):
        model_admin = admin.site._registry[ImportBatch]
        self.assertIsInstance(model_admin, ImportBatchAdmin)
        self.assertIn("source", model_admin.readonly_fields)
        self.assertIn("errors", model_admin.readonly_fields)
        self.assertIn("source", model_admin.list_filter)
