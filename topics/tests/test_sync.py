"""Fixture-backed sync coverage for the member wiki family (#1688).

The ``wiki_topics`` family fills the site-owned ``topics`` app from the
private wiki repository's top-level ``_wiki/`` section. The fixture repo
mirrors the real repository shape: flat topic pages with ``layout`` /
``title`` / ``summary`` / ``related`` frontmatter, an ``index.md`` hub,
and the record directories that must reach no parser family.
"""

from community_base.knowledge_base.models import (
    KnowledgeBasePage,
)
from django.test import TestCase

from content.access import LEVEL_BASIC
from content.nav_availability import has_published_topics_for_nav
from integrations.tests.sync_fixtures import make_sync_repo, sync_repo
from topics.models import STATUS_DRAFT, STATUS_PUBLISHED, TopicPage

WIKI_REPO = 'AI-Shipping-Labs/wiki'

INDEX_BODY = (
    'Start with the role, follow the roadmap, or jump straight to a topic.\n\n'
    '## Start here\n\n'
    '- [RAG](rag.md) - the classic pipeline\n'
    '- [Agents](agents.md) - the loop\n'
    '- Canonical: [AI Hero](https://aishippinglabs.com/courses/aihero)\n'
)
RAG_BODY = (
    'RAG answers questions from documents the model was never trained on.\n'
)
AGENTS_BODY = 'Agents are the loop, the frameworks, durable execution.\n'


class _MemberWikiSyncRepoTest(TestCase):
    """One wiki repo checkout with a _wiki section and record directories."""

    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self,
            repo_name=WIKI_REPO,
            prefix='member-wiki-',
        )
        self.repo.write_markdown(
            '_wiki/index.md',
            {
                'layout': 'wiki_home',
                'title': 'AISL Wiki',
                'summary': 'Topic guides built from every AISL course, workshop, and article.',
            },
            INDEX_BODY,
            ensure_content_id=False,
        )
        self.repo.write_markdown(
            '_wiki/rag.md',
            {
                'layout': 'wiki',
                'title': 'RAG',
                'summary': 'Retrieval-augmented generation across the AISL material.',
                'related': ['agents', 'vector-search'],
                'topics': ['rag'],
            },
            RAG_BODY,
            ensure_content_id=False,
        )
        self.repo.write_markdown(
            '_wiki/agents.md',
            {
                'layout': 'wiki',
                'title': 'Agents',
                'summary': 'The loop, the frameworks, durable execution.',
                'related': ['rag'],
            },
            AGENTS_BODY,
            ensure_content_id=False,
        )
        # The record directories: the repository's internal agent index.
        # These mirror the real files closely enough to prove the point:
        # with `date` frontmatter they classify as articles, with
        # `difficulty` as projects, unless the classifier claims them.
        self.repo.write_markdown(
            '_workshops/2026-05-04-agentic-rag.md',
            {
                'layout': 'workshop_record',
                'title': 'Agentic RAG',
                'slug': '2026-05-04-agentic-rag',
                'date': '2026-05-04',
            },
            '## Source\n\nA workshop record, not a site article.\n',
            ensure_content_id=False,
        )
        self.repo.write_markdown(
            '_projects/cybersecurity-disclosure-agent.md',
            {
                'layout': 'project_record',
                'title': 'Cybersecurity Disclosure Agent',
                'difficulty': 'advanced',
            },
            'A project record.\n',
            ensure_content_id=False,
        )
        self.repo.write_text(
            'market-wiki/pages/skills.md',
            '# Market Wiki: Skills\n\nA generated market page.\n',
        )
        # graph/ and search/ hold non-markdown artifacts; nothing may parse
        # them into content rows.
        self.repo.write_text('graph/graph.json', '{"nodes": [], "edges": []}')
        self.repo.write_text(
            'search/search-corpus.json', '{"documents": []}',
        )
        self.sync_log = sync_repo(self.source, self.repo)


class MemberWikiSyncHappyPathTest(_MemberWikiSyncRepoTest):
    def test_sync_reports_no_errors(self):
        self.assertEqual(
            self.sync_log.errors, [],
            f'Expected no errors, got: {self.sync_log.errors}',
        )

    def test_topic_pages_created_with_file_stem_slugs(self):
        slugs = set(TopicPage.objects.values_list('slug', flat=True))
        self.assertEqual(slugs, {'index', 'rag', 'agents'})

    def test_index_page_carries_hub_fields(self):
        index = TopicPage.objects.get(slug='index')
        self.assertEqual(index.title, 'AISL Wiki')
        self.assertEqual(
            index.summary,
            'Topic guides built from every AISL course, workshop, and article.',
        )

    def test_pages_default_to_basic_and_published(self):
        for page in TopicPage.objects.all():
            with self.subTest(slug=page.slug):
                self.assertEqual(page.required_level, LEVEL_BASIC)
                self.assertEqual(page.status, STATUS_PUBLISHED)

    def test_related_frontmatter_stored_as_slug_list(self):
        rag = TopicPage.objects.get(slug='rag')
        self.assertEqual(rag.related, ['agents', 'vector-search'])

    def test_body_rendered_to_html(self):
        rag = TopicPage.objects.get(slug='rag')
        self.assertIn('<p>', rag.body_html)
        self.assertIn('never trained on', rag.body_html)

    def test_records_are_not_synced(self):
        from content.models import Article, Project

        self.assertEqual(Article.objects.count(), 0)
        self.assertEqual(Project.objects.count(), 0)
        # Only the _wiki pages became rows; every record file was claimed.
        self.assertEqual(TopicPage.objects.count(), 3)

    def test_public_knowledge_base_untouched(self):
        # The content repo's public wiki/docs sections are separate
        # families; a wiki-repo sync creates no package KB rows.
        self.assertEqual(KnowledgeBasePage.objects.count(), 0)


