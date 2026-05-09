from io import StringIO
from tempfile import NamedTemporaryFile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django_q.models import Schedule

from accounts.models import IMPORT_SOURCE_COURSE_DB, ImportBatch, TierOverride
from accounts.services.import_course_db import (
    build_course_db_import_adapter,
    register_course_db_import_adapter,
)
from accounts.services.import_users import run_import_batch
from email_app.models import EmailLog
from email_app.tasks.welcome_imported import send_imported_welcome_email
from payments.models import Tier

User = get_user_model()


def csv_file(contents):
    handle = NamedTemporaryFile("w", suffix=".csv", encoding="utf-8", delete=False)
    handle.write(contents)
    handle.close()
    return handle.name


class CourseDbCsvAdapterTest(TestCase):
    def setUp(self):
        register_course_db_import_adapter()
        self.main_tier = Tier.objects.get(slug="main")

    def test_csv_parsing_aggregates_duplicate_email_in_first_seen_course_order(self):
        path = csv_file(
            "email,name,course_slug,enrollment_date,course_db_user_id,ignored\n"
            " Ada@Example.com ,Ada Lovelace,Data Engineering Zoomcamp,2024-01-01,101,x\n"
            "ada@example.com,Ada Later,ML_Zoomcamp,2024-02-01,102,y\n"
            "ada@example.com,Ada Later,data-engineering-zoomcamp,2024-01-01,101,z\n"
        )

        rows = list(build_course_db_import_adapter(path)())

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.email, "Ada@Example.com")
        self.assertEqual(row.name, "Ada Lovelace")
        self.assertEqual(row.tier_slug, "main")
        self.assertIsNone(row.tier_expiry)
        self.assertEqual(
            row.tags,
            ["course:data-engineering-zoomcamp", "course:ml-zoomcamp"],
        )
        self.assertEqual(
            row.source_metadata,
            {
                "course_slugs": ["data-engineering-zoomcamp", "ml-zoomcamp"],
                "enrollment_dates_by_course": {
                    "data-engineering-zoomcamp": ["2024-01-01"],
                    "ml-zoomcamp": ["2024-02-01"],
                },
                "course_db_user_ids": ["101", "102"],
            },
        )
        self.assertEqual(row.extra_user_fields, {})

    def test_missing_required_columns_fail_before_batch_creation(self):
        path = csv_file("email,name\nada@example.com,Ada\n")

        with self.assertRaises(CommandError) as error:
            call_command("import_users", "course_db", "--csv", path)

        self.assertIn("missing required column", str(error.exception))
        self.assertEqual(ImportBatch.objects.count(), 0)

    def test_command_requires_csv_for_course_db(self):
        with self.assertRaises(CommandError) as error:
            call_command("import_users", "course_db")

        self.assertIn("--csv is required", str(error.exception))
        self.assertEqual(ImportBatch.objects.count(), 0)

    def test_dry_run_counts_rows_but_writes_no_users_or_side_effects(self):
        path = csv_file(
            "email,name,course_slug,enrollment_date,course_db_user_id\n"
            "existing@example.com,Existing,data-engineering-zoomcamp,2024-01-01,1\n"
            "new@example.com,New,ml-zoomcamp,2024-02-01,2\n"
            "bad@example.com,Bad,,2024-03-01,3\n"
        )
        existing = User.objects.create_user(
            email="existing@example.com",
            tags=["before"],
            import_metadata={"stripe": {"customer": "cus_1"}},
        )
        out = StringIO()

        call_command(
            "import_users",
            "course_db",
            "--csv",
            path,
            "--dry-run",
            stdout=out,
        )

        batch = ImportBatch.objects.get(source=IMPORT_SOURCE_COURSE_DB)
        self.assertTrue(batch.dry_run)
        self.assertEqual(batch.users_updated, 1)
        self.assertEqual(batch.users_created, 1)
        self.assertEqual(batch.users_skipped, 1)
        self.assertEqual(batch.emails_queued, 0)
        existing.refresh_from_db()
        self.assertEqual(existing.tags, ["before"])
        self.assertEqual(existing.import_metadata, {"stripe": {"customer": "cus_1"}})
        self.assertFalse(User.objects.filter(email="new@example.com").exists())
        self.assertEqual(TierOverride.objects.count(), 0)
        self.assertEqual(Schedule.objects.count(), 0)
        self.assertIn("1 created, 1 updated, 1 skipped", out.getvalue())

    def test_live_import_applies_metadata_tags_main_override_and_preserves_tier(self):
        path = csv_file(
            "email,name,course_slug,enrollment_date,course_db_user_id\n"
            "alum@example.com,Ada Lovelace,data-engineering-zoomcamp,2024-01-01,101\n"
            "alum@example.com,Ada Lovelace,ml-zoomcamp,2024-02-01,102\n"
        )

        batch = run_import_batch(
            IMPORT_SOURCE_COURSE_DB,
            build_course_db_import_adapter(path),
        )

        user = User.objects.get(email="alum@example.com")
        self.assertEqual(user.import_source, IMPORT_SOURCE_COURSE_DB)
        self.assertEqual(user.tier.slug, "free")
        self.assertFalse(user.email_verified)
        self.assertEqual(
            user.tags,
            ["course:data-engineering-zoomcamp", "course:ml-zoomcamp"],
        )
        self.assertEqual(
            user.import_metadata["course_db"]["course_slugs"],
            ["data-engineering-zoomcamp", "ml-zoomcamp"],
        )
        override = TierOverride.objects.get(user=user, is_active=True)
        self.assertEqual(override.override_tier, self.main_tier)
        self.assertEqual(batch.users_created, 1)
        self.assertEqual(batch.emails_queued, 1)

    def test_existing_user_reconciliation_preserves_source_subscription_and_metadata(self):
        basic_tier = Tier.objects.get(slug="basic")
        user = User.objects.create_user(
            email="paid@example.com",
            import_source="stripe",
            stripe_customer_id="cus_existing",
            subscription_id="sub_existing",
            tier=basic_tier,
            tags=["paid"],
            import_metadata={"stripe": {"customer": "cus_existing"}},
        )
        path = csv_file(
            "email,name,course_slug,enrollment_date,course_db_user_id\n"
            "paid@example.com,Paid User,llm-zoomcamp,2024-01-01,501\n"
        )

        run_import_batch(IMPORT_SOURCE_COURSE_DB, build_course_db_import_adapter(path))

        user.refresh_from_db()
        self.assertEqual(user.import_source, "stripe")
        self.assertEqual(user.stripe_customer_id, "cus_existing")
        self.assertEqual(user.subscription_id, "sub_existing")
        self.assertEqual(user.tier, basic_tier)
        self.assertEqual(user.tags, ["paid", "course:llm-zoomcamp"])
        self.assertEqual(user.import_metadata["stripe"], {"customer": "cus_existing"})
        self.assertEqual(
            user.import_metadata["course_db"],
            {
                "course_slugs": ["llm-zoomcamp"],
                "enrollment_dates_by_course": {"llm-zoomcamp": ["2024-01-01"]},
                "course_db_user_ids": ["501"],
            },
        )
        self.assertEqual(
            TierOverride.objects.filter(user=user, is_active=True).count(),
            1,
        )

    def test_invalid_email_and_blank_course_slug_are_row_errors(self):
        path = csv_file(
            "email,name,course_slug\n"
            "valid@example.com,Valid,data-engineering-zoomcamp\n"
            "invalid-email,Bad,ml-zoomcamp\n"
            "blank@example.com,Blank,   \n"
        )

        batch = run_import_batch(
            IMPORT_SOURCE_COURSE_DB,
            build_course_db_import_adapter(path),
            send_welcome=False,
        )

        self.assertEqual(batch.users_created, 1)
        self.assertEqual(batch.users_skipped, 2)
        self.assertEqual(len(batch.errors), 2)
        self.assertEqual(
            {error["error_message"] for error in batch.errors},
            {"invalid email", "blank course_slug"},
        )
        self.assertTrue(User.objects.filter(email="valid@example.com").exists())

    def test_rerunning_same_csv_is_idempotent(self):
        path = csv_file(
            "email,name,course_slug,enrollment_date,course_db_user_id\n"
            "repeat@example.com,Repeat,data-engineering-zoomcamp,2024-01-01,101\n"
        )

        run_import_batch(IMPORT_SOURCE_COURSE_DB, build_course_db_import_adapter(path))
        user = User.objects.get(email="repeat@example.com")
        EmailLog.objects.create(user=user, email_type="welcome_imported")
        second_batch = run_import_batch(
            IMPORT_SOURCE_COURSE_DB,
            build_course_db_import_adapter(path),
        )

        user.refresh_from_db()
        self.assertEqual(User.objects.filter(email="repeat@example.com").count(), 1)
        self.assertEqual(user.tags, ["course:data-engineering-zoomcamp"])
        self.assertEqual(
            user.import_metadata["course_db"]["course_slugs"],
            ["data-engineering-zoomcamp"],
        )
        self.assertEqual(
            TierOverride.objects.filter(user=user, is_active=True).count(),
            1,
        )
        self.assertEqual(TierOverride.objects.filter(user=user).count(), 1)
        self.assertEqual(
            EmailLog.objects.filter(user=user, email_type="welcome_imported").count(),
            1,
        )
        self.assertEqual(second_batch.users_updated, 1)
        self.assertEqual(second_batch.emails_queued, 0)


