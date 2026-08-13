"""Event detail backlinks to public parent/community records (#1390)."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from bookclub.models import Book
from events.models import (
    Event,
    EventRegistration,
    EventSeries,
    SeriesRegistration,
)
from plans.models import Sprint

User = get_user_model()


def _make_series(*, slug='relationship-series', is_active=True):
    return EventSeries.objects.create(
        name='AI Builders Sessions',
        slug=slug,
        cadence='none',
        day_of_week=None,
        start_time=None,
        timezone='Europe/Berlin',
        required_level=0,
        is_active=is_active,
    )


def _make_event(*, series=None, slug='relationship-session', status='upcoming'):
    start = timezone.now() + timedelta(days=7)
    return Event.objects.create(
        title='Shipping together',
        slug=slug,
        start_datetime=start,
        end_datetime=start + timedelta(hours=1),
        status=status,
        published=True,
        required_level=0,
        event_series=series,
    )


def _make_book(series, *, title, slug, status='current'):
    return Book.objects.create(
        title=title,
        slug=slug,
        author='Test Author',
        status=status,
        required_level=0,
        start_date=timezone.localdate(),
        event_series=series,
    )


def _make_sprint(series, *, name, slug, status='active'):
    return Sprint.objects.create(
        name=name,
        slug=slug,
        start_date=timezone.localdate(),
        duration_weeks=6,
        status=status,
        min_tier_level=0,
        event_series=series,
    )


class EventDetailRelationshipTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.series = _make_series()
        cls.event = _make_event(series=cls.series)
        cls.user = User.objects.create_user(
            email='relationship-member@test.com', password='pass',
        )

    def test_public_relationships_have_canonical_ordered_rows(self):
        books = [
            _make_book(
                self.series, title='Zeta Book', slug='zeta-book',
                status='finished',
            ),
            _make_book(
                self.series, title='Alpha Book', slug='alpha-book',
                status='current',
            ),
            _make_book(
                self.series, title='Beta Book', slug='beta-book',
                status='upcoming',
            ),
        ]
        sprints = [
            _make_sprint(
                self.series, name='Zeta Sprint', slug='zeta-sprint',
                status='completed',
            ),
            _make_sprint(
                self.series, name='Alpha Sprint', slug='alpha-sprint',
                status='active',
            ),
        ]

        response = self.client.get(self.event.get_absolute_url())

        expected = [
            {
                'kind': 'event_series',
                'text': f'Event series · {self.series.name}',
                'url': self.series.get_absolute_url(),
            },
            *[
                {
                    'kind': 'book',
                    'text': f'Book Club · {book.title}',
                    'url': book.get_absolute_url(),
                }
                for book in sorted(books, key=lambda book: (book.title, book.pk))
            ],
            *[
                {
                    'kind': 'sprint',
                    'text': f'Community Sprint · {sprint.name}',
                    'url': sprint.get_absolute_url(),
                }
                for sprint in sorted(
                    sprints, key=lambda sprint: (sprint.name, sprint.pk),
                )
            ],
        ]
        self.assertEqual(response.context['event_relationships'], expected)
        self.assertTemplateUsed(response, 'events/_event_relationships.html')
        self.assertContains(
            response, 'data-testid="event-relationship-context"', count=1,
        )
        self.assertContains(
            response, 'data-testid="event-relationship-row"',
            count=len(expected),
        )
        self.assertContains(
            response,
            'aria-labelledby="event-relationship-context-title"',
            count=1,
        )
        self.assertContains(
            response, 'aria-label="Related event context"', count=1,
        )
        for relationship in expected:
            with self.subTest(relationship=relationship['text']):
                self.assertContains(response, relationship['text'], count=1)
                self.assertContains(
                    response,
                    f'href="{relationship["url"]}"',
                    count=1,
                )

    def test_hidden_and_unrelated_programs_do_not_leak(self):
        hidden_names = [
            'Draft Book',
            'Cancelled Book',
            'Draft Sprint',
            'Cancelled Sprint',
            'Unrelated Book',
            'Unrelated Sprint',
        ]
        _make_book(
            self.series, title=hidden_names[0], slug='draft-book', status='draft',
        )
        _make_book(
            self.series, title=hidden_names[1], slug='cancelled-book',
            status='cancelled',
        )
        _make_sprint(
            self.series, name=hidden_names[2], slug='draft-sprint', status='draft',
        )
        _make_sprint(
            self.series, name=hidden_names[3], slug='cancelled-sprint',
            status='cancelled',
        )
        unrelated_series = _make_series(slug='unrelated-series')
        _make_event(series=unrelated_series, slug='unrelated-session')
        _make_book(
            unrelated_series, title=hidden_names[4], slug='unrelated-book',
        )
        _make_sprint(
            unrelated_series, name=hidden_names[5], slug='unrelated-sprint',
        )

        response = self.client.get(self.event.get_absolute_url())

        self.assertEqual(
            response.context['event_relationships'],
            [{
                'kind': 'event_series',
                'text': f'Event series · {self.series.name}',
                'url': self.series.get_absolute_url(),
            }],
        )
        for hidden_name in hidden_names:
            with self.subTest(hidden_name=hidden_name):
                self.assertNotContains(response, hidden_name)

    def test_individual_dead_destinations_and_empty_context_are_omitted(self):
        inactive_series = _make_series(
            slug='inactive-series', is_active=False,
        )
        inactive_event = _make_event(
            series=inactive_series, slug='inactive-series-event',
        )
        visible_book = _make_book(
            inactive_series, title='Still Public Book', slug='still-public-book',
        )

        response = self.client.get(inactive_event.get_absolute_url())

        self.assertEqual(
            response.context['event_relationships'],
            [{
                'kind': 'book',
                'text': f'Book Club · {visible_book.title}',
                'url': visible_book.get_absolute_url(),
            }],
        )
        self.assertNotContains(response, f'Event series · {inactive_series.name}')

        cancelled_series = _make_series(slug='cancelled-only-series')
        cancelled_event = _make_event(
            series=cancelled_series,
            slug='cancelled-only-event',
            status='cancelled',
        )
        cancelled_response = self.client.get(cancelled_event.get_absolute_url())
        self.assertEqual(cancelled_response.context['event_relationships'], [])
        self.assertNotContains(
            cancelled_response, 'data-testid="event-relationship-context"',
        )

        standalone = _make_event(slug='standalone-relationship-event')
        standalone_response = self.client.get(standalone.get_absolute_url())
        self.assertEqual(standalone_response.context['event_relationships'], [])
        self.assertNotContains(
            standalone_response, 'data-testid="event-relationship-context"',
        )

    def test_relationship_discovery_is_registration_independent(self):
        book = _make_book(
            self.series, title='Registration Independent Book',
            slug='registration-independent-book',
        )
        sprint = _make_sprint(
            self.series, name='Registration Independent Sprint',
            slug='registration-independent-sprint',
        )
        expected = [
            self.series.get_absolute_url(),
            book.get_absolute_url(),
            sprint.get_absolute_url(),
        ]

        responses = [self.client.get(self.event.get_absolute_url())]
        self.client.force_login(self.user)
        responses.append(self.client.get(self.event.get_absolute_url()))
        EventRegistration.objects.create(event=self.event, user=self.user)
        responses.append(self.client.get(self.event.get_absolute_url()))
        EventRegistration.objects.filter(event=self.event, user=self.user).delete()
        SeriesRegistration.objects.create(series=self.series, user=self.user)
        responses.append(self.client.get(self.event.get_absolute_url()))

        for index, response in enumerate(responses):
            with self.subTest(viewer_state=index):
                self.assertEqual(
                    [item['url'] for item in response.context['event_relationships']],
                    expected,
                )

    def test_query_count_does_not_grow_with_related_program_rows(self):
        _make_book(self.series, title='First Book', slug='first-book')
        _make_sprint(self.series, name='First Sprint', slug='first-sprint')

        with CaptureQueriesContext(connection) as small_context:
            small_response = self.client.get(self.event.get_absolute_url())
        self.assertEqual(len(small_response.context['event_relationships']), 3)

        for index in range(2, 8):
            _make_book(
                self.series,
                title=f'Book {index}',
                slug=f'relationship-book-{index}',
            )
            _make_sprint(
                self.series,
                name=f'Sprint {index}',
                slug=f'relationship-sprint-{index}',
            )

        with CaptureQueriesContext(connection) as large_context:
            large_response = self.client.get(self.event.get_absolute_url())
        self.assertEqual(len(large_response.context['event_relationships']), 15)
        self.assertEqual(len(large_context), len(small_context))
