"""Public wiki/docs page rendering coverage for the knowledge base (#1685).

The public routes are the package's (`/wiki/`, `/docs/`) with this site's
own templates under ``templates/knowledge_base/``. These tests seed rows
through the same sync families the content repository uses and assert the
rendered pages: listing pages, page bodies, breadcrumbs, sequential
navigation, section children, drafting and sitemap inclusion.
"""

from community_base.knowledge_base.models import (
    SECTION_DOCS,
    SECTION_WIKI,
    STATUS_PUBLISHED,
    KnowledgeBasePage,
)
from django.test import TestCase

from integrations.tests.sync_fixtures import make_sync_repo, sync_repo

CONTENT_REPO = 'AI-Shipping-Labs/content'


def _kb_content_id(n):
    return f'73d341c8-0000-4000-8000-{n:012d}'


class _KnowledgeBaseRenderRepoTest(TestCase):
    """Syncs the seed-shaped fixture repo once per test."""

    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self,
            repo_name=CONTENT_REPO,
            prefix='kb-render-',
        )
        self.repo.write_markdown(
            'wiki/about-the-wiki.md',
            {
                'content_id': _kb_content_id(1),
                'title': 'About the Community Wiki',
                'summary': 'What the wiki is and how to contribute.',
            },
            '# About the Community Wiki\n\nThe wiki is a set of standalone '
            'reference pages.\n\n## What belongs in the wiki\n\n'
            '- Reference material you keep coming back to.\n',
        )
        self.repo.write_markdown(
            'docs/getting-started.md',
            {
                'content_id': _kb_content_id(2),
                'title': 'Getting Started with AI Shipping Labs',
                'summary': 'The four steps from signup to shipping.',
                'nav_order': 0,
            },
            '# Getting Started with AI Shipping Labs\n\nWork through the '
            'pages in order.\n',
        )
        self.repo.write_markdown(
            'docs/taking-courses.md',
            {
                'content_id': _kb_content_id(3),
                'title': 'Taking Courses',
                'parent': 'getting-started',
                'nav_order': 2,
            },
            '# Taking Courses\n\nCourses are the guided path: modules and '
            'units, in order.\n',
        )
        self.repo.write_markdown(
            'docs/attending-workshops.md',
            {
                'content_id': _kb_content_id(4),
                'title': 'Attending Workshops',
                'parent': 'getting-started',
                'nav_order': 3,
            },
            '# Attending Workshops\n\nWorkshops are live, hands-on sessions.'
            '\n',
        )
        sync_repo(self.source, self.repo)


class WikiHomeViewTest(_KnowledgeBaseRenderRepoTest):
    def test_wiki_home_lists_pages(self):
        response = self.client.get('/wiki/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="wiki-page-list"')
        self.assertContains(response, 'About the Community Wiki')
        # Docs pages never leak into the wiki listing.
        self.assertNotContains(response, 'Taking Courses')

    def test_wiki_page_links_to_detail(self):
        response = self.client.get('/wiki/')
        self.assertContains(response, 'href="/wiki/about-the-wiki/"')


