from datetime import date, datetime, timedelta

from django.test import Client, TestCase
from django.utils import timezone

from content.access import LEVEL_BASIC, LEVEL_MAIN
from content.models import Article, Project, Tutorial, Workshop
from content.services.related_content import (
    FALLBACK_TITLE,
    RELATED_TITLE,
    build_related_content_rail,
)
from events.models import Event


def _titles(rail):
    return [item.title for item in rail.items]


def _event_datetime(days=0):
    return timezone.make_aware(datetime(2026, 1, 20, 12, 0)) + timedelta(days=days)


class RelatedContentBuilderTest(TestCase):
    def test_scores_by_shared_tags_then_public_date_then_title(self):
        current = Article.objects.create(
            title='Current',
            slug='current',
            date=date(2026, 1, 10),
            tags=['Agents', 'MCP'],
            published=True,
        )
        Article.objects.create(
            title='New Two Tag Article',
            slug='new-two-tag',
            date=date(2026, 1, 6),
            tags=['agents', 'mcp'],
            published=True,
        )
        Project.objects.create(
            title='Old Two Tag Project',
            slug='old-two-tag',
            date=date(2026, 1, 5),
            tags=['agents', 'mcp'],
            published=True,
        )
        Tutorial.objects.create(
            title='Newest One Tag Tutorial',
            slug='newest-one-tag',
            date=date(2026, 1, 9),
            tags=['agents'],
            published=True,
        )
        rail = build_related_content_rail(current)

        self.assertEqual(rail.title, RELATED_TITLE)
        self.assertFalse(rail.is_fallback)
        self.assertEqual(
            _titles(rail),
            [
                'New Two Tag Article',
                'Old Two Tag Project',
                'Newest One Tag Tutorial',
            ],
        )

    def test_fallback_uses_newest_published_internal_pages_excluding_current(self):
        current = Project.objects.create(
            title='Untagged Current Project',
            slug='untagged-current',
            date=date(2026, 1, 5),
            tags=[],
            published=True,
        )
        Article.objects.create(
            title='Older Article',
            slug='older-article',
            date=date(2026, 1, 1),
            published=True,
        )
        Workshop.objects.create(
            title='Middle Workshop',
            slug='middle-workshop',
            date=date(2026, 1, 2),
            status='published',
        )
        Tutorial.objects.create(
            title='Newest Tutorial',
            slug='newest-tutorial',
            date=date(2026, 1, 3),
            published=True,
        )

        rail = build_related_content_rail(current)

        self.assertEqual(rail.title, FALLBACK_TITLE)
        self.assertTrue(rail.is_fallback)
        self.assertEqual(
            _titles(rail),
            ['Newest Tutorial', 'Middle Workshop', 'Older Article'],
        )
        self.assertNotIn('Untagged Current Project', _titles(rail))

    def test_includes_cross_type_matches_and_excludes_current_object(self):
        current = Article.objects.create(
            title='Agent Article',
            slug='agent-article',
            date=date(2026, 2, 1),
            tags=['agents'],
            published=True,
        )
        workshop = Workshop.objects.create(
            title='Agent Workshop',
            slug='agent-workshop',
            date=date(2026, 2, 3),
            status='published',
            tags=['agents'],
        )
        event = Event.objects.create(
            title='Agent Event',
            slug='agent-event',
            start_datetime=_event_datetime(),
            status='completed',
            published=True,
            tags=['agents'],
        )

        rail = build_related_content_rail(current)

        self.assertIn(workshop.title, _titles(rail))
        self.assertIn(event.title, _titles(rail))
        self.assertNotIn(current.title, _titles(rail))
        self.assertEqual(len(rail.items), 2)

    def test_filters_unpublished_draft_private_and_hidden_candidates(self):
        current = Article.objects.create(
            title='Published Article',
            slug='published-article',
            date=date(2026, 3, 1),
            tags=['rag'],
            published=True,
        )
        published_project = Project.objects.create(
            title='Published Project',
            slug='published-project',
            date=date(2026, 3, 2),
            tags=['rag'],
            published=True,
        )
        Article.objects.create(
            title='Draft Article',
            slug='draft-article',
            date=date(2026, 3, 3),
            tags=['rag'],
            published=False,
        )
        Tutorial.objects.create(
            title='Unpublished Tutorial',
            slug='unpublished-tutorial',
            date=date(2026, 3, 4),
            tags=['rag'],
            published=False,
        )
        Project.objects.create(
            title='Pending Project',
            slug='pending-project',
            date=date(2026, 3, 5),
            tags=['rag'],
            published=False,
            status='pending_review',
        )
        Workshop.objects.create(
            title='Draft Workshop',
            slug='draft-workshop',
            date=date(2026, 3, 6),
            status='draft',
            tags=['rag'],
        )
        Event.objects.create(
            title='Draft Event',
            slug='draft-event',
            start_datetime=_event_datetime(),
            status='draft',
            published=True,
            tags=['rag'],
        )
        Event.objects.create(
            title='Unpublished Event',
            slug='unpublished-event',
            start_datetime=_event_datetime(1),
            status='completed',
            published=False,
            tags=['rag'],
        )

        rail = build_related_content_rail(current)

        self.assertEqual(_titles(rail), [published_project.title])

    def test_gated_cards_expose_only_safe_metadata_and_canonical_url(self):
        current = Article.objects.create(
            title='Open Agent Article',
            slug='open-agent-article',
            date=date(2026, 4, 1),
            tags=['agents'],
            published=True,
        )
        Tutorial.objects.create(
            title='Paid Agent Tutorial',
            slug='paid-agent-tutorial',
            description='Safe public teaser.',
            content_markdown='SECRET GATED BODY',
            date=date(2026, 4, 2),
            tags=['agents'],
            required_level=LEVEL_BASIC,
            published=True,
        )
        Workshop.objects.create(
            title='Main Agent Workshop',
            slug='main-agent-workshop',
            description='Workshop teaser.',
            date=date(2026, 4, 3),
            status='published',
            tags=['agents'],
            pages_required_level=LEVEL_MAIN,
            recording_required_level=LEVEL_MAIN,
            code_repo_url='https://github.com/example/private-code',
        )

        rail = build_related_content_rail(current)

        paid_tutorial = next(
            item for item in rail.items if item.title == 'Paid Agent Tutorial'
        )
        self.assertTrue(paid_tutorial.is_gated)
        self.assertEqual(paid_tutorial.tier_label, 'Basic or above')
        self.assertEqual(paid_tutorial.url, '/tutorials/paid-agent-tutorial')
        self.assertEqual(paid_tutorial.description, 'Safe public teaser.')
        self.assertNotIn('SECRET GATED BODY', paid_tutorial.description)

        workshop = next(
            item for item in rail.items if item.title == 'Main Agent Workshop'
        )
        self.assertTrue(workshop.is_gated)
        self.assertEqual(workshop.tier_label, 'Main or above')
        self.assertEqual(workshop.url, '/workshops/main-agent-workshop')
        self.assertNotIn('github.com/example/private-code', workshop.description)


class RelatedContentRailRenderTest(TestCase):
    def setUp(self):
        self.client = Client()

    def test_blog_detail_renders_matching_shared_rail(self):
        Article.objects.create(
            title='Current Article',
            slug='current-article',
            description='Current description',
            date=date(2026, 5, 1),
            tags=['agents'],
            published=True,
        )
        Project.objects.create(
            title='Related Agent Project',
            slug='related-agent-project',
            description='Build an agent.',
            date=date(2026, 5, 2),
            tags=['agents'],
            published=True,
        )

        response = self.client.get('/blog/current-article')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="related-content-rail"')
        self.assertContains(response, 'Related content')
        self.assertContains(response, 'Related Agent Project')
        self.assertContains(response, 'href="/projects/related-agent-project"')

    def test_rail_is_not_rendered_when_no_candidates_exist(self):
        Article.objects.create(
            title='Only Article',
            slug='only-article',
            description='Only description',
            date=date(2026, 5, 1),
            tags=['agents'],
            published=True,
        )

        response = self.client.get('/blog/only-article')

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="related-content-rail"')
