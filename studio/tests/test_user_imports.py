import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from accounts.models import (
    IMPORT_SOURCE_COURSE_DB,
    IMPORT_SOURCE_STRIPE,
    ImportBatch,
)
from accounts.services import import_users
from accounts.services.import_users import ImportRow, register_import_adapter
from accounts.tasks import run_import_batch_task
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
