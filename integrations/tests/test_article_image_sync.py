import os
import tempfile
from contextlib import nullcontext
from datetime import date
from unittest.mock import patch

from botocore.exceptions import ClientError
from community_base.content_sync.checkout import ImmutableCheckout
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from content.models import Article
from integrations.models import ContentSource, SyncLog
from integrations.services.content_sync import run_sync
from integrations.services.github import sync_content_source


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

    @patch("integrations.services.content_sync.package_sync_content_source")
    def test_unprocessed_manifest_disables_unchanged_head_fast_path(self, package_sync):
        """The site gate escalates force while article manifests are incomplete."""
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
        package_sync.return_value = SyncLog.objects.create(
            source=self.source, status="success",
        )

        log = run_sync(self.source)

        self.assertIs(log, package_sync.return_value)
        self.assertTrue(package_sync.call_args.kwargs["force"])
        article.delete()

    @patch("integrations.services.content_sync.package_sync_content_source")
    def test_reconciled_empty_manifest_states_restore_fast_path(self, package_sync):
        """Fully reconciled manifests leave the fast-path decision to the package."""
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
        self.source.last_synced_commit = "b" * 40
        self.source.last_sync_status = "success"
        self.source.save(update_fields=["last_synced_commit", "last_sync_status"])
        package_sync.return_value = SyncLog.objects.create(
            source=self.source, status="skipped",
        )

        run_sync(self.source)

        self.assertFalse(package_sync.call_args.kwargs["force"])

    @patch("community_base.content_sync.github.GitHubClient.resolve_commit")
    def test_partial_last_sync_blocks_unchanged_head_skip(self, resolve_commit):
        """Package rule: any partial last sync blocks the skip, regardless of retryability."""
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
        self.source.last_sync_status = "partial"
        self.source.save(update_fields=["last_synced_commit", "last_sync_status"])
        SyncLog.objects.create(
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
        resolve_commit.return_value = commit_sha

        log = sync_content_source(self.source)

        self.assertNotEqual(log.status, "skipped")
        resolve_commit.assert_called_once()

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

    @staticmethod
    def _terminal_steps(log):
        """Rich per-file entries only; the package adds one bounded family entry."""
        return [
            (error.get("step"), error.get("retryable"))
            for error in log.errors
            if "step" in error
        ]

    @staticmethod
    def _terminal_steps(log):
        """Rich per-file entries only; the package adds one bounded family entry."""
        return [
            (error.get("step"), error.get("retryable"))
            for error in log.errors
            if "step" in error
        ]

    def _sync_twice(self, *, transient_store_error=False):
        def checkout_fixture(source, client=None, commit_sha=None):
            return ImmutableCheckout(self.repo.name, commit_sha=self.commit_sha)

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
                "content.sync_parsers.media.upload_images_to_s3",
                return_value={"uploaded": 0, "skipped": 0, "errors": []},
            ),
            patch(
                "community_base.content_sync.github.checkout_repository",
                side_effect=checkout_fixture,
            ) as clone_repo,
            patch(
                "community_base.content_sync.github.GitHubClient.resolve_commit",
                return_value=self.commit_sha,
            ) as fetch_head,
            store_context,
        ):
            first = sync_content_source(self.source)
            second = sync_content_source(self.source)
        return first, second, clone_repo.call_count, fetch_head.call_count

    def test_real_missing_cover_is_terminal_and_next_unchanged_sync_resyncs(self):
        self._write_article(cover="images/missing-cover.jpg")

        first, second, clone_count, fetch_count = self._sync_twice()

        article = Article.objects.get(slug="missing-image-fixture")
        self.assertTrue(article.image_manifest_complete)
        self.assertEqual(article.cover_image_url, "")
        self.assertEqual(first.status, "partial")
        self.assertEqual(
            self._terminal_steps(first),
            [("cover_image_missing", False)],
        )
        self.assertEqual(second.status, "partial")
        self.assertEqual(clone_count, 2)
        self.assertEqual(fetch_count, 2)

    def test_real_missing_body_image_is_terminal_and_next_unchanged_sync_resyncs(self):
        self._write_article(body="![Missing](images/missing-body.jpg)")

        first, second, clone_count, fetch_count = self._sync_twice()

        article = Article.objects.get(slug="missing-image-fixture")
        self.assertTrue(article.image_manifest_complete)
        self.assertEqual(first.status, "partial")
        self.assertEqual(
            self._terminal_steps(first),
            [("image_reference_missing", False)],
        )
        self.assertEqual(second.status, "partial")
        self.assertEqual(clone_count, 2)
        self.assertEqual(fetch_count, 2)

    def test_real_missing_cover_and_body_terminal_warnings_resync_together(self):
        self._write_article(
            cover="images/missing-cover.jpg",
            body="![Missing](images/missing-body.jpg)",
        )

        first, second, clone_count, fetch_count = self._sync_twice()

        self.assertEqual(first.status, "partial")
        self.assertCountEqual(
            self._terminal_steps(first),
            [
                ("cover_image_missing", False),
                ("image_reference_missing", False),
            ],
        )
        self.assertEqual(second.status, "partial")
        self.assertEqual(clone_count, 2)
        self.assertEqual(fetch_count, 2)

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
            self._terminal_steps(first),
            [
                ("image_reference_missing", False),
                ("article_image_variant", True),
            ],
        )
        self.assertEqual(clone_count, 2)
        self.assertEqual(fetch_count, 2)