class WikiPageViewTest(_KnowledgeBaseRenderRepoTest):
    def test_wiki_page_renders_body(self):
        response = self.client.get('/wiki/about-the-wiki/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'About the Community Wiki')
        self.assertContains(response, 'standalone reference pages')
        self.assertContains(response, 'What belongs in the wiki')

    def test_wiki_page_back_link_to_section(self):
        response = self.client.get('/wiki/about-the-wiki/')
        self.assertContains(response, 'href="/wiki"')


class DocsHomeViewTest(_KnowledgeBaseRenderRepoTest):
    def test_docs_home_lists_tree(self):
        response = self.client.get('/docs/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="docs-page-tree"')
        self.assertContains(response, 'Getting Started with AI Shipping Labs')
        self.assertContains(response, 'Taking Courses')
        self.assertContains(response, 'Attending Workshops')

    def test_docs_home_links_nested_page(self):
        response = self.client.get('/docs/')
        self.assertContains(response, 'href="/docs/getting-started/taking-courses/"')


class NestedDocsPageViewTest(_KnowledgeBaseRenderRepoTest):
    def test_nested_docs_page_renders(self):
        response = self.client.get('/docs/getting-started/taking-courses/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Taking Courses')
        self.assertContains(response, 'modules and units')
        self.assertContains(response, 'data-testid="kb-page-body"')

    def test_nested_docs_page_shows_breadcrumb_to_parent(self):
        response = self.client.get('/docs/getting-started/taking-courses/')
        self.assertContains(response, 'aria-label="Breadcrumb"')
        self.assertContains(response, 'href="/docs/getting-started/"')
        self.assertContains(response, 'Getting Started with AI Shipping Labs')

    def test_parent_page_lists_children(self):
        response = self.client.get('/docs/getting-started/')
        self.assertContains(response, 'In this section')
        self.assertContains(response, 'href="/docs/getting-started/taking-courses/"')
        self.assertContains(response, 'href="/docs/getting-started/attending-workshops/"')

    def test_docs_page_sequential_navigation(self):
        # Depth-first reading order: taking-courses (2) sits between
        # getting-started (0) and attending-workshops (3).
        response = self.client.get('/docs/getting-started/taking-courses/')
        self.assertContains(response, 'aria-label="Page navigation"')
        self.assertContains(response, 'href="/docs/getting-started/"')
        self.assertContains(
            response, 'href="/docs/getting-started/attending-workshops/"',
        )


class KnowledgeBaseDraftHiddenTest(_KnowledgeBaseRenderRepoTest):
    def test_drafted_page_returns_404(self):
        page = KnowledgeBasePage.objects.get(
            section=SECTION_DOCS, slug='taking-courses',
        )
        page.status = 'draft'
        page.save(update_fields=['status', 'updated_at'])
        response = self.client.get('/docs/getting-started/taking-courses/')
        self.assertEqual(response.status_code, 404)

    def test_drafted_wiki_page_returns_404(self):
        page = KnowledgeBasePage.objects.get(
            section=SECTION_WIKI, slug='about-the-wiki',
        )
        page.status = 'draft'
        page.save(update_fields=['status', 'updated_at'])
        response = self.client.get('/wiki/about-the-wiki/')
        self.assertEqual(response.status_code, 404)


class KnowledgeBaseNavContextTest(_KnowledgeBaseRenderRepoTest):
    def test_context_exposes_kb_flags_after_sync(self):
        response = self.client.get('/wiki/')
        self.assertIs(response.context['has_published_wiki'], True)
        self.assertIs(response.context['has_published_docs'], True)

    def test_primary_nav_carries_learning_items_when_published(self):
        response = self.client.get('/wiki/')
        learning = next(
            group for group in response.context['primary_nav']
            if group['key'] == 'learning'
        )
        slugs = {item['slug'] for item in learning['items']}
        self.assertIn('wiki', slugs)
        self.assertIn('docs', slugs)

    def test_primary_nav_hides_section_without_published_pages(self):
        KnowledgeBasePage.objects.filter(section=SECTION_WIKI).update(
            status='draft',
        )
        from content.nav_availability import refresh_knowledge_base_nav_cache

        refresh_knowledge_base_nav_cache()
        response = self.client.get('/docs/')
        learning = next(
            group for group in response.context['primary_nav']
            if group['key'] == 'learning'
        )
        slugs = {item['slug'] for item in learning['items']}
        self.assertNotIn('wiki', slugs)
        self.assertIn('docs', slugs)


class KnowledgeBaseSitemapTest(_KnowledgeBaseRenderRepoTest):
    def test_sitemap_includes_wiki_page(self):
        response = self.client.get('/sitemap.xml')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/wiki/about-the-wiki/')

    def test_sitemap_includes_nested_docs_page(self):
        response = self.client.get('/sitemap.xml')
        self.assertContains(response, '/docs/getting-started/taking-courses/')

    def test_sitemap_excludes_drafted_page(self):
        page = KnowledgeBasePage.objects.get(
            section=SECTION_WIKI, slug='about-the-wiki',
        )
        page.status = 'draft'
        page.save(update_fields=['status', 'updated_at'])
        response = self.client.get('/sitemap.xml')
        self.assertNotContains(response, '/wiki/about-the-wiki/')
