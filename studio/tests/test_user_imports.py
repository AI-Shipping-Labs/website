import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django_q.models import Schedule

from accounts.models import (
    IMPORT_SOURCE_COURSE_DB,
    IMPORT_SOURCE_SLACK,
    IMPORT_SOURCE_STRIPE,
    ImportBatch,
)
from accounts.services import import_users
from accounts.services.import_users import ImportRow, register_import_adapter
from accounts.tasks import run_import_batch_task, run_scheduled_import
from jobs.tasks import build_task_name
from payments.models import Tier

User = get_user_model()

TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}


class ImportRegistryMixin:
    def setUp(self):
        super().setUp()
        self._adapters = import_users.ADAPTERS.copy()

    def tearDown(self):
        import_users.ADAPTERS.clear()
        import_users.ADAPTERS.update(self._adapters)
        super().tearDown()


@override_settings(STORAGES=TEST_STORAGES)
class StudioUserImportsViewTest(ImportRegistryMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@example.com",
            password="testpass",
            is_staff=True,
        )
        cls.superuser = User.objects.create_superuser(
            email="super@example.com",
            password="testpass",
        )
        cls.member = User.objects.create_user(
            email="member@example.com",
            password="testpass",
        )

    def test_list_is_staff_only_and_renders_sidebar_link(self):
        response = self.client.get("/studio/imports/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

        self.client.login(email="member@example.com", password="testpass")
        response = self.client.get("/studio/imports/")
        self.assertEqual(response.status_code, 403)

        self.client.login(email="staff@example.com", password="testpass")
        response = self.client.get("/studio/imports/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "studio/imports/list.html")
        self.assertContains(response, "User imports")

    def test_list_filters_source_and_dry_run_newest_first(self):
        older = ImportBatch.objects.create(
            source=IMPORT_SOURCE_STRIPE,
            dry_run=False,
            status=ImportBatch.STATUS_COMPLETED,
        )
        newer = ImportBatch.objects.create(
            source=IMPORT_SOURCE_COURSE_DB,
            dry_run=True,
            status=ImportBatch.STATUS_COMPLETED,
            users_created=3,
        )

        self.client.login(email="staff@example.com", password="testpass")
        response = self.client.get("/studio/imports/?source=course_db&dry_run=yes")
        self.assertContains(response, "Course database")
        self.assertContains(response, "dry-run")
        self.assertContains(response, "3")
        self.assertEqual(list(response.context["page_obj"].object_list), [newer])
        self.assertLess(older.started_at, newer.started_at)

    def test_detail_and_fragment_render_audit_params_and_normalized_errors(self):
        batch = ImportBatch.objects.create(
            source=IMPORT_SOURCE_STRIPE,
            actor=self.staff,
            dry_run=True,
            status=ImportBatch.STATUS_COMPLETED,
            params={"default_tags": ["alpha"], "send_welcome": False},
            errors=[
                {
                    "kind": "conflict",
                    "row": 2,
                    "email": "a@example.com",
                    "field": "stripe_customer_id",
                    "incoming_value": "cus_1",
                    "incoming_source": "stripe",
                    "error_message": "conflicting value",
                }
            ],
        )
        self.client.login(email="staff@example.com", password="testpass")

        response = self.client.get(f"/studio/imports/{batch.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('"default_tags"', response.context["params_json"])
        self.assertContains(response, "conflicting value")
        self.assertContains(response, "stripe_customer_id")

        fragment = self.client.get(f"/studio/imports/{batch.pk}/fragment/")
        self.assertEqual(fragment.status_code, 200)
        self.assertContains(fragment, "conflicting value")

    @patch("studio.views.user_imports.async_task")
    def test_staff_dry_run_creates_one_batch_and_enqueues_task(self, mock_async_task):
        import_users.ADAPTERS.clear()
        register_import_adapter(IMPORT_SOURCE_STRIPE, lambda: iter([]))
        self.client.login(email="staff@example.com", password="testpass")

        response = self.client.post(
            "/studio/imports/new/",
            {
                "source": IMPORT_SOURCE_STRIPE,
                "dry_run": "on",
                "tags": " Alpha, beta ",
                "send_welcome": "on",
            },
        )

        batch = ImportBatch.objects.get()
        self.assertRedirects(response, f"/studio/imports/{batch.pk}/")
        self.assertEqual(batch.actor, self.staff)
        self.assertTrue(batch.dry_run)
        self.assertEqual(batch.status, ImportBatch.STATUS_RUNNING)
        self.assertEqual(batch.params["default_tags"], ["alpha", "beta"])
        self.assertFalse(batch.params["send_welcome"])
        mock_async_task.assert_called_once_with(
            "accounts.tasks.run_import_batch_task",
            batch.pk,
            task_name=build_task_name(
                "Run user import",
                f"{IMPORT_SOURCE_STRIPE} batch #{batch.pk} dry-run",
                "Studio user imports",
            ),
        )

    @patch("studio.views.user_imports.async_task")
    def test_live_import_requires_superuser(self, mock_async_task):
        import_users.ADAPTERS.clear()
        register_import_adapter(IMPORT_SOURCE_STRIPE, lambda: iter([]))
        self.client.login(email="staff@example.com", password="testpass")

        response = self.client.post(
            "/studio/imports/new/",
            {"source": IMPORT_SOURCE_STRIPE, "send_welcome": "on"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(ImportBatch.objects.count(), 0)
        mock_async_task.assert_not_called()

    @patch("studio.views.user_imports.async_task")
    def test_superuser_live_import_creates_batch(self, mock_async_task):
        import_users.ADAPTERS.clear()
        register_import_adapter(IMPORT_SOURCE_STRIPE, lambda: iter([]))
        self.client.login(email="super@example.com", password="testpass")

        response = self.client.post(
            "/studio/imports/new/",
            {"source": IMPORT_SOURCE_STRIPE, "send_welcome": "on"},
        )

        batch = ImportBatch.objects.get()
        self.assertRedirects(response, f"/studio/imports/{batch.pk}/")
        self.assertFalse(batch.dry_run)
        self.assertTrue(batch.params["send_welcome"])
        self.assertEqual(batch.actor, self.superuser)
        mock_async_task.assert_called_once()

    def test_form_marks_unregistered_adapter_unavailable(self):
        import_users.ADAPTERS.clear()
        self.client.login(email="staff@example.com", password="testpass")

        response = self.client.get("/studio/imports/new/")

        self.assertContains(response, "adapter unavailable")
        self.assertContains(response, "disabled")

    @patch("studio.views.user_imports.async_task")
    @override_settings(BASE_DIR=Path(tempfile.gettempdir()))
    def test_course_db_upload_validation_and_private_metadata(self, mock_async_task):
        import_users.ADAPTERS.clear()
        register_import_adapter(IMPORT_SOURCE_COURSE_DB, lambda csv_path: iter([]))
        self.client.login(email="staff@example.com", password="testpass")

        missing = self.client.post(
            "/studio/imports/new/",
            {"source": IMPORT_SOURCE_COURSE_DB, "dry_run": "on"},
        )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(ImportBatch.objects.count(), 0)

        upload = SimpleUploadedFile(
            "alumni.csv",
            b"email,name,course_slug\nada@example.com,Ada,mlops\n",
            content_type="text/csv",
        )
        response = self.client.post(
            "/studio/imports/new/",
            {
                "source": IMPORT_SOURCE_COURSE_DB,
                "dry_run": "on",
                "csv_file": upload,
            },
        )

        batch = ImportBatch.objects.get()
        self.assertRedirects(response, f"/studio/imports/{batch.pk}/")
        self.assertEqual(batch.params["csv_original_filename"], "alumni.csv")
        self.assertTrue(batch.params["csv_available"])
        self.assertNotIn("ada@example.com", str(batch.params))
        self.assertTrue(Path(batch.params["csv_path"]).exists())
        Path(batch.params["csv_path"]).unlink(missing_ok=True)

    @patch("studio.views.user_imports.async_task")
    def test_stripe_rejects_csv_upload(self, mock_async_task):
        import_users.ADAPTERS.clear()
        register_import_adapter(IMPORT_SOURCE_STRIPE, lambda: iter([]))
        self.client.login(email="staff@example.com", password="testpass")

        response = self.client.post(
            "/studio/imports/new/",
            {
                "source": IMPORT_SOURCE_STRIPE,
                "dry_run": "on",
                "csv_file": SimpleUploadedFile("x.csv", b"email\nx@example.com\n"),
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(ImportBatch.objects.count(), 0)
        mock_async_task.assert_not_called()

    @patch("studio.views.user_imports.async_task")
    def test_superuser_can_rerun_completed_live_batch(self, mock_async_task):
        original = ImportBatch.objects.create(
            source=IMPORT_SOURCE_STRIPE,
            dry_run=False,
            status=ImportBatch.STATUS_COMPLETED,
            params={"default_tags": ["paid"], "send_welcome": True},
        )
        self.client.login(email="super@example.com", password="testpass")

        response = self.client.post(f"/studio/imports/{original.pk}/rerun/")

        rerun = ImportBatch.objects.exclude(pk=original.pk).get()
        self.assertRedirects(response, f"/studio/imports/{rerun.pk}/")
        self.assertEqual(rerun.source, original.source)
        self.assertEqual(rerun.params["default_tags"], ["paid"])
        self.assertTrue(rerun.params["send_welcome"])
        mock_async_task.assert_called_once_with(
            "accounts.tasks.run_import_batch_task",
            rerun.pk,
            task_name=build_task_name(
                "Rerun user import",
                f"{IMPORT_SOURCE_STRIPE} batch #{rerun.pk} live",
                "Studio user imports",
            ),
        )

    @patch("studio.views.user_imports.async_task")
    def test_course_db_rerun_blocks_missing_upload(self, mock_async_task):
        original = ImportBatch.objects.create(
            source=IMPORT_SOURCE_COURSE_DB,
            dry_run=False,
            status=ImportBatch.STATUS_COMPLETED,
            params={"csv_path": "/missing/alumni.csv", "csv_available": False},
        )
        self.client.login(email="super@example.com", password="testpass")

        response = self.client.post(f"/studio/imports/{original.pk}/rerun/")

        self.assertRedirects(response, "/studio/imports/new/")
        self.assertEqual(ImportBatch.objects.count(), 1)
        mock_async_task.assert_not_called()

    def test_staff_can_view_schedule_state_but_not_toggle(self):
        call_command("setup_schedules")
        Schedule.objects.filter(name="import-stripe-daily").update(repeats=0)
        self.client.login(email="staff@example.com", password="testpass")

        response = self.client.get("/studio/imports/")

        self.assertContains(response, "Scheduled imports")
        self.assertContains(response, "Slack workspace")
        self.assertContains(response, "03:00 UTC")
        self.assertContains(response, "Stripe customers")
        self.assertContains(response, "03:30 UTC")
        self.assertContains(response, "Superuser permission is required")
        self.assertNotContains(response, "Course database</div>")
        self.assertNotContains(response, ">Disable</button>")
        self.assertNotContains(response, ">Enable</button>")

    def test_superuser_can_disable_and_enable_schedule(self):
        call_command("setup_schedules")
        self.client.login(email="super@example.com", password="testpass")

        response = self.client.post(
            "/studio/imports/schedules/slack/toggle/",
            {"action": "disable"},
        )

        self.assertRedirects(response, "/studio/imports/")
        schedule = Schedule.objects.get(name="import-slack-daily")
        self.assertEqual(schedule.repeats, 0)
        self.assertEqual(ImportBatch.objects.count(), 0)

        response = self.client.post(
            "/studio/imports/schedules/slack/toggle/",
            {"action": "enable"},
        )

        self.assertRedirects(response, "/studio/imports/")
        schedule.refresh_from_db()
        self.assertEqual(schedule.repeats, -1)

    def test_non_superuser_cannot_toggle_schedule(self):
        call_command("setup_schedules")
        self.client.login(email="staff@example.com", password="testpass")

        response = self.client.post(
            "/studio/imports/schedules/stripe/toggle/",
            {"action": "disable"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Schedule.objects.get(name="import-stripe-daily").repeats, -1)


class ImportBatchTaskTest(ImportRegistryMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@example.com",
            password="testpass",
            is_staff=True,
        )
        Tier.objects.get_or_create(slug="free", defaults={"name": "Free", "level": 0})
        Tier.objects.get_or_create(slug="main", defaults={"name": "Main", "level": 20})

    def test_task_executes_into_existing_batch_without_creating_duplicate(self):
        import_users.ADAPTERS.clear()
        register_import_adapter(
            IMPORT_SOURCE_STRIPE,
            lambda: iter([ImportRow(email="ada@example.com", tags=["source-tag"])]),
        )
        batch = ImportBatch.objects.create(
            source=IMPORT_SOURCE_STRIPE,
            actor=self.staff,
            dry_run=False,
            status=ImportBatch.STATUS_RUNNING,
            params={"default_tags": ["operator-tag"], "send_welcome": False},
        )

        run_import_batch_task(batch.pk)

        batch.refresh_from_db()
        self.assertEqual(ImportBatch.objects.count(), 1)
        self.assertEqual(batch.status, ImportBatch.STATUS_COMPLETED)
        self.assertEqual(batch.users_created, 1)
        user = User.objects.get(email="ada@example.com")
        self.assertEqual(set(user.tags), {"source-tag", "operator-tag"})

    def test_task_marks_same_batch_failed_when_adapter_missing(self):
        import_users.ADAPTERS.clear()
        batch = ImportBatch.objects.create(
            source=IMPORT_SOURCE_STRIPE,
            dry_run=True,
            status=ImportBatch.STATUS_RUNNING,
        )

        with self.assertRaises(Exception):
            run_import_batch_task(batch.pk)

        batch.refresh_from_db()
        self.assertEqual(ImportBatch.objects.count(), 1)
        self.assertEqual(batch.status, ImportBatch.STATUS_FAILED)
        self.assertIsNotNone(batch.finished_at)
        self.assertEqual(batch.errors[-1]["kind"], "task_failure")
        self.assertIn("No import adapter", batch.summary)

    @override_settings(BASE_DIR=Path(tempfile.gettempdir()))
    def test_course_db_task_reads_upload_and_marks_it_consumed(self):
        import_users.ADAPTERS.clear()
        from accounts.services.import_course_db import build_course_db_import_adapter

        register_import_adapter(IMPORT_SOURCE_COURSE_DB, build_course_db_import_adapter)
        upload_dir = Path(tempfile.gettempdir()) / "studio-import-task-test"
        upload_dir.mkdir(exist_ok=True)
        csv_path = upload_dir / "alumni.csv"
        csv_path.write_text(
            "email,name,course_slug\nada@example.com,Ada,mlops\n",
            encoding="utf-8",
        )
        batch = ImportBatch.objects.create(
            source=IMPORT_SOURCE_COURSE_DB,
            actor=self.staff,
            dry_run=True,
            status=ImportBatch.STATUS_RUNNING,
            params={
                "csv_path": str(csv_path),
                "csv_original_filename": "alumni.csv",
                "csv_available": True,
                "send_welcome": False,
            },
        )

        run_import_batch_task(batch.pk)

        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.STATUS_COMPLETED)
        self.assertEqual(batch.users_created, 1)
        self.assertFalse(csv_path.exists())
        self.assertFalse(batch.params["csv_available"])
        self.assertTrue(batch.params["csv_consumed"])


class ScheduledImportTaskTest(ImportRegistryMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        Tier.objects.get_or_create(slug="free", defaults={"name": "Free", "level": 0})

    def test_rejects_unsupported_source_without_creating_batch(self):
        with self.assertRaises(ValueError):
            run_scheduled_import(IMPORT_SOURCE_COURSE_DB)

        self.assertEqual(ImportBatch.objects.count(), 0)

    def test_successful_scheduled_slack_and_stripe_dispatch(self):
        for source in (IMPORT_SOURCE_SLACK, IMPORT_SOURCE_STRIPE):
            with self.subTest(source=source):
                import_users.ADAPTERS.clear()
                adapter = lambda: iter([ImportRow(email=f"{source}@example.com")])
                register_import_adapter(source, adapter)

                batch_id = run_scheduled_import(source)

                batch = ImportBatch.objects.get(pk=batch_id)
                self.assertEqual(batch.status, ImportBatch.STATUS_COMPLETED)
                self.assertFalse(batch.dry_run)
                self.assertIsNone(batch.actor)
                self.assertTrue(batch.params["send_welcome"])
                self.assertTrue(batch.params["scheduled"])
                self.assertEqual(batch.users_created, 1)
                self.assertEqual(batch.emails_queued, 1)

    def test_scheduled_import_calls_runner_with_live_system_options(self):
        import_users.ADAPTERS.clear()
        adapter = lambda: iter([])
        register_import_adapter(IMPORT_SOURCE_STRIPE, adapter)

        def fake_run(source, adapter_fn, **kwargs):
            batch = kwargs["batch"]
            self.assertEqual(source, IMPORT_SOURCE_STRIPE)
            self.assertIs(adapter_fn, adapter)
            self.assertFalse(kwargs["dry_run"])
            self.assertIsNone(kwargs["actor"])
            self.assertTrue(kwargs["send_welcome"])
            batch.status = ImportBatch.STATUS_COMPLETED
            batch.finished_at = batch.started_at
            batch.summary = "ok"
            batch.save()
            return batch

        with patch("accounts.tasks.run_import_batch", side_effect=fake_run) as mock_run:
            batch_id = run_scheduled_import(IMPORT_SOURCE_STRIPE)

        mock_run.assert_called_once()
        self.assertEqual(ImportBatch.objects.get(pk=batch_id).status, ImportBatch.STATUS_COMPLETED)

    def test_missing_adapter_records_failed_batch(self):
        import_users.ADAPTERS.clear()

        batch_id = run_scheduled_import(IMPORT_SOURCE_STRIPE)

        batch = ImportBatch.objects.get(pk=batch_id)
        self.assertEqual(batch.status, ImportBatch.STATUS_FAILED)
        self.assertIsNotNone(batch.finished_at)
        self.assertIn("No import adapter", batch.summary)
        self.assertEqual(batch.errors[-1]["kind"], "scheduled_import_failure")

    def test_failed_adapter_records_failed_batch_and_other_source_can_run(self):
        import_users.ADAPTERS.clear()
        register_import_adapter(
            IMPORT_SOURCE_SLACK,
            lambda: (_ for _ in ()).throw(CommandError("Slack token missing")),
        )
        register_import_adapter(
            IMPORT_SOURCE_STRIPE,
            lambda: iter([ImportRow(email="stripe-ok@example.com")]),
        )

        slack_batch_id = run_scheduled_import(IMPORT_SOURCE_SLACK)
        stripe_batch_id = run_scheduled_import(IMPORT_SOURCE_STRIPE)

        self.assertEqual(ImportBatch.objects.get(pk=slack_batch_id).status, ImportBatch.STATUS_FAILED)
        stripe_batch = ImportBatch.objects.get(pk=stripe_batch_id)
        self.assertEqual(stripe_batch.status, ImportBatch.STATUS_COMPLETED)
        self.assertEqual(stripe_batch.users_created, 1)

    @patch("accounts.tasks.mail_admins")
    def test_alerts_once_after_three_failure_streak(self, mock_mail_admins):
        import_users.ADAPTERS.clear()
        register_import_adapter(
            IMPORT_SOURCE_SLACK,
            lambda: (_ for _ in ()).throw(CommandError("Slack token missing")),
        )

        first = run_scheduled_import(IMPORT_SOURCE_SLACK)
        second = run_scheduled_import(IMPORT_SOURCE_SLACK)
        third = run_scheduled_import(IMPORT_SOURCE_SLACK)
        fourth = run_scheduled_import(IMPORT_SOURCE_SLACK)

        self.assertEqual(mock_mail_admins.call_count, 1)
        message = mock_mail_admins.call_args.kwargs["message"]
        self.assertIn("slack", message)
        self.assertIn(f"Latest batch id: {third}", message)
        self.assertIn("Slack token missing", message)
        self.assertTrue(ImportBatch.objects.get(pk=third).params["failure_alert_sent"])
        self.assertFalse(ImportBatch.objects.get(pk=first).params.get("failure_alert_sent", False))
        self.assertFalse(ImportBatch.objects.get(pk=second).params.get("failure_alert_sent", False))
        self.assertFalse(ImportBatch.objects.get(pk=fourth).params.get("failure_alert_sent", False))

    @patch("accounts.tasks.mail_admins")
    def test_success_resets_failure_alert_streak(self, mock_mail_admins):
        import_users.ADAPTERS.clear()
        failing = lambda: (_ for _ in ()).throw(CommandError("Stripe config missing"))
        successful = lambda: iter([ImportRow(email="reset@example.com")])
        register_import_adapter(IMPORT_SOURCE_STRIPE, failing)

        run_scheduled_import(IMPORT_SOURCE_STRIPE)
        run_scheduled_import(IMPORT_SOURCE_STRIPE)
        register_import_adapter(IMPORT_SOURCE_STRIPE, successful)
        run_scheduled_import(IMPORT_SOURCE_STRIPE)
        register_import_adapter(IMPORT_SOURCE_STRIPE, failing)
        run_scheduled_import(IMPORT_SOURCE_STRIPE)
        run_scheduled_import(IMPORT_SOURCE_STRIPE)

        self.assertEqual(mock_mail_admins.call_count, 0)

        run_scheduled_import(IMPORT_SOURCE_STRIPE)

        self.assertEqual(mock_mail_admins.call_count, 1)