class CourseDbImportCommandTest(TestCase):
    def setUp(self):
        register_course_db_import_adapter()

    def test_command_wires_csv_into_registered_course_db_adapter(self):
        path = csv_file("email,name,course_slug\ncmd@example.com,Cmd,data-talks\n")
        out = StringIO()

        call_command("import_users", "course_db", "--csv", path, stdout=out)

        self.assertIn("1 created", out.getvalue())
        self.assertTrue(User.objects.filter(email="cmd@example.com").exists())


class CourseDbWelcomeEmailTest(TestCase):
    @patch("email_app.services.email_service.EmailService._send_ses", return_value="ses-1")
    def test_course_db_welcome_copy_explains_datatalks_context(self, mock_send):
        user = User.objects.create_user(
            email="welcome-course@example.com",
            import_source=IMPORT_SOURCE_COURSE_DB,
            import_metadata={
                "course_db": {
                    "course_slugs": ["data-engineering-zoomcamp", "ml-zoomcamp"],
                },
            },
            tags=["course:data-engineering-zoomcamp", "course:ml-zoomcamp"],
        )

        result = send_imported_welcome_email(user.pk)

        self.assertEqual(result["status"], "sent")
        html_body = mock_send.call_args.args[2]
        self.assertIn("DataTalks course history", html_body)
        self.assertIn("data-engineering-zoomcamp", html_body)
        self.assertIn("ml-zoomcamp", html_body)
        self.assertIn("Set your password", html_body)
        self.assertIn("Sign in to AI Shipping Labs", html_body)
        self.assertEqual(mock_send.call_args.kwargs["email_kind"], "transactional")
        self.assertIsNone(mock_send.call_args.kwargs["unsubscribe_url"])
        self.assertNotIn("/api/unsubscribe?token=", html_body)
        self.assertNotIn("unsubscribe link below", html_body)
        self.assertIn("account deletion", html_body)
