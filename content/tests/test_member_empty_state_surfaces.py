"""Rendered empty-state regressions for issue #1227."""

from datetime import date
from html.parser import HTMLParser

from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase

from content.models import Article, CuratedLink, Project


class _ComponentLinkParser(HTMLParser):
    def __init__(self, testid='member-empty-state'):
        super().__init__()
        self.testid = testid
        self.depth = 0
        self.links = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if self.depth:
            self.depth += 1
        elif tag == 'div' and attributes.get('data-testid') == self.testid:
            self.depth = 1
        if self.depth and tag == 'a':
            self.links.append(attributes.get('href'))

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1


def _component_links(response, testid='member-empty-state'):
    parser = _ComponentLinkParser(testid)
    parser.feed(response.content.decode())
    return parser.links


class TagsEmptyStateTest(TestCase):
    def test_tags_index_fresh_state_uses_exact_copy_without_cta(self):
        response = self.client.get('/tags')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="member-empty-state"')
        self.assertContains(response, 'data-empty-kind="fresh"')
        self.assertContains(response, 'data-lucide="tag"')
        self.assertContains(response, 'No tags yet')
        self.assertContains(response, "Content will be tagged as it's published.")
        self.assertEqual(_component_links(response), [])

    def test_tag_detail_filter_state_interpolates_tag_and_resets(self):
        response = self.client.get('/tags/nonexistent')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="member-empty-state"')
        self.assertContains(response, 'data-empty-kind="filter"')
        self.assertContains(response, 'No content found')
        self.assertContains(
            response,
            'No published content uses the &quot;nonexistent&quot; tag yet.',
            html=False,
        )
        self.assertContains(response, 'href="/tags"')
        self.assertContains(response, 'Browse all tags')

    def test_tag_detail_copy_escapes_interpolated_tag(self):
        request = RequestFactory().get('/tags/unsafe')
        request.session = {}
        request.user = AnonymousUser()
        rendered = render_to_string(
            'content/tags_detail.html',
            {
                'tag': '<script>alert(1)</script>',
                'results': [],
                'result_count': 0,
            },
            request=request,
        )

        self.assertNotIn('<script>alert(1)</script>', rendered)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', rendered)

    def test_non_empty_tags_keep_existing_listing(self):
        Article.objects.create(
            title='Tagged article',
            slug='tagged-article-1227',
            date=date(2026, 7, 13),
            tags=['agents'],
            published=True,
        )

        index_response = self.client.get('/tags')
        detail_response = self.client.get('/tags/agents')

        self.assertContains(index_response, 'agents')
        self.assertNotContains(index_response, 'No tags yet')
        self.assertContains(detail_response, 'Tagged article')
        self.assertNotContains(detail_response, 'No content found')


class ResourcesEmptyStateTest(TestCase):
    def test_fresh_state_uses_exact_copy_without_reset(self):
        response = self.client.get('/resources')

        self.assertContains(response, 'data-testid="member-empty-state"')
        self.assertContains(response, 'data-empty-kind="fresh"')
        self.assertContains(response, 'data-lucide="folder-open"')
        self.assertContains(response, 'No curated links yet')
        self.assertContains(
            response,
            'Check back soon for workshops, courses, and references.',
        )
        self.assertNotContains(response, 'View all links')

    def test_tag_filter_state_uses_exact_copy_and_reset(self):
        CuratedLink.objects.create(
            item_id='resource-1227',
            title='Published resource',
            url='https://example.com/resource',
            category='other',
            tags=['python'],
            published=True,
        )

        response = self.client.get('/resources?tag=no-match')

        self.assertContains(response, 'data-empty-kind="filter"')
        self.assertContains(response, 'No links found')
        self.assertContains(
            response,
            'No curated links found with the selected tags.',
        )
        self.assertContains(response, 'href="/resources"')
        self.assertContains(response, 'View all links')
        self.assertNotContains(response, 'Published resource')

    def test_non_empty_resources_keep_existing_cards(self):
        CuratedLink.objects.create(
            item_id='visible-resource-1227',
            title='Visible resource 1227',
            url='https://example.com/visible',
            category='other',
            published=True,
        )

        response = self.client.get('/resources')

        self.assertContains(response, 'Visible resource 1227')
        self.assertNotContains(response, 'No curated links yet')


class ProjectsEmptyStateTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(
            title='Beginner project 1227',
            slug='beginner-project-1227',
            description='A visible project.',
            date=date(2026, 7, 13),
            difficulty='beginner',
            tags=['python'],
            published=True,
        )

    def assert_project_markers(self, response, kind):
        self.assertContains(response, 'data-testid="projects-empty-state"')
        self.assertContains(response, 'data-testid="member-empty-state"')
        self.assertContains(response, f'data-empty-kind="{kind}"', count=2)
        self.assertContains(response, 'data-lucide="rocket"')

    def test_fresh_state_uses_exact_copy_without_reset(self):
        Project.objects.all().delete()

        response = self.client.get('/projects')

        self.assert_project_markers(response, 'fresh')
        self.assertContains(response, 'No project ideas yet')
        self.assertContains(
            response,
            'Check back soon for pet and portfolio project ideas.',
        )
        self.assertNotContains(response, 'View all projects')

    def test_tag_filter_state_uses_exact_copy_and_reset(self):
        response = self.client.get('/projects?tag=no-match')

        self.assert_project_markers(response, 'filter')
        self.assertContains(response, 'No projects match these tags')
        self.assertContains(response, 'No projects match the selected tags.')
        self.assertContains(response, 'href="/projects"')
        self.assertContains(response, 'View all projects')

    def test_difficulty_filter_state_uses_exact_copy_and_reset(self):
        response = self.client.get('/projects?difficulty=expert')

        self.assert_project_markers(response, 'filter')
        self.assertContains(response, 'No projects match this difficulty')
        self.assertContains(
            response,
            'No projects match the selected difficulty.',
        )
        self.assertContains(response, 'href="/projects"')
        self.assertContains(response, 'View all projects')

    def test_tag_copy_wins_when_tag_and_difficulty_are_active(self):
        response = self.client.get(
            '/projects?difficulty=expert&tag=no-match',
        )

        self.assert_project_markers(response, 'filter')
        self.assertContains(response, 'No projects match these tags')
        self.assertNotContains(response, 'No projects match this difficulty')

    def test_non_empty_projects_keep_existing_cards(self):
        response = self.client.get('/projects')

        self.assertContains(response, 'Beginner project 1227')
        self.assertNotContains(response, 'projects-empty-state')
