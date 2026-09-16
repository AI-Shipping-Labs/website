"""Fixture-backed sync coverage for the knowledge base families (#1685).

The ``wiki_pages`` and ``docs_pages`` families fill the package app
``community_base.knowledge_base`` from the content repository's top-level
``wiki/`` and ``docs/`` sections. The fixture repo mirrors the seed shape
committed on the content repository's ``wiki-docs-section`` branch: flat
wiki pages, docs pages nested through the ``parent:`` frontmatter field
with ``nav_order`` sibling positions.
"""

from community_base.knowledge_base.models import (
    SECTION_DOCS,
    SECTION_WIKI,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    KnowledgeBasePage,
)
from django.test import TestCase

from integrations.tests.sync_fixtures import make_sync_repo, sync_repo

CONTENT_REPO = 'AI-Shipping-Labs/content'

WIKI_PAGE_BODY = (
    '# About the Community Wiki\n\n'
    'The wiki is a set of standalone reference pages.\n\n'
    '## What belongs in the wiki\n\n'
    '- Reference material you keep coming back to.\n'
)
DOCS_CHILD_BODY = (
    '# Taking Courses\n\n'
    'Courses are the guided path. Each course has modules and units.\n'
)


def _kb_content_id(n):
    return f'73d341c8-0000-4000-8000-{n:012d}'


class _KnowledgeBaseSyncRepoTest(TestCase):
    """One content repo with a wiki section and a nested docs section."""

    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self,
            repo_name=CONTENT_REPO,
            prefix='kb-sync-',
        )
        self.repo.write_markdown(
            'wiki/about-the-wiki.md',
            {
                'content_id': _kb_content_id(1),
                'title': 'About the Community Wiki',
                'summary': 'What the wiki is and how to contribute.',
            },
            WIKI_PAGE_BODY,
        )
        self.repo.write_markdown(
            'wiki/ai-engineering-glossary.md',
            {
                'content_id': _kb_content_id(2),
                'title': 'AI Engineering Glossary',
            },
            '# AI Engineering Glossary\n\nRAG: retrieval-augmented generation.\n',
        )
        self.repo.write_markdown(
            'docs/getting-started.md',
            {
                'content_id': _kb_content_id(3),
                'title': 'Getting Started with AI Shipping Labs',
                'summary': 'The four steps from signup to shipping.',
                'nav_order': 0,
            },
            '# Getting Started with AI Shipping Labs\n\nWork through the pages in order.\n',
        )
        self.repo.write_markdown(
            'docs/taking-courses.md',
            {
                'content_id': _kb_content_id(4),
                'title': 'Taking Courses',
                'parent': 'getting-started',
                'nav_order': 2,
            },
            DOCS_CHILD_BODY,
        )
        self.repo.write_markdown(
            'docs/attending-workshops.md',
            {
                'content_id': _kb_content_id(5),
                'title': 'Attending Workshops',
                'parent': 'getting-started',
                'nav_order': 3,
            },
            '# Attending Workshops\n\nWorkshops are live, hands-on sessions.\n',
        )
        self.sync_log = sync_repo(self.source, self.repo)


class KnowledgeBaseSyncHappyPathTest(_KnowledgeBaseSyncRepoTest):
    def test_sync_reports_no_errors(self):
        self.assertEqual(
            self.sync_log.errors, [],
            f'Expected no errors, got: {self.sync_log.errors}',
        )

    def test_wiki_pages_created_published(self):
        pages = KnowledgeBasePage.objects.filter(section=SECTION_WIKI)
        self.assertEqual(pages.count(), 2)
        page = pages.get(slug='about-the-wiki')
        self.assertEqual(page.title, 'About the Community Wiki')
        self.assertEqual(
            page.summary, 'What the wiki is and how to contribute.',
        )
        self.assertEqual(page.status, STATUS_PUBLISHED)
        self.assertIsNone(page.parent)

    def test_docs_page_nested_under_parent(self):
        child = KnowledgeBasePage.objects.get(
            section=SECTION_DOCS, slug='taking-courses',
        )
        parent = KnowledgeBasePage.objects.get(
            section=SECTION_DOCS, slug='getting-started',
        )
        self.assertEqual(child.parent, parent)
        self.assertEqual(child.nav_order, 2)
        self.assertEqual(parent.nav_order, 0)
        self.assertIsNone(parent.parent)

    def test_page_body_rendered_to_html(self):
        page = KnowledgeBasePage.objects.get(
            section=SECTION_DOCS, slug='taking-courses',
        )
        self.assertIn('<p>', page.body_html)
        self.assertIn('modules and units', page.body_html)
        # The package strips the leading title H1 before rendering: the
        # rendered body must not carry a second copy of the page title.
        self.assertNotIn('<h1', page.body_html)

    def test_wiki_section_heading_rendered(self):
        page = KnowledgeBasePage.objects.get(
            section=SECTION_WIKI, slug='about-the-wiki',
        )
        self.assertIn('<h2', page.body_html)
        self.assertIn('What belongs in the wiki', page.body_html)

    def test_wiki_page_cannot_have_parent(self):
        self.assertIsNone(
            KnowledgeBasePage.objects.get(
                section=SECTION_WIKI, slug='about-the-wiki',
            ).parent,
        )


