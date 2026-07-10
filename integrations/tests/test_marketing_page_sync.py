import os
import shutil
import tempfile
import uuid

from django.test import TestCase

from content.models import MarketingPage
from integrations.models import ContentSource
from integrations.services.github import sync_content_source
from integrations.tests.sync_fixtures import write_markdown_file


class MarketingPageSyncTest(TestCase):
    def setUp(self):
        self.source = ContentSource.objects.create(repo_name='AI-Shipping-Labs/content')
        self.repo_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.repo_dir, ignore_errors=True)

    def _write_page(self, filename='pages/launch-recap.md', **metadata):
        content_id = metadata.pop('content_id', str(uuid.uuid4()))
        data = {
            'content_type': 'marketing_page',
            'content_id': content_id,
            'title': 'Launch Recap',
            'public_path': '/launch-recap',
            'status': 'published',
            'description': 'Launch recap description.',
            'meta_description': 'Launch recap search text.',
            'tags': ['launch'],
            'cover_image_url': '',
            'show_in_sitemap': True,
            'nav_section': 'resources',
            'nav_label': 'Launch Recap',
            'nav_order': 5,
            **metadata,
        }
        path = os.path.join(self.repo_dir, filename)
        write_markdown_file(path, data, '## Body\n\nLaunch body.')
        return content_id

    def test_sync_upserts_marketing_page_and_renders_markdown(self):
        content_id = self._write_page()

        log = sync_content_source(self.source, repo_dir=self.repo_dir)

        self.assertEqual(log.items_created, 1)
        page = MarketingPage.objects.get(content_id=content_id)
        self.assertEqual(page.public_path, '/launch-recap')
        self.assertEqual(page.source_repo, 'AI-Shipping-Labs/content')
        self.assertEqual(page.source_path, 'pages/launch-recap.md')
        self.assertIn('<h2>Body</h2>', page.content_html)
        self.assertEqual(page.nav_section, 'resources')

    def test_sync_marks_removed_synced_page_as_draft_without_deleting(self):
        content_id = self._write_page()
        sync_content_source(self.source, repo_dir=self.repo_dir)
        os.remove(os.path.join(self.repo_dir, 'pages/launch-recap.md'))

        log = sync_content_source(self.source, repo_dir=self.repo_dir)

        page = MarketingPage.objects.get(content_id=content_id)
        self.assertEqual(page.status, 'draft')
        self.assertEqual(log.items_deleted, 1)

    def test_sync_rejects_reserved_public_path(self):
        self._write_page(public_path='/events')

        log = sync_content_source(self.source, repo_dir=self.repo_dir)

        self.assertFalse(MarketingPage.objects.exists())
        self.assertTrue(any('conflicts with an existing route' in err['error'] for err in log.errors))
