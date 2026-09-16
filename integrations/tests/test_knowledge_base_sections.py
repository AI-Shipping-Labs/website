"""Section classification and nav-flag refresh for the KB families (#1685).

Rendering and sync behavior have their authoritative coverage in
``content/tests/test_knowledge_base_*.py``; these tests pin the two
behaviors that live in the integrations layer: the classifier anchors the
``wiki/`` and ``docs/`` sections to the checkout top level, and a family
sync refreshes the cached Wiki/Docs nav availability flags.
"""

import os
import shutil
import tempfile

from community_base.knowledge_base.models import KnowledgeBasePage
from django.test import TestCase

from content.nav_availability import (
    has_published_kb_pages_for_nav,
    refresh_knowledge_base_nav_cache,
)
from integrations.models import ContentSource
from integrations.services.github import sync_content_source
from integrations.tests.sync_fixtures import write_markdown_file


def _kb_content_id(n):
    """Stable fake content_id: the content repo requires one per file."""
    return f'73d341c8-0000-4000-8000-{n:012d}'


class KnowledgeBaseSectionsSyncTest(TestCase):
    def setUp(self):
        self.source = ContentSource.objects.create(repo_name='AI-Shipping-Labs/content')
        self.repo_dir = tempfile.mkdtemp()
        refresh_knowledge_base_nav_cache()

    def tearDown(self):
        shutil.rmtree(self.repo_dir, ignore_errors=True)

    def test_kb_dirs_are_anchored_to_the_checkout_top_level(self):
        # A "wiki" folder inside blog/ is article material, not the KB.
        write_markdown_file(
            os.path.join(self.repo_dir, 'wiki/real.md'),
            {'content_id': _kb_content_id(1), 'title': 'Real Wiki Page'},
        )
        write_markdown_file(
            os.path.join(self.repo_dir, 'blog/wiki/inner.md'),
            {'content_id': _kb_content_id(2), 'title': 'A Blog Note',
             'date': '2026-09-16'},
        )

        sync_content_source(self.source, repo_dir=self.repo_dir)

        self.assertTrue(
            KnowledgeBasePage.objects.filter(section='wiki', slug='real').exists(),
        )
        self.assertFalse(
            KnowledgeBasePage.objects.filter(section='wiki', slug='inner').exists(),
        )

    def test_sync_refreshes_nav_availability_flags(self):
        self.assertFalse(has_published_kb_pages_for_nav('wiki'))
        self.assertFalse(has_published_kb_pages_for_nav('docs'))

        write_markdown_file(
            os.path.join(self.repo_dir, 'wiki/glossary.md'),
            {'content_id': _kb_content_id(3), 'title': 'Glossary'},
        )
        sync_content_source(self.source, repo_dir=self.repo_dir)

        self.assertTrue(has_published_kb_pages_for_nav('wiki'))
        self.assertFalse(has_published_kb_pages_for_nav('docs'))
