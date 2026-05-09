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
from accounts.admin.user import UserAdmin
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


def conflicts(batch):
    return [error for error in batch.errors if error.get("kind") == "conflict"]


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

    def test_same_source_identifier_conflict_keeps_existing_value(self):
        user = User.objects.create_user(
            email="same-conflict@example.com",
            import_source="stripe",
            imported_at=timezone.now(),
            stripe_customer_id="cus_existing",
            import_metadata={"stripe": {"stripe_customer_id": "cus_existing"}},
            tags=["stripe:imported"],
        )

        batch = run_import_batch(
            "stripe",
            rows(
                ImportRow(
                    email="same-conflict@example.com",
                    source_metadata={"stripe_customer_id": "cus_new", "status": "active"},
                    tags=["stripe:active"],
                    extra_user_fields={"stripe_customer_id": "cus_new"},
                )
            ),
        )

        user.refresh_from_db()
        self.assertEqual(user.stripe_customer_id, "cus_existing")
        self.assertEqual(user.import_metadata["stripe"]["stripe_customer_id"], "cus_new")
        self.assertEqual(user.import_metadata["stripe"]["status"], "active")
        self.assertEqual(user.tags, ["stripe:imported", "stripe:active"])
        self.assertEqual(batch.users_updated, 1)
        self.assertEqual(batch.users_skipped, 0)
        self.assertEqual(conflicts(batch)[0]["field"], "stripe_customer_id")

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

    def test_cross_source_merge_logs_conflicts_and_fills_only_empty_fields(self):
        user = User.objects.create_user(
            email="merge-conflict@example.com",
            first_name="Alice",
            last_name="Existing",
            import_source="slack",
            imported_at=timezone.now(),
            slack_user_id="U1",
            stripe_customer_id="cus_existing",
            import_metadata={"slack": {"slack_user_id": "U1"}},
            tags=["slack-member"],
        )

        batch = run_import_batch(
            "stripe",
            rows(
                ImportRow(
                    email="merge-conflict@example.com",
                    name="Alicia Incoming",
                    source_metadata={"stripe_customer_id": "cus_new"},
                    tags=["stripe:active", "slack-member"],
                    extra_user_fields={
                        "stripe_customer_id": "cus_new",
                        "subscription_id": "sub_new",
                    },
                )
            ),
        )

        user.refresh_from_db()
        self.assertEqual(user.import_source, "slack")
        self.assertEqual(user.first_name, "Alice")
        self.assertEqual(user.last_name, "Existing")
        self.assertEqual(user.stripe_customer_id, "cus_existing")
        self.assertEqual(user.subscription_id, "sub_new")
        self.assertEqual(user.tags, ["slack-member", "stripe:active"])
        self.assertEqual(user.import_metadata["stripe"]["stripe_customer_id"], "cus_new")
        self.assertEqual(batch.users_updated, 1)
        self.assertEqual(batch.users_skipped, 0)
        self.assertEqual(
            {conflict["field"] for conflict in conflicts(batch)},
            {"first_name", "last_name", "stripe_customer_id"},
        )

    def test_manual_user_becomes_imported_from_first_source_only(self):
        user = User.objects.create_user(email="manual@example.com")

        run_import_batch(
            "slack",
            rows(
                ImportRow(
                    email="manual@example.com",
                    source_metadata={"slack_user_id": "U1"},
                    tags=["slack-member"],
                )
            ),
        )
        run_import_batch(
            "stripe",
            rows(
                ImportRow(
                    email="manual@example.com",
                    source_metadata={"stripe_customer_id": "cus_1"},
                    tags=["stripe:active"],
                )
            ),
        )

        user.refresh_from_db()
        self.assertEqual(user.import_source, "slack")
        self.assertIsNotNone(user.imported_at)
        self.assertEqual(set(user.import_metadata), {"slack", "stripe"})
        self.assertEqual(user.tags, ["slack-member", "stripe:active"])

    def test_plus_address_aliases_are_distinct_imported_users(self):
        batch = run_import_batch(
            "stripe",
            rows(
                ImportRow(email="alice@example.com", tags=["base"]),
                ImportRow(email="alice+stripe@example.com", tags=["plus"]),
            ),
            send_welcome=False,
        )

        self.assertEqual(batch.users_created, 2)
        base = User.objects.get(email="alice@example.com")
        plus = User.objects.get(email="alice+stripe@example.com")
        self.assertNotEqual(base.pk, plus.pk)
        self.assertEqual(base.tags, ["base"])
        self.assertEqual(plus.tags, ["plus"])

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

    def test_dry_run_detects_conflicts_without_user_writes(self):
        existing = User.objects.create_user(
            email="dry-conflict@example.com",
            import_source="slack",
            imported_at=timezone.now(),
            slack_user_id="UOLD",
            import_metadata={"slack": {"slack_user_id": "UOLD"}},
            tags=["before"],
        )

        batch = run_import_batch(
            "slack",
            rows(
                ImportRow(
                    email="dry-conflict@example.com",
                    source_metadata={"slack_user_id": "UNEW"},
                    tags=["after"],
                    extra_user_fields={"slack_user_id": "UNEW"},
                )
            ),
            dry_run=True,
        )

        self.assertEqual(batch.users_updated, 1)
        self.assertEqual(conflicts(batch)[0]["field"], "slack_user_id")
        existing.refresh_from_db()
        self.assertEqual(existing.slack_user_id, "UOLD")
        self.assertEqual(existing.import_metadata, {"slack": {"slack_user_id": "UOLD"}})
        self.assertEqual(existing.tags, ["before"])

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

    def test_unknown_and_protected_extra_fields_are_row_errors(self):
        batch = run_import_batch(
            "slack",
            rows(
                ImportRow(email="valid-fields@example.com"),
                ImportRow(
                    email="protected@example.com",
                    extra_user_fields={"tier": "main"},
                ),
                ImportRow(
                    email="unknown-field@example.com",
                    extra_user_fields={"not_a_user_field": "value"},
                ),
            ),
        )

        self.assertEqual(batch.users_created, 1)
        self.assertEqual(batch.users_skipped, 2)
        self.assertEqual(
            {error["error_message"] for error in batch.errors},
            {"protected user field: tier", "unknown user field: not_a_user_field"},
        )
        self.assertTrue(User.objects.filter(email="valid-fields@example.com").exists())

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

    def test_inactive_stripe_tier_slug_does_not_create_override(self):
        user = User.objects.create_user(email="inactive-stripe-tier@example.com")

        batch = run_import_batch(
            "stripe",
            rows(
                ImportRow(
                    email="inactive-stripe-tier@example.com",
                    tier_slug="main",
                    subscription_active=False,
                )
            ),
        )

        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "free")
        self.assertFalse(TierOverride.objects.filter(user=user).exists())
        self.assertEqual(batch.users_updated, 1)

    def test_tier_conflict_preserves_existing_active_override(self):
        premium_tier = Tier.objects.get(slug="premium")
        user = User.objects.create_user(email="tier-conflict@example.com")
        TierOverride.objects.create(
            user=user,
            original_tier=user.tier,
            override_tier=premium_tier,
            expires_at=timezone.now() + timedelta(days=30),
        )

        batch = run_import_batch(
            "course_db",
            rows(ImportRow(email="tier-conflict@example.com", tier_slug="main")),
        )

        override = TierOverride.objects.get(user=user, is_active=True)
        self.assertEqual(override.override_tier, premium_tier)
        self.assertEqual(TierOverride.objects.filter(user=user).count(), 1)
        self.assertEqual(batch.users_updated, 1)
        self.assertEqual(conflicts(batch)[0]["field"], "tier_override")

    def test_same_tier_override_is_idempotent(self):
        user = User.objects.create_user(email="tier-same@example.com")
        TierOverride.objects.create(
            user=user,
            original_tier=user.tier,
            override_tier=self.main_tier,
            expires_at=timezone.now() + timedelta(days=30),
        )

        batch = run_import_batch(
            "course_db",
            rows(ImportRow(email="tier-same@example.com", tier_slug="main")),
        )

        self.assertEqual(TierOverride.objects.filter(user=user).count(), 1)
        self.assertEqual(batch.users_updated, 1)
        self.assertEqual(conflicts(batch), [])

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
        self.assertEqual(_mock_send.call_args.kwargs["email_kind"], "transactional")
        self.assertIsNone(_mock_send.call_args.kwargs["unsubscribe_url"])
        self.assertNotIn("/api/unsubscribe?token=", html_body)

    @patch("email_app.services.email_service.EmailService._send_ses")
    def test_unsubscribed_user_is_skipped(self, mock_send):
        user = User.objects.create_user(email="skip@example.com", unsubscribed=True)
        result = send_imported_welcome_email(user.pk)
        self.assertEqual(result["status"], "skipped")
        mock_send.assert_not_called()


class ImportUsersCommandTest(TestCase):
    def setUp(self):
        import_users.ADAPTERS.clear()

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


class UserAdminTest(TestCase):
    def test_community_fieldset_exposes_slack_membership_fields(self):
        model_admin = admin.site._registry[User]
        self.assertIsInstance(model_admin, UserAdmin)
        community_fields = dict(model_admin.fieldsets)["Community"]["fields"]
        self.assertEqual(
            community_fields,
            ("slack_user_id", "slack_member", "slack_checked_at"),
        )
