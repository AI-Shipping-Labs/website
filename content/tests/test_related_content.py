from datetime import date, datetime, timedelta

from django.test import Client, TestCase
from django.utils import timezone

from content.access import LEVEL_BASIC, LEVEL_MAIN
from content.models import Article, Project, Tutorial, Workshop
from content.services.related_content import (
    FALLBACK_TITLE,
    RELATED_TITLE,
    _dropped_pair_keys,
    build_related_content_rail,
)
from events.models import Event


def _titles(rail):
    return [item.title for item in rail.items]


def _cards_titled(rail, title):
    return [item for item in rail.items if item.title == title]


def _event_datetime(days=0):
    return timezone.make_aware(datetime(2026, 1, 20, 12, 0)) + timedelta(days=days)


def _future_dt(days=30):
    return timezone.now() + timedelta(days=days)


def _past_dt(days=30):
    return timezone.now() - timedelta(days=days)


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


class RelatedContentPairDedupTest(TestCase):
    """Issue #1359: an Event and its linked Workshop collapse to one card.

    Reporter directive: the Workshop always wins for a genuine pair, regardless
    of whether the linked event is upcoming, past, or has no scheduled time.
    """

    def test_upcoming_pair_collapses_to_the_workshop_card(self):
        current = Article.objects.create(
            title='CV Careers Article',
            slug='cv-careers-article',
            date=date(2026, 1, 10),
            tags=['careers', 'cv'],
            published=True,
        )
        event = Event.objects.create(
            title='Tailor Your CV for AI Engineering Roles',
            slug='tailor-cv-event',
            start_datetime=_future_dt(),
            status='upcoming',
            published=True,
            tags=['careers', 'cv'],
        )
        workshop = Workshop.objects.create(
            title='Tailor Your CV for AI Engineering Roles',
            slug='tailor-cv-workshop',
            date=date(2026, 1, 5),
            status='published',
            tags=['careers', 'cv'],
        )
        workshop.event = event
        workshop.save(update_fields=['event'])

        rail = build_related_content_rail(current)

        pair_cards = _cards_titled(rail, 'Tailor Your CV for AI Engineering Roles')
        self.assertEqual(len(pair_cards), 1)
        self.assertEqual(pair_cards[0].content_type, 'workshop')

    def test_past_pair_collapses_to_the_workshop_card(self):
        current = Article.objects.create(
            title='RAG Careers Article',
            slug='rag-careers-article',
            date=date(2026, 1, 10),
            tags=['rag'],
            published=True,
        )
        event = Event.objects.create(
            title='Ship a RAG Pipeline',
            slug='ship-rag-event',
            start_datetime=_past_dt(),
            status='completed',
            published=True,
            tags=['rag'],
        )
        workshop = Workshop.objects.create(
            title='Ship a RAG Pipeline',
            slug='ship-rag-workshop',
            date=date(2026, 1, 5),
            status='published',
            tags=['rag'],
        )
        workshop.event = event
        workshop.save(update_fields=['event'])

        rail = build_related_content_rail(current)

        pair_cards = _cards_titled(rail, 'Ship a RAG Pipeline')
        self.assertEqual(len(pair_cards), 1)
        self.assertEqual(pair_cards[0].content_type, 'workshop')

    def test_event_without_start_datetime_collapses_to_workshop(self):
        # ``Event.start_datetime`` is a required (NOT NULL) column, so a
        # null-start event cannot be persisted through the ORM. The collapse
        # decision lives in ``_dropped_pair_keys``, so this exercises that real
        # production code path with an in-memory event whose ``start_datetime``
        # is null. The Workshop still wins (unconditional precedence).
        current = Article.objects.create(
            title='Prompt Article',
            slug='prompt-article',
            date=date(2026, 1, 10),
            tags=['prompting'],
            published=True,
        )
        event = Event(pk=9001, start_datetime=None)
        workshop = Workshop(pk=9002)
        workshop.event_id = 9001

        collected = [
            (event, 'event', 'Event', 'calendar'),
            (workshop, 'workshop', 'Workshop', 'graduation-cap'),
        ]
        dropped = _dropped_pair_keys(
            collected,
            current=current,
            current_model_key='content.article',
            current_pk=current.pk,
        )

        self.assertIn(('events.event', 9001), dropped)
        self.assertNotIn(('content.workshop', 9002), dropped)

    def test_workshop_page_hides_its_own_linked_event(self):
        event = Event.objects.create(
            title='Own Linked Event',
            slug='own-linked-event',
            start_datetime=_future_dt(),
            status='upcoming',
            published=True,
            tags=['evaluation'],
        )
        current = Workshop.objects.create(
            title='Evaluation Workshop',
            slug='evaluation-workshop',
            date=date(2026, 1, 5),
            status='published',
            tags=['evaluation'],
        )
        current.event = event
        current.save(update_fields=['event'])
        Article.objects.create(
            title='Evaluation Article',
            slug='evaluation-article',
            date=date(2026, 1, 6),
            tags=['evaluation'],
            published=True,
        )

        rail = build_related_content_rail(current)

        self.assertNotIn('Own Linked Event', _titles(rail))
        self.assertNotIn('Evaluation Workshop', _titles(rail))
        self.assertIn('Evaluation Article', _titles(rail))

    def test_event_page_hides_its_own_linked_workshop(self):
        current = Event.objects.create(
            title='LLMOps Event',
            slug='llmops-event',
            start_datetime=_past_dt(),
            status='completed',
            published=True,
            tags=['llmops'],
        )
        workshop = Workshop.objects.create(
            title='LLMOps Workshop',
            slug='llmops-workshop',
            date=date(2026, 1, 5),
            status='published',
            tags=['llmops'],
        )
        workshop.event = current
        workshop.save(update_fields=['event'])
        Article.objects.create(
            title='LLMOps Article',
            slug='llmops-article',
            date=date(2026, 1, 6),
            tags=['llmops'],
            published=True,
        )

        rail = build_related_content_rail(current)

        self.assertNotIn('LLMOps Workshop', _titles(rail))
        self.assertIn('LLMOps Article', _titles(rail))

    def test_unlinked_events_and_workshops_remain_eligible(self):
        current = Article.objects.create(
            title='Agent Article',
            slug='agent-article-1359',
            date=date(2026, 1, 10),
            tags=['agents'],
            published=True,
        )
        Event.objects.create(
            title='Standalone Event',
            slug='standalone-event',
            start_datetime=_past_dt(),
            status='completed',
            published=True,
            tags=['agents'],
        )
        Workshop.objects.create(
            title='Standalone Workshop',
            slug='standalone-workshop',
            date=date(2026, 1, 5),
            status='published',
            tags=['agents'],
        )

        rail = build_related_content_rail(current)

        self.assertIn('Standalone Event', _titles(rail))
        self.assertIn('Standalone Workshop', _titles(rail))

    def test_non_paired_ordering_is_unchanged(self):
        current = Article.objects.create(
            title='Ordering Current',
            slug='ordering-current',
            date=date(2026, 1, 10),
            tags=['agents', 'mcp'],
            published=True,
        )
        Article.objects.create(
            title='New Two Tag Article',
            slug='ordering-new-two-tag',
            date=date(2026, 1, 6),
            tags=['agents', 'mcp'],
            published=True,
        )
        Project.objects.create(
            title='Old Two Tag Project',
            slug='ordering-old-two-tag',
            date=date(2026, 1, 5),
            tags=['agents', 'mcp'],
            published=True,
        )
        Tutorial.objects.create(
            title='Newest One Tag Tutorial',
            slug='ordering-newest-one-tag',
            date=date(2026, 1, 9),
            tags=['agents'],
            published=True,
        )

        rail = build_related_content_rail(current)

        self.assertEqual(
            _titles(rail),
            [
                'New Two Tag Article',
                'Old Two Tag Project',
                'Newest One Tag Tutorial',
            ],
        )

    def test_single_date_format_across_card_types(self):
        current = Article.objects.create(
            title='Date Format Article',
            slug='date-format-article',
            date=date(2026, 1, 10),
            tags=['dates'],
            published=True,
        )
        Event.objects.create(
            title='Single Digit Event',
            slug='single-digit-event',
            start_datetime=timezone.make_aware(datetime(2026, 7, 8, 12, 0)),
            status='completed',
            published=True,
            tags=['dates'],
        )
        Workshop.objects.create(
            title='Single Digit Workshop',
            slug='single-digit-workshop',
            date=date(2026, 7, 8),
            status='published',
            tags=['dates'],
        )
        Article.objects.create(
            title='Single Digit Article',
            slug='single-digit-article',
            date=date(2026, 7, 8),
            tags=['dates'],
            published=True,
        )
        Tutorial.objects.create(
            title='Single Digit Tutorial',
            slug='single-digit-tutorial',
            date=date(2026, 7, 8),
            tags=['dates'],
            published=True,
        )
        Project.objects.create(
            title='Single Digit Project',
            slug='single-digit-project',
            date=date(2026, 7, 8),
            tags=['dates'],
            published=True,
        )

        rail = build_related_content_rail(current, limit=10)

        cards = {item.title: item for item in rail.items}
        for title in (
            'Single Digit Workshop',
            'Single Digit Event',
            'Single Digit Article',
            'Single Digit Tutorial',
            'Single Digit Project',
        ):
            self.assertEqual(cards[title].date_label, 'July 8, 2026')
        for item in rail.items:
            self.assertNotIn('July 08, 2026', item.date_label)


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
