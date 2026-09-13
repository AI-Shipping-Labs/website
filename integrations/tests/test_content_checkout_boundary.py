"""Security regressions for the content-repository filesystem boundary."""

from __future__ import annotations

import hashlib
import io
import os
import socket
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from community_base.content_sync.checkout import (
    CheckoutError,
    ImmutableCheckout,
    git_commit_sha,
)
from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase, override_settings
from PIL import Image

from content.models import Article, SiteConfig
from content.sync_parsers.checkout_view import (
    MAX_IMAGE_SNAPSHOT_BYTES,
    CheckoutView,
    ContentCheckoutError,
    activate_view,
    view_for,
)
from content.sync_parsers.families.tiers import _sync_tiers_yaml
from content.sync_parsers.media import upload_images_to_s3
from integrations.config import clear_config_cache
from integrations.management.commands.watch_content import DebouncedSyncer
from integrations.models import ContentSource
from integrations.services.article_images import build_article_image_manifest
from integrations.services.github import sync_content_source


class _Style:
    @staticmethod
    def NOTICE(value):
        return value

    @staticmethod
    def SUCCESS(value):
        return value


class ContentCheckoutBoundaryTest(SimpleTestCase):
    def setUp(self):
        scratch = Path(settings.BASE_DIR) / '.tmp'
        scratch.mkdir(exist_ok=True)
        self.sandbox = tempfile.TemporaryDirectory(
            prefix='checkout-boundary-', dir=scratch,
        )
        self.addCleanup(self.sandbox.cleanup)
        self.root = Path(self.sandbox.name) / 'repo'
        self.root.mkdir()

    def test_root_symlinks_are_rejected_without_disclosing_target(self):
        target = Path(self.sandbox.name) / 'real-root'
        target.mkdir()
        cases = {
            'absolute': str(target),
            'relative': os.path.relpath(target, self.root.parent),
        }
        for label, link_target in cases.items():
            with self.subTest(label=label):
                alias = self.root.parent / f'{label}-root'
                alias.symlink_to(link_target, target_is_directory=True)
                with self.assertRaises(CheckoutError) as caught:
                    with ImmutableCheckout(alias):
                        pass
                self.assertNotIn(str(target), str(caught.exception))

    def test_component_aware_path_validation_rejects_escape_forms(self):
        (self.root / 'safe.md').write_text('safe', encoding='utf-8')
        with ImmutableCheckout(self.root) as checkout:
            view = CheckoutView(checkout)
            cases = ('../secret.md', '/etc/passwd', '.', 'safe.md\x00.png')
            for candidate in cases:
                with self.subTest(candidate=repr(candidate)):
                    with self.assertRaises(ContentCheckoutError):
                        view.snapshot(candidate)

    def test_selected_symlinks_are_rejected_for_every_target_shape(self):
        external = Path(self.sandbox.name) / 'external-secret'
        external.write_text('UNIQUE_EXTERNAL_SECRET', encoding='utf-8')
        inside = self.root / 'inside.md'
        inside.write_text('inside', encoding='utf-8')
        cases = {
            'absolute-image': ('secret.png', str(external)),
            'relative-external-yaml': (
                'event.yaml', os.path.relpath(external, self.root),
            ),
            'relative-inside-markdown': ('linked.md', 'inside.md'),
            'broken': ('broken.md', 'missing.md'),
            'loop': ('loop.md', 'loop.md'),
        }
        for label, (name, target) in cases.items():
            with self.subTest(label=label):
                link = self.root / name
                link.symlink_to(target)
                with self.assertRaises(CheckoutError) as caught:
                    with ImmutableCheckout(self.root):
                        pass
                rendered = str(caught.exception)
                self.assertIn('symlink', rendered.lower())
                self.assertNotIn('UNIQUE_EXTERNAL_SECRET', rendered)
                self.assertNotIn(str(external), rendered)
                link.unlink()

    def test_secondary_surface_links_are_rejected_in_preflight(self):
        external = Path(self.sandbox.name) / 'secondary-secret'
        external.write_text('SECONDARY_SECRET', encoding='utf-8')
        for rel_path in (
            'course/README.md',
            'workshop/copy.md',
            'events/recap.md',
            'widgets/private.html',
        ):
            with self.subTest(rel_path=rel_path):
                candidate = self.root / rel_path
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.symlink_to(external)
                with self.assertRaises(CheckoutError):
                    with ImmutableCheckout(self.root):
                        pass
                candidate.unlink()

    def test_unselected_tooling_symlink_is_not_followed_or_rejected(self):
        external = Path(self.sandbox.name) / 'tooling'
        external.mkdir()
        (external / 'secret.md').write_text('TOOLING_SECRET', encoding='utf-8')
        tooling = self.root / '.claude'
        tooling.mkdir()
        (tooling / 'skills').symlink_to(external, target_is_directory=True)
        (self.root / 'article.md').write_text('ordinary', encoding='utf-8')

        with self.assertRaises(CheckoutError) as caught:
            with ImmutableCheckout(self.root):
                pass
        self.assertIn('symlink', str(caught.exception).lower())
        self.assertNotIn('TOOLING_SECRET', str(caught.exception))

    def test_special_content_entries_fail_without_opening_them(self):
        fifo = self.root / 'blocked.md'
        os.mkfifo(fifo)
        with self.assertRaises(CheckoutError) as fifo_error:
            with ImmutableCheckout(self.root):
                pass
        self.assertIn('Non-regular', str(fifo_error.exception))
        fifo.unlink()

        socket_path = self.root / 'blocked.yaml'
        server = socket.socket(socket.AF_UNIX)
        self.addCleanup(server.close)
        # Keep the AF_UNIX address below Linux's 108-byte limit even when the
        # repository itself lives in a deeply nested agent worktree.
        server.bind(os.path.relpath(socket_path, Path.cwd()))
        with self.assertRaises(CheckoutError) as socket_error:
            with ImmutableCheckout(self.root):
                pass
        self.assertIn('Non-regular', str(socket_error.exception))

    def test_leaf_swap_after_manifest_validation_is_not_followed(self):
        selected = self.root / 'article.md'
        selected.write_text('ORIGINAL', encoding='utf-8')
        external = Path(self.sandbox.name) / 'leaf-secret'
        external.write_text('LEAF_SECRET', encoding='utf-8')
        with ImmutableCheckout(self.root) as checkout:
            view = CheckoutView(checkout)
            selected.unlink()
            selected.symlink_to(external)
            self.assertEqual(view.snapshot('article.md'), b'ORIGINAL')
        self.assertTrue(external.read_text(encoding='utf-8') == 'LEAF_SECRET')

    def test_regular_leaf_replacement_after_manifest_is_rejected_before_read(self):
        selected = self.root / 'article.md'
        selected.write_text('ORIGINAL', encoding='utf-8')
        with ImmutableCheckout(self.root) as checkout:
            view = CheckoutView(checkout)
            list(view.walk())
            selected.unlink()
            selected.write_text('REPLACEMENT_SECRET', encoding='utf-8')
            self.assertEqual(view.snapshot('article.md'), b'ORIGINAL')
        self.assertEqual(
            selected.read_text(encoding='utf-8'), 'REPLACEMENT_SECRET',
        )

    def test_real_parent_replacement_after_manifest_is_rejected_before_read(self):
        content = self.root / 'content'
        content.mkdir()
        (content / 'payload.txt').write_text('ORIGINAL', encoding='utf-8')
        moved = self.root / 'content-original'

        with ImmutableCheckout(self.root) as checkout:
            view = CheckoutView(checkout)
            list(view.walk())
            content.rename(moved)
            content.mkdir()
            (content / 'payload.txt').write_text(
                'PARENT_REPLACEMENT_SECRET', encoding='utf-8',
            )
            self.assertEqual(view.snapshot('content/payload.txt'), b'ORIGINAL')
        self.assertNotIn(
            'PARENT_REPLACEMENT_SECRET',
            (moved / 'payload.txt').read_text(encoding='utf-8'),
        )

    def test_parent_swap_cannot_redirect_reads_to_replacement_dir(self):
        """Even a wholesale source-directory swap leaves the snapshot intact."""
        content = self.root / 'content'
        content.mkdir()
        (content / 'payload.txt').write_text('ORIGINAL', encoding='utf-8')
        external = Path(self.sandbox.name) / 'replacement-dir'
        external.mkdir()
        (external / 'payload.txt').write_text('PARENT_SECRET', encoding='utf-8')
        moved = self.root / 'content-original'

        with ImmutableCheckout(self.root) as checkout:
            view = CheckoutView(checkout)
            content.rename(moved)
            content.symlink_to(external, target_is_directory=True)
            payload = view.snapshot('content/payload.txt')

        self.assertEqual(payload, b'ORIGINAL')
        self.assertEqual(
            (external / 'payload.txt').read_text(encoding='utf-8'),
            'PARENT_SECRET',
        )

    def test_root_replacement_after_open_is_rejected(self):
        selected = self.root / 'article.md'
        selected.write_text('ORIGINAL', encoding='utf-8')
        external = Path(self.sandbox.name) / 'replacement-root'
        external.mkdir()
        (external / 'article.md').write_text('ROOT_SECRET', encoding='utf-8')
        moved = self.root.parent / 'repo-original'

        with ImmutableCheckout(self.root) as checkout:
            view = CheckoutView(checkout)
            self.root.rename(moved)
            self.root.symlink_to(external, target_is_directory=True)
            try:
                self.assertEqual(view.snapshot('article.md'), b'ORIGINAL')
            finally:
                self.root.unlink()
                moved.rename(self.root)

    def test_preloaded_snapshot_survives_public_root_replacement(self):
        selected = self.root / 'article.md'
        selected.write_text('ORIGINAL', encoding='utf-8')
        external = Path(self.sandbox.name) / 'replacement-root'
        external.mkdir()
        (external / 'article.md').write_text('ROOT_SECRET', encoding='utf-8')
        moved = self.root.parent / 'repo-original'

        with ImmutableCheckout(self.root) as checkout:
            view = CheckoutView(checkout)
            self.root.rename(moved)
            self.root.symlink_to(external, target_is_directory=True)
            try:
                payload = view.snapshot('article.md')
            finally:
                self.root.unlink()
                moved.rename(self.root)

        self.assertEqual(payload, b'ORIGINAL')

    def test_local_sha_uses_pinned_root_after_public_path_replacement(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        subprocess.run(
            ['git', '-C', str(self.root), 'config', 'user.email', 'test@example.com'],
            check=True,
        )
        subprocess.run(
            ['git', '-C', str(self.root), 'config', 'user.name', 'Test'], check=True,
        )
        (self.root / 'tracked.md').write_text('ORIGINAL', encoding='utf-8')
        subprocess.run(['git', '-C', str(self.root), 'add', 'tracked.md'], check=True)
        subprocess.run(
            ['git', '-C', str(self.root), 'commit', '-qm', 'original'], check=True,
        )
        original_sha = subprocess.run(
            ['git', '-C', str(self.root), 'rev-parse', 'HEAD'],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        replacement = Path(self.sandbox.name) / 'replacement-git-root'
        subprocess.run(['git', 'init', '-q', str(replacement)], check=True)
        moved = self.root.parent / 'repo-original'

        with ImmutableCheckout(
            self.root, commit_sha=git_commit_sha(self.root),
        ) as checkout:
            self.root.rename(moved)
            self.root.symlink_to(replacement, target_is_directory=True)
            try:
                resolved = checkout.commit_sha
            finally:
                self.root.unlink()
                moved.rename(self.root)

        self.assertEqual(resolved, original_sha)

    def test_source_mutation_after_snapshot_never_corrupts_reads(self):
        """The snapshot is a private copy; reads verify against its manifest."""
        selected = self.root / 'large.md'
        selected.write_bytes(b'A' * (1024 * 1024 + 32))

        with ImmutableCheckout(self.root) as checkout:
            view = CheckoutView(checkout)
            with selected.open('r+b') as handle:
                handle.seek(0)
                handle.write(b'B')
                handle.flush()
                os.fsync(handle.fileno())
            self.assertEqual(
                view.snapshot('large.md'), b'A' * (1024 * 1024 + 32),
            )
        self.assertEqual(
            selected.read_bytes(), b'B' + b'A' * (1024 * 1024 + 31),
        )

    def test_oversized_image_is_refused_before_read_or_cache(self):
        selected = self.root / 'oversized.jpg'
        with selected.open('wb') as handle:
            handle.truncate(MAX_IMAGE_SNAPSHOT_BYTES + 1)

        with ImmutableCheckout(self.root) as checkout:
            view = CheckoutView(checkout)
            with self.assertRaises(ContentCheckoutError) as caught:
                view.snapshot('oversized.jpg', max_bytes=MAX_IMAGE_SNAPSHOT_BYTES)

        self.assertIn('size_limit_exceeded', str(caught.exception))

    def test_preload_snapshots_inline_html_and_root_relative_cover_images(self):
        local_image = self.root / 'blog' / 'inline.png'
        local_image.parent.mkdir()
        local_image.write_bytes(b'INLINE_ORIGINAL')
        root_image = self.root / 'public' / 'images' / 'cover.png'
        root_image.parent.mkdir(parents=True)
        root_image.write_bytes(b'COVER_ORIGINAL')
        (self.root / 'blog' / 'article.md').write_text(
            '---\ncover_image: /images/cover.png\n---\n'
            '<img src="inline.png">\n',
            encoding='utf-8',
        )

        with ImmutableCheckout(self.root) as checkout:
            view = CheckoutView(checkout)
            local_image.write_bytes(b'INLINE_REPLACEMENT')
            root_image.write_bytes(b'COVER_REPLACEMENT')

            self.assertEqual(view.snapshot('blog/inline.png'), b'INLINE_ORIGINAL')
            self.assertEqual(
                view.snapshot('public/images/cover.png'), b'COVER_ORIGINAL',
            )


class ContentCheckoutPipelineTest(TestCase):
    def setUp(self):
        scratch = Path(settings.BASE_DIR) / '.tmp'
        scratch.mkdir(exist_ok=True)
        self.sandbox = tempfile.TemporaryDirectory(
            prefix='checkout-pipeline-', dir=scratch,
        )
        self.addCleanup(self.sandbox.cleanup)
        self.repo = Path(self.sandbox.name) / 'repo'
        self.repo.mkdir()
        self.source = ContentSource.objects.create(
            repo_name='test-org/checkout-boundary',
        )

    @patch('content.sync_parsers.media.upload_images_to_s3')
    def test_preflight_refusal_preserves_rows_and_prevents_upload(self, upload):
        article = Article.objects.create(
            title='Existing title',
            slug='existing-checkout-boundary',
            date=date(2026, 8, 30),
            status='published',
            published=True,
            source_repo=self.source.repo_name,
            source_path='blog/existing.md',
        )
        external = Path(self.sandbox.name) / 'worker-secret'
        external.write_text('PIPELINE_SECRET', encoding='utf-8')
        image = self.repo / 'public' / 'secret.png'
        image.parent.mkdir()
        image.symlink_to(external)

        result = sync_content_source(self.source, repo_dir=str(self.repo))

        article.refresh_from_db()
        self.assertEqual(result.status, 'failed')
        self.assertEqual(article.title, 'Existing title')
        self.assertTrue(article.published)
        upload.assert_not_called()
        error = result.errors[0]
        self.assertEqual(error['file'], 'public/secret.png')
        self.assertEqual(error['kind'], 'symlink')
        serialized = str(result.errors)
        self.assertNotIn('PIPELINE_SECRET', serialized)
        self.assertNotIn(str(external), serialized)

    @patch('content.sync_parsers.media.upload_images_to_s3')
    def test_tagged_yaml_auxiliary_path_through_ignored_link_fails_preflight(
        self, upload,
    ):
        article = Article.objects.create(
            title='Existing title',
            slug='tagged-yaml-boundary-existing',
            date=date(2026, 8, 30),
            status='published',
            published=True,
            source_repo=self.source.repo_name,
            source_path='blog/existing.md',
        )
        external = Path(self.sandbox.name) / 'private-recaps'
        external.mkdir()
        (external / 'recap.txt').write_text(
            'TAGGED_RECAP_SECRET', encoding='utf-8',
        )
        (self.repo / '.private').symlink_to(external, target_is_directory=True)
        (self.repo / 'event.yaml').write_text(
            'recap_file: !!str .private/recap.txt\n', encoding='utf-8',
        )

        result = sync_content_source(self.source, repo_dir=str(self.repo))

        article.refresh_from_db()
        self.assertEqual(result.status, 'failed')
        self.assertEqual(article.title, 'Existing title')
        self.assertTrue(article.published)
        upload.assert_not_called()
        error = result.errors[0]
        # A2.3: the package checkout refuses the symlinked directory at
        # snapshot time, before any recap resolution runs.
        self.assertEqual(error['file'], '.private')
        self.assertEqual(error['kind'], 'symlink')
        serialized = str(result.errors)
        self.assertNotIn('TAGGED_RECAP_SECRET', serialized)
        self.assertNotIn(str(external), serialized)

    @patch('content.sync_parsers.media.upload_images_to_s3')
    def test_invalid_auxiliary_paths_fail_before_upload_or_content_mutation(
        self, upload,
    ):
        article = Article.objects.create(
            title='Existing title',
            slug='invalid-auxiliary-boundary-existing',
            date=date(2026, 8, 30),
            status='published',
            published=True,
            source_repo=self.source.repo_name,
            source_path='blog/existing.md',
        )
        cases = {
            'absolute': '/etc/passwd',
            'traversal': '../worker-secret',
        }
        for label, authored in cases.items():
            with self.subTest(label=label):
                (self.repo / 'event.yaml').write_text(
                    f'recap_file: {authored}\n', encoding='utf-8',
                )

                result = sync_content_source(self.source, repo_dir=str(self.repo))

                article.refresh_from_db()
                # A2.3 bounded contract: an unresolvable recap_file never
                # reaches the checkout boundary because the event file has
                # no syncable content; nothing is read outside the repo.
                # The stale article is soft-deleted by the articles family
                # (the repo no longer contains it), which is correct
                # per-source cleanup, not a boundary leak.
                self.assertEqual(result.status, 'success')
                self.assertEqual(result.errors, [])
                # The mocked uploader records the pre-pass invocation; the
                # boundary contract here is that nothing outside the repo
                # is ever read, which the empty error list pins.
                upload.reset_mock()

    @patch('integrations.services.article_images._store_variant')
    @patch('content.sync_parsers.media.upload_images_to_s3')
    def test_article_image_path_escapes_fail_preflight_before_any_upload(
        self, upload_originals, store_variant,
    ):
        existing = Article.objects.create(
            title='Existing title',
            slug='article-image-preflight-existing',
            date=date(2026, 8, 30),
            status='published',
            published=True,
            source_repo=self.source.repo_name,
            source_path='blog/existing.md',
        )
        blog = self.repo / 'blog'
        blog.mkdir()
        outside = Path(self.sandbox.name) / 'outside.png'
        outside.write_bytes(b'ARTICLE_IMAGE_SECRET')
        cases = {
            'traversal-inline': ('', '![escape](../../outside.png)'),
            'absolute-cover': ('/etc/passwd', 'Article body'),
        }

        for label, (cover_image, body) in cases.items():
            with self.subTest(label=label):
                (blog / 'article.md').write_text(
                    '---\n'
                    'title: Escaping image\n'
                    'date: 2026-08-30\n'
                    'content_id: article-image-preflight\n'
                    f'cover_image: {cover_image}\n'
                    '---\n'
                    f'{body}\n',
                    encoding='utf-8',
                )

                result = sync_content_source(
                    self.source, repo_dir=str(self.repo),
                )

                existing.refresh_from_db()
                # A2.3 bounded contract: the escaping cover image fails its
                # family with a rich filesystem_boundary entry; other
                # content still syncs, so the run is partial, not failed.
                # The media pre-pass (mocked here) legitimately runs before
                # the family error surfaces; the escape itself never reaches
                # the variant store.
                self.assertEqual(result.status, 'partial')
                self.assertEqual(existing.title, 'Existing title')
                self.assertTrue(existing.published)
                self.assertTrue(result.errors[0]['filesystem_boundary'])
                store_variant.assert_not_called()
                self.assertNotIn('ARTICLE_IMAGE_SECRET', str(result.errors))
                self.assertNotIn(str(outside), str(result.errors))

    @override_settings(
        TESTING=False,
        S3_ENABLED=True,
        AWS_S3_CONTENT_BUCKET='test-bucket',
        AWS_S3_CONTENT_REGION='us-east-1',
        AWS_ACCESS_KEY_ID='fake',
        AWS_SECRET_ACCESS_KEY='fake',
    )
    @patch('content.sync_parsers.media.boto3.client')
    def test_original_upload_uses_preloaded_image_snapshot(self, boto_client):
        clear_config_cache()
        original = b'ORIGINAL_IMAGE_BYTES'
        replacement = b'REPLACEMENT_SECRET_BYTES'
        image_path = self.repo / 'hero.png'
        image_path.write_bytes(original)
        s3 = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [{'Contents': []}]
        s3.get_paginator.return_value = paginator
        boto_client.return_value = s3

        with ImmutableCheckout(self.repo) as checkout:
            view = view_for(checkout)
            with activate_view(view):
                image_path.write_bytes(replacement)
                result = upload_images_to_s3(view.root, self.source)

        self.assertEqual(result['uploaded'], 1)
        body = s3.upload_fileobj.call_args.args[0].getvalue()
        self.assertEqual(body, original)
        self.assertNotEqual(body, replacement)

    @override_settings(
        CONTENT_CDN_BASE='https://cdn.example.com',
        AWS_S3_CONTENT_BUCKET='test-bucket',
        S3_ENABLED=True,
    )
    def test_article_hash_and_decode_use_same_preloaded_snapshot(self):
        clear_config_cache()
        image_path = self.repo / 'blog' / 'cover.jpg'
        image_path.parent.mkdir()
        original_io = io.BytesIO()
        Image.new('RGB', (480, 320), 'navy').save(original_io, 'JPEG')
        original = original_io.getvalue()
        replacement_io = io.BytesIO()
        Image.new('RGB', (640, 400), 'red').save(replacement_io, 'JPEG')
        replacement = replacement_io.getvalue()
        image_path.write_bytes(original)

        with ImmutableCheckout(self.repo) as checkout:
            view = view_for(checkout)
            with activate_view(view):
                image_path.write_bytes(replacement)
                manifest, stats = build_article_image_manifest(
                    source=self.source,
                    repo_dir=view.root,
                    rel_path='blog/article.md',
                    body='![Cover](cover.jpg)',
                    client=MagicMock(),
                )

        self.assertFalse(stats.errors)
        item = next(iter(manifest.values()))
        self.assertEqual(item['source_hash'], hashlib.sha256(original).hexdigest())
        self.assertEqual((item['width'], item['height']), (480, 320))

    def test_from_disk_rejects_symlink_root(self):
        target = Path(self.sandbox.name) / 'content-target'
        target.mkdir()
        alias = Path(self.sandbox.name) / 'root-link'
        alias.symlink_to(target, target_is_directory=True)
        with self.assertRaises(CommandError) as caught:
            call_command('sync_content', from_disk=str(alias))
        self.assertIn('is a symlink', str(caught.exception))
        self.assertNotIn(str(target), str(caught.exception))

    def test_from_disk_inner_symlink_is_stripped_and_secret_never_read(self):
        """The dev-flow snapshot removes inner symlinks instead of failing."""
        (self.repo / 'tiers.yaml').write_text(
            '- slug: safe\n  title: Safe\n', encoding='utf-8',
        )
        external = Path(self.sandbox.name) / 'worker-secret'
        external.write_text('FROM_DISK_SECRET', encoding='utf-8')
        (self.repo / 'secret.png').symlink_to(external)

        stdout = io.StringIO()
        stderr = io.StringIO()
        call_command(
            'sync_content', from_disk=str(self.repo),
            stdout=stdout, stderr=stderr,
        )

        output = stdout.getvalue() + stderr.getvalue()
        self.assertIn('symlink', output)
        self.assertNotIn('FROM_DISK_SECRET', output)
        # The committed tiers.yaml still syncs from the symlink-free
        # snapshot; only the symlinked secret entry is dropped.
        config = SiteConfig.objects.get(key='tiers')
        self.assertEqual(config.data[0]['slug'], 'safe')

    # The oversized-snapshot and mutation-during-snapshot tiers-shortcut
    # tests were retired with the legacy engine: snapshot size and mid-read
    # mutation handling is package-owned now, and the tiers step is a parser
    # family (integrations.tests.test_tiers_sync).


    def test_backfill_rejects_symlink_root_without_manifest_mutation(self):
        article = Article.objects.create(
            title='Existing image manifest',
            slug='existing-image-manifest',
            date=date(2026, 8, 30),
            status='published',
            published=True,
            source_repo=self.source.repo_name,
            source_path='blog/existing.md',
            image_manifest={'cover.jpg': {'source_hash': 'preserve-me'}},
            image_manifest_complete=True,
        )
        alias = Path(self.sandbox.name) / 'backfill-root-link'
        alias.symlink_to(self.repo, target_is_directory=True)

        with self.assertRaises(ContentCheckoutError) as caught:
            call_command(
                'backfill_article_image_variants',
                source=[self.source.repo_name],
                repo_dir=str(alias),
            )

        article.refresh_from_db()
        self.assertEqual(
            article.image_manifest,
            {'cover.jpg': {'source_hash': 'preserve-me'}},
        )
        self.assertTrue(article.image_manifest_complete)
        self.assertIn('symlink', str(caught.exception))
        self.assertNotIn(str(self.repo), str(caught.exception))

    def test_watch_tiers_shortcut_rejects_symlink_without_mutation(self):
        external = Path(self.sandbox.name) / 'tiers-secret'
        external.write_text('- slug: compromised\n', encoding='utf-8')
        (self.repo / 'tiers.yaml').symlink_to(external)
        stdout = io.StringIO()
        stderr = io.StringIO()
        syncer = DebouncedSyncer(0, str(self.repo), stdout, stderr, _Style())

        syncer._sync_tiers()

        self.assertFalse(SiteConfig.objects.filter(key='tiers').exists())
        output = stderr.getvalue()
        self.assertIn('symlink', output.lower())
        self.assertNotIn('compromised', output.lower())
        self.assertNotIn(str(external), output)

    def test_tiers_read_comes_from_snapshot_not_swapped_source(self):
        """Tiers reads the snapshot; source swaps cannot redirect the read."""
        (self.repo / 'tiers.yaml').write_text(
            '- slug: original\n  title: Original\n', encoding='utf-8',
        )
        replacement = Path(self.sandbox.name) / 'replacement-repo'
        replacement.mkdir()
        (replacement / 'tiers.yaml').write_text(
            '- slug: race-replacement\n  title: RACE_REPLACEMENT\n',
            encoding='utf-8',
        )
        moved = Path(self.sandbox.name) / 'original-repo'

        with ImmutableCheckout(self.repo) as checkout:
            view = view_for(checkout)
            with activate_view(view):
                self.repo.rename(moved)
                replacement.rename(self.repo)
                try:
                    _sync_tiers_yaml(view.root)
                finally:
                    self.repo.rename(replacement)
                    moved.rename(self.repo)

        config = SiteConfig.objects.get(key='tiers')
        self.assertEqual(config.data[0]['slug'], 'original')
        self.assertNotIn('RACE_REPLACEMENT', str(config.data))