class KnowledgeBaseSyncIdempotenceTest(_KnowledgeBaseSyncRepoTest):
    def test_second_sync_is_unchanged(self):
        second_log = sync_repo(self.source, self.repo)
        self.assertEqual(second_log.errors, [])
        self.assertEqual(second_log.items_created, 0)
        self.assertEqual(second_log.items_updated, 0)
        kb_actions = [
            detail['action']
            for detail in second_log.items_detail
            if detail.get('content_type') in {'wiki_page', 'docs_page'}
        ]
        self.assertTrue(kb_actions, 'knowledge base items missing from detail')
        self.assertEqual(set(kb_actions), {'unchanged'})


class KnowledgeBaseSyncUpdateTest(_KnowledgeBaseSyncRepoTest):
    def test_body_change_updates_page_and_html(self):
        self.repo.write_markdown(
            'wiki/about-the-wiki.md',
            {
                'content_id': _kb_content_id(1),
                'title': 'About the Community Wiki',
                'summary': 'Updated summary.',
            },
            WIKI_PAGE_BODY + '\nNew section added later.\n',
        )
        second_log = sync_repo(self.source, self.repo)
        self.assertEqual(second_log.errors, [])
        page = KnowledgeBasePage.objects.get(
            section=SECTION_WIKI, slug='about-the-wiki',
        )
        self.assertEqual(page.summary, 'Updated summary.')
        self.assertIn('New section added later.', page.body)
        self.assertIn('New section added later.', page.body_html)


class KnowledgeBaseDeleteMissingTest(_KnowledgeBaseSyncRepoTest):
    def test_removed_page_drafted_others_untouched(self):
        self.repo.remove('docs/taking-courses.md')
        second_log = sync_repo(self.source, self.repo)
        self.assertEqual(second_log.errors, [])
        drafted = KnowledgeBasePage.objects.get(
            section=SECTION_DOCS, slug='taking-courses',
        )
        self.assertEqual(drafted.status, STATUS_DRAFT)
        self.assertEqual(
            KnowledgeBasePage.objects.get(
                section=SECTION_WIKI, slug='about-the-wiki',
            ).status,
            STATUS_PUBLISHED,
        )
        self.assertEqual(
            KnowledgeBasePage.objects.get(
                section=SECTION_DOCS, slug='attending-workshops',
            ).status,
            STATUS_PUBLISHED,
        )


class KnowledgeBaseBrokenParentTest(_KnowledgeBaseSyncRepoTest):
    """A docs page whose parent reference never resolves is a bounded error."""

    def test_unknown_parent_reported_and_page_skipped(self):
        self.repo.write_markdown(
            'docs/orphaned-page.md',
            {
                'content_id': _kb_content_id(6),
                'title': 'Orphaned Page',
                'parent': 'does-not-exist',
            },
            '# Orphaned Page\n\nNo parent by this name.\n',
        )
        second_log = sync_repo(self.source, self.repo)
        self.assertTrue(
            second_log.errors,
            'an unknown parent reference must surface as a sync error',
        )
        self.assertFalse(
            KnowledgeBasePage.objects.filter(
                section=SECTION_DOCS, slug='orphaned-page',
            ).exists(),
        )
        # The good pages still sync: the failure is bounded, not fatal.
        self.assertEqual(
            KnowledgeBasePage.objects.get(
                section=SECTION_DOCS, slug='taking-courses',
            ).status,
            STATUS_PUBLISHED,
        )

    def test_wiki_parent_reference_refused(self):
        self.repo.write_markdown(
            'wiki/parented-page.md',
            {
                'content_id': _kb_content_id(7),
                'title': 'Parented Page',
                'parent': 'about-the-wiki',
            },
            '# Parented Page\n\nWiki pages are flat.\n',
        )
        second_log = sync_repo(self.source, self.repo)
        self.assertTrue(second_log.errors)
        self.assertFalse(
            KnowledgeBasePage.objects.filter(
                section=SECTION_WIKI, slug='parented-page',
            ).exists(),
        )


class KnowledgeBaseNavAvailabilityRefreshTest(_KnowledgeBaseSyncRepoTest):
    """The cached nav flags follow the published pages (issue #1685)."""

    def test_flags_true_after_sync(self):
        from content.nav_availability import has_published_kb_pages_for_nav

        self.assertTrue(has_published_kb_pages_for_nav('wiki'))
        self.assertTrue(has_published_kb_pages_for_nav('docs'))

    def test_flags_false_after_delete_missing(self):
        from content.nav_availability import has_published_kb_pages_for_nav

        self.repo.remove('wiki/about-the-wiki.md')
        self.repo.remove('wiki/ai-engineering-glossary.md')
        sync_repo(self.source, self.repo)
        self.assertFalse(has_published_kb_pages_for_nav('wiki'))
        self.assertTrue(has_published_kb_pages_for_nav('docs'))
