import os
import shutil
import tempfile
from contextlib import nullcontext
from datetime import date
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from content.models import Article
from integrations.models import ContentSource, SyncLog
from integrations.services.github import sync_content_source
from integrations.services.github_sync.orchestration import _maybe_skip_unchanged_head


@override_settings(
    CONTENT_CDN_BASE="https://cdn.example.com",
    AWS_S3_CONTENT_BUCKET="content-bucket",
)
class ArticleImageSyncTest(TestCase):
    def setUp(self):
        self.repo = tempfile.TemporaryDirectory()
        self.source = ContentSource.objects.create(
            repo_name="AI-Shipping-Labs/content",
            webhook_secret="secret",
        )
        os.makedirs(os.path.join(self.repo.name, "blog", "images"))
        Image.new("RGB", (480, 320), "navy").save(
            os.path.join(self.repo.name, "blog", "images", "cover.jpg"),
            "JPEG",
        )
        with open(os.path.join(self.repo.name, "blog", "post.md"), "w", encoding="utf-8") as source_file:
            source_file.write(
                "---\n"
                "content_id: 13850000-0000-4000-8000-000000000001\n"
                "title: Image fixture\n"
                "slug: image-fixture\n"
                "date: 2026-08-01\n"
                "cover_image: images/cover.jpg\n"
                "---\n\n"
                "Before.\n\n![Inline](images/cover.jpg)\n"
            )

    def tearDown(self):
        self.repo.cleanup()

    def test_normal_sync_populates_manifest_without_mutating_author_fields(self):
        first = sync_content_source(self.source, repo_dir=self.repo.name)
        article = Article.objects.get(slug="image-fixture")
        original_markdown = article.content_markdown
        original_cover = article.cover_image_url

        self.assertEqual(first.items_created, 1)
        self.assertIn(original_cover, article.image_manifest)
        self.assertTrue(article.image_manifest_complete)
        self.assertEqual(article.data_json, {})
        self.assertIn("![Inline](https://cdn.example.com/content/blog/images/cover.jpg)", original_markdown)

        second = sync_content_source(self.source, repo_dir=self.repo.name)
        article.refresh_from_db()
        self.assertEqual(second.items_unchanged, 1)
        self.assertEqual(article.content_markdown, original_markdown)
        self.assertEqual(article.cover_image_url, original_cover)

    @patch("integrations.services.github_sync.orchestration.fetch_remote_head_sha")
    def test_unprocessed_manifest_disables_unchanged_head_fast_path(self, fetch_head):
        article = Article.objects.create(
            title="Legacy",
            slug="legacy-manifest",
            date=date(2026, 8, 1),
            source_repo=self.source.repo_name,
            source_path="blog/post.md",
            image_manifest={},
            image_manifest_complete=False,
        )
        self.source.last_synced_commit = "a" * 40
        self.source.save(update_fields=["last_synced_commit"])

        result = _maybe_skip_unchanged_head(
            self.source,
            repo_dir=None,
            batch_id=None,
            force=False,
        )

        self.assertIsNone(result)
        fetch_head.assert_not_called()
        article.delete()

    @patch("integrations.services.github_sync.orchestration.fetch_remote_head_sha")
    def test_reconciled_empty_manifest_states_restore_fast_path(self, fetch_head):
        for index, title in enumerate(
            ("Coverless", "External only", "Unsupported", "Corrupt fallback"),
            start=1,
        ):
            Article.objects.create(
                title=title,
                slug=f"completed-empty-{index}",
                date=date(2026, 8, index),
                source_repo=self.source.repo_name,
                source_path=f"blog/empty-{index}.md",
                image_manifest={},
                image_manifest_complete=True,
            )
        commit_sha = "b" * 40
        self.source.last_synced_commit = commit_sha
        self.source.last_sync_status = "success"
        self.source.save(update_fields=["last_synced_commit", "last_sync_status"])
        SyncLog.objects.create(
            source=self.source,
            status="success",
            commit_sha=commit_sha,
            finished_at=timezone.now(),
        )
        fetch_head.return_value = commit_sha

        result = _maybe_skip_unchanged_head(
            self.source,
            repo_dir=None,
            batch_id=None,
            force=False,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.status, "skipped")
        fetch_head.assert_called_once()

    @patch("integrations.services.github_sync.orchestration.fetch_remote_head_sha")
    def test_terminal_image_warning_allows_skip_but_retryable_warning_does_not(self, fetch_head):
        Article.objects.create(
            title="Completed fallback",
            slug="completed-warning",
            date=date(2026, 8, 1),
            source_repo=self.source.repo_name,
            source_path="blog/warning.md",
            image_manifest={},
            image_manifest_complete=True,
        )
        commit_sha = "c" * 40
        self.source.last_synced_commit = commit_sha
        self.source.save(update_fields=["last_synced_commit"])
        terminal_log = SyncLog.objects.create(
            source=self.source,
            status="partial",
            commit_sha=commit_sha,
            finished_at=timezone.now(),
            errors=[
                {
                    "file": "blog/warning.md",
                    "step": "article_image_variant",
                    "retryable": False,
                    "error": "source is corrupt or unsupported",
                }
            ],
        )
        fetch_head.return_value = commit_sha

        skipped = _maybe_skip_unchanged_head(
            self.source,
            repo_dir=None,
            batch_id=None,
            force=False,
        )
        self.assertIsNotNone(skipped)

        SyncLog.objects.filter(pk=skipped.pk).delete()
        terminal_log.delete()
        SyncLog.objects.create(
            source=self.source,
            status="partial",
            commit_sha=commit_sha,
            finished_at=timezone.now(),
            errors=[
                {
                    "file": "blog/warning.md",
                    "step": "article_image_variant",
                    "retryable": True,
                    "error": "S3 temporarily unavailable",
                }
            ],
        )
        fetch_head.reset_mock()

        not_skipped = _maybe_skip_unchanged_head(
            self.source,
            repo_dir=None,
            batch_id=None,
            force=False,
        )
        self.assertIsNone(not_skipped)
        fetch_head.assert_not_called()

    def test_backfill_dry_run_then_write_is_scoped_idempotent_and_non_destructive(self):
        sync_content_source(self.source, repo_dir=self.repo.name)
        article = Article.objects.get(slug="image-fixture")
        Article.objects.filter(pk=article.pk).update(
            image_manifest={},
            image_manifest_complete=False,
        )
        original_path = os.path.join(self.repo.name, "blog", "images", "cover.jpg")

        call_command(
            "backfill_article_image_variants",
            "--dry-run",
            "--source",
            "content",
            "--article",
            article.slug,
            "--repo-dir",
            self.repo.name,
        )
        article.refresh_from_db()
        self.assertEqual(article.image_manifest, {})
        self.assertFalse(article.image_manifest_complete)

        call_command(
            "backfill_article_image_variants",
            "--source",
            "content",
            "--article",
            article.slug,
            "--repo-dir",
            self.repo.name,
        )
        article.refresh_from_db()
        first_manifest = article.image_manifest
        self.assertTrue(first_manifest)
        self.assertTrue(article.image_manifest_complete)
        self.assertTrue(os.path.isfile(original_path))

        call_command(
            "backfill_article_image_variants",
            "--source",
            "content",
            "--article",
            article.slug,
            "--repo-dir",
            self.repo.name,
        )
        article.refresh_from_db()
        self.assertEqual(article.image_manifest, first_manifest)