class MemberWikiLinkRewriteTest(_MemberWikiSyncRepoTest):
    def test_index_relative_md_links_rewritten(self):
        index = TopicPage.objects.get(slug='index')
        self.assertIn('href="/topics/rag/"', index.body_html)
        self.assertIn('href="/topics/agents/"', index.body_html)
        self.assertNotIn('.md)', index.body_html)
        self.assertNotIn('.md"', index.body_html)

    def test_absolute_canonical_links_untouched(self):
        index = TopicPage.objects.get(slug='index')
        self.assertIn(
            'href="https://aishippinglabs.com/courses/aihero"',
            index.body_html,
        )


class MemberWikiSyncIdempotenceTest(_MemberWikiSyncRepoTest):
    def test_second_sync_is_unchanged(self):
        second_log = sync_repo(self.source, self.repo)
        self.assertEqual(second_log.errors, [])
        self.assertEqual(second_log.items_created, 0)
        self.assertEqual(second_log.items_updated, 0)
        topic_actions = [
            detail['action']
            for detail in second_log.items_detail
            if detail.get('content_type') == 'wiki_topic'
        ]
        self.assertTrue(topic_actions, 'topic items missing from detail')
        self.assertEqual(set(topic_actions), {'unchanged'})
        self.assertEqual(TopicPage.objects.count(), 3)

    def test_body_change_updates_page_and_html(self):
        self.repo.write_markdown(
            '_wiki/rag.md',
            {
                'layout': 'wiki',
                'title': 'RAG',
                'summary': 'Updated summary.',
                'related': ['vector-search'],
            },
            RAG_BODY + '\nAgentic search fixes where it breaks.\n',
            ensure_content_id=False,
        )
        second_log = sync_repo(self.source, self.repo)
        self.assertEqual(second_log.errors, [])
        rag = TopicPage.objects.get(slug='rag')
        self.assertEqual(rag.summary, 'Updated summary.')
        self.assertIn('Agentic search fixes', rag.body)
        self.assertIn('Agentic search fixes', rag.body_html)
        self.assertEqual(rag.related, ['vector-search'])
        self.assertEqual(TopicPage.objects.count(), 3)


class MemberWikiDeleteMissingTest(_MemberWikiSyncRepoTest):
    def test_removed_page_drafted_others_untouched(self):
        self.repo.remove('_wiki/agents.md')
        second_log = sync_repo(self.source, self.repo)
        self.assertEqual(second_log.errors, [])
        drafted = TopicPage.objects.get(slug='agents')
        self.assertEqual(drafted.status, STATUS_DRAFT)
        for slug in ('index', 'rag'):
            self.assertEqual(
                TopicPage.objects.get(slug=slug).status,
                STATUS_PUBLISHED,
            )

    def test_republished_page_returns_published(self):
        self.repo.remove('_wiki/agents.md')
        sync_repo(self.source, self.repo)
        self.repo.write_markdown(
            '_wiki/agents.md',
            {
                'layout': 'wiki',
                'title': 'Agents',
                'summary': 'The loop, the frameworks, durable execution.',
            },
            AGENTS_BODY,
            ensure_content_id=False,
        )
        sync_repo(self.source, self.repo)
        self.assertEqual(
            TopicPage.objects.get(slug='agents').status,
            STATUS_PUBLISHED,
        )


class MemberWikiBrokenFileTest(_MemberWikiSyncRepoTest):
    def test_missing_title_reported_and_page_skipped(self):
        self.repo.write_markdown(
            '_wiki/broken.md',
            {'layout': 'wiki', 'summary': 'No title here.'},
            'A page without a title.\n',
            ensure_content_id=False,
        )
        second_log = sync_repo(self.source, self.repo)
        self.assertTrue(
            second_log.errors,
            'a missing title must surface as a bounded sync error',
        )
        self.assertFalse(TopicPage.objects.filter(slug='broken').exists())
        # The good pages still sync: the failure is bounded, not fatal.
        self.assertEqual(
            TopicPage.objects.get(slug='rag').status,
            STATUS_PUBLISHED,
        )

    def test_non_list_related_reported_and_page_skipped(self):
        self.repo.write_markdown(
            '_wiki/bad-related.md',
            {
                'layout': 'wiki',
                'title': 'Bad Related',
                'related': 'agents',
            },
            'A page with a scalar related field.\n',
            ensure_content_id=False,
        )
        second_log = sync_repo(self.source, self.repo)
        self.assertTrue(second_log.errors)
        self.assertFalse(TopicPage.objects.filter(slug='bad-related').exists())

    def test_nested_wiki_file_reported(self):
        self.repo.write_markdown(
            '_wiki/nested/page.md',
            {'layout': 'wiki', 'title': 'Nested'},
            'Topic pages are flat.\n',
            ensure_content_id=False,
        )
        second_log = sync_repo(self.source, self.repo)
        self.assertTrue(second_log.errors)
        self.assertFalse(TopicPage.objects.filter(slug='page').exists())


class MemberWikiNavAvailabilityTest(_MemberWikiSyncRepoTest):
    """The cached Topics nav flag follows the synced pages (#1688)."""

    def test_flag_true_after_sync(self):
        self.assertTrue(has_published_topics_for_nav())

    def test_flag_false_after_all_pages_removed(self):
        self.repo.remove('_wiki/index.md')
        self.repo.remove('_wiki/rag.md')
        self.repo.remove('_wiki/agents.md')
        sync_repo(self.source, self.repo)
        self.assertFalse(has_published_topics_for_nav())