@override_settings(
    CONTENT_CDN_BASE="https://cdn.example.com",
    AWS_S3_CONTENT_BUCKET="content-bucket",
)
class ArticleImageMissingReferenceFastPathTest(TestCase):
    commit_sha = "d" * 40

    def setUp(self):
        self.repo = tempfile.TemporaryDirectory()
        self.source = ContentSource.objects.create(
            repo_name="AI-Shipping-Labs/content-missing-images",
            webhook_secret="secret",
        )
        os.makedirs(os.path.join(self.repo.name, "blog", "images"))

    def tearDown(self):
        self.repo.cleanup()

    def _write_article(self, *, cover="", body="Body."):
        cover_line = f"cover_image: {cover}\n" if cover else ""
        with open(
            os.path.join(self.repo.name, "blog", "missing.md"),
            "w",
            encoding="utf-8",
        ) as source_file:
            source_file.write(
                "---\n"
                "content_id: 13850000-0000-4000-8000-000000000099\n"
                "title: Missing image fixture\n"
                "slug: missing-image-fixture\n"
                "date: 2026-08-01\n"
                f"{cover_line}"
                "---\n\n"
                f"{body}\n"
            )

    def _sync_twice(self, *, transient_store_error=False):
        def clone_fixture(_repo_name, destination, _is_private):
            shutil.copytree(self.repo.name, destination, dirs_exist_ok=True)
            return self.commit_sha

        store_context = (
            patch(
                "integrations.services.article_images._store_variant",
                side_effect=ClientError(
                    {"Error": {"Code": "ServiceUnavailable"}},
                    "PutObject",
                ),
            )
            if transient_store_error
            else nullcontext()
        )
        with (
            patch(
                "integrations.services.github_sync.orchestration.acquire_sync_lock",
                return_value=True,
            ),
            patch(
                "integrations.services.github_sync.orchestration.release_sync_lock",
                return_value=None,
            ),
            patch(
                "integrations.services.github_sync.orchestration.upload_images_to_s3",
                return_value={"uploaded": 0, "skipped": 0, "errors": []},
            ),
            patch(
                "integrations.services.github_sync.orchestration.clone_or_pull_repo",
                side_effect=clone_fixture,
            ) as clone_repo,
            patch(
                "integrations.services.github_sync.orchestration.fetch_remote_head_sha",
                return_value=self.commit_sha,
            ) as fetch_head,
            store_context,
        ):
            first = sync_content_source(self.source)
            second = sync_content_source(self.source)
        return first, second, clone_repo.call_count, fetch_head.call_count

    def test_real_missing_cover_is_terminal_and_next_unchanged_sync_skips(self):
        self._write_article(cover="images/missing-cover.jpg")

        first, second, clone_count, fetch_count = self._sync_twice()

        article = Article.objects.get(slug="missing-image-fixture")
        self.assertTrue(article.image_manifest_complete)
        self.assertEqual(article.cover_image_url, "")
        self.assertEqual(first.status, "partial")
        self.assertEqual(
            [(error.get("step"), error.get("retryable")) for error in first.errors],
            [("cover_image_missing", False)],
        )
        self.assertEqual(second.status, "skipped")
        self.assertEqual(clone_count, 1)
        self.assertEqual(fetch_count, 1)

    def test_real_missing_body_image_is_terminal_and_next_unchanged_sync_skips(self):
        self._write_article(body="![Missing](images/missing-body.jpg)")

        first, second, clone_count, fetch_count = self._sync_twice()

        article = Article.objects.get(slug="missing-image-fixture")
        self.assertTrue(article.image_manifest_complete)
        self.assertEqual(first.status, "partial")
        self.assertEqual(
            [(error.get("step"), error.get("retryable")) for error in first.errors],
            [("image_reference_missing", False)],
        )
        self.assertEqual(second.status, "skipped")
        self.assertEqual(clone_count, 1)
        self.assertEqual(fetch_count, 1)

    def test_real_missing_cover_and_body_terminal_warnings_skip_together(self):
        self._write_article(
            cover="images/missing-cover.jpg",
            body="![Missing](images/missing-body.jpg)",
        )

        first, second, clone_count, fetch_count = self._sync_twice()

        self.assertEqual(first.status, "partial")
        self.assertCountEqual(
            [
                (error.get("step"), error.get("retryable"))
                for error in first.errors
            ],
            [
                ("cover_image_missing", False),
                ("image_reference_missing", False),
            ],
        )
        self.assertEqual(second.status, "skipped")
        self.assertEqual(clone_count, 1)
        self.assertEqual(fetch_count, 1)

    def test_real_terminal_and_retryable_mix_reclones_without_head_skip(self):
        Image.new("RGB", (480, 320), "navy").save(
            os.path.join(self.repo.name, "blog", "images", "cover.jpg"),
            "JPEG",
        )
        self._write_article(
            cover="images/cover.jpg",
            body="![Missing](images/missing-body.jpg)",
        )

        first, second, clone_count, fetch_count = self._sync_twice(
            transient_store_error=True,
        )

        article = Article.objects.get(slug="missing-image-fixture")
        self.assertFalse(article.image_manifest_complete)
        self.assertEqual(first.status, "partial")
        self.assertEqual(second.status, "partial")
        self.assertCountEqual(
            [
                (error.get("step"), error.get("retryable"))
                for error in first.errors
            ],
            [
                ("image_reference_missing", False),
                ("article_image_variant", True),
            ],
        )
        self.assertEqual(clone_count, 2)
        self.assertEqual(fetch_count, 0)
