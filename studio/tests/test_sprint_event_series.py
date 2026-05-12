"""Studio surfaces for linking event series to sprints (issue #565,
renamed from event-group in #575).

Covers the form (event-series select renders, pre-selects, persists,
unlinks, rejects invalid ids with HTTP 400) and the detail page
("Event series" section: linked series + occurrence table, or
empty-state + "Link an event series" CTA).
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from events.models import Event, EventSeries
from plans.models import Sprint

User = get_user_model()


def _make_event_series(name='Wednesday office hours', slug='weekly-oh'):
    return EventSeries.objects.create(
        name=name,
        slug=slug,
        cadence='weekly',
        cadence_weeks=1,
        day_of_week=2,
        start_time=datetime.time(18, 0),
        timezone='Europe/Berlin',
    )


def _make_event(series, *, position, status='upcoming'):
    base = datetime.datetime(2026, 5, 6, 18, 0, tzinfo=datetime.timezone.utc)
    start = base + datetime.timedelta(days=7 * (position - 1))
    return Event.objects.create(
        title=f'{series.name} — Session {position}',
        slug=f'{series.slug}-session-{position}',
        description='',
        kind='standard',
        platform='zoom',
        start_datetime=start,
        timezone='Europe/Berlin',
        status=status,
        origin='studio',
        event_series=series,
        series_position=position,
        published=True,
    )


class SprintFormEventSeriesSelectTest(TestCase):
    """The Studio sprint form renders the Event series dropdown."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.series_a = _make_event_series(name='Alpha series', slug='alpha')
        cls.series_b = _make_event_series(name='Beta series', slug='beta')

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_create_form_lists_all_event_series_with_none_default(self):
        response = self.client.get('/studio/sprints/new')
        self.assertEqual(response.status_code, 200)
        # The select is named ``event_series`` and exposes a stable test id.
        self.assertContains(
            response, 'data-testid="sprint-event-series"',
        )
        # Both series appear as options.
        self.assertContains(
            response, f'<option value="{self.series_a.pk}"',
        )
        self.assertContains(
            response, f'<option value="{self.series_b.pk}"',
        )
        self.assertContains(response, 'Alpha series')
        self.assertContains(response, 'Beta series')
        # The "— None —" placeholder is selected by default on a new sprint.
        self.assertContains(
            response,
            '<option value="" selected>— None —</option>',
            html=True,
        )

    def test_edit_form_pre_selects_currently_linked_series(self):
        sprint = Sprint.objects.create(
            name='S', slug='s', start_date=datetime.date(2026, 5, 1),
            event_series=self.series_a,
        )
        response = self.client.get(f'/studio/sprints/{sprint.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<option value="{self.series_a.pk}" selected>Alpha series</option>',
            html=True,
        )
        # The other series should be present but NOT selected.
        self.assertContains(
            response,
            f'<option value="{self.series_b.pk}">Beta series</option>',
            html=True,
        )

    def test_edit_form_shows_unlink_hint(self):
        sprint = Sprint.objects.create(
            name='S', slug='s', start_date=datetime.date(2026, 5, 1),
            event_series=self.series_a,
        )
        response = self.client.get(f'/studio/sprints/{sprint.pk}/edit')
        self.assertContains(
            response,
            'data-testid="sprint-event-series-unlink-hint"',
        )

    def test_create_form_does_not_show_unlink_hint(self):
        response = self.client.get('/studio/sprints/new')
        self.assertNotContains(
            response,
            'data-testid="sprint-event-series-unlink-hint"',
        )

    def test_create_form_shows_create_series_hint_link(self):
        response = self.client.get('/studio/sprints/new')
        self.assertContains(
            response,
            'data-testid="sprint-create-event-series-hint"',
        )
        # Link must point at the event-series new flow.
        self.assertContains(response, '/studio/event-series/new')


class SprintFormEventSeriesPersistenceTest(TestCase):
    """Submitting the form persists / clears the FK correctly."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.series = _make_event_series()

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_create_with_event_series_selected_persists_fk(self):
        response = self.client.post('/studio/sprints/new', {
            'name': 'Linked sprint',
            'slug': 'linked-sprint',
            'start_date': '2026-05-01',
            'duration_weeks': '6',
            'status': 'draft',
            'event_series': str(self.series.pk),
        })
        self.assertEqual(response.status_code, 302)
        sprint = Sprint.objects.get(slug='linked-sprint')
        self.assertEqual(sprint.event_series_id, self.series.pk)

    def test_create_without_event_series_persists_null(self):
        response = self.client.post('/studio/sprints/new', {
            'name': 'Solo sprint',
            'slug': 'solo-sprint',
            'start_date': '2026-05-01',
            'duration_weeks': '6',
            'status': 'draft',
            'event_series': '',
        })
        self.assertEqual(response.status_code, 302)
        sprint = Sprint.objects.get(slug='solo-sprint')
        self.assertIsNone(sprint.event_series)

    def test_edit_selecting_none_clears_fk(self):
        sprint = Sprint.objects.create(
            name='Linked', slug='linked',
            start_date=datetime.date(2026, 5, 1),
            event_series=self.series,
        )
        # Add one event so we can verify the unlink does not delete it.
        event = _make_event(self.series, position=1)

        response = self.client.post(
            f'/studio/sprints/{sprint.pk}/edit',
            {
                'name': sprint.name,
                'slug': sprint.slug,
                'start_date': sprint.start_date.isoformat(),
                'duration_weeks': str(sprint.duration_weeks),
                'status': sprint.status,
                'event_series': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        sprint.refresh_from_db()
        self.assertIsNone(sprint.event_series)
        # Series and events survive the unlink.
        self.assertTrue(EventSeries.objects.filter(pk=self.series.pk).exists())
        event.refresh_from_db()
        self.assertEqual(event.event_series_id, self.series.pk)

    def test_edit_invalid_event_series_id_returns_400_and_no_write(self):
        sprint = Sprint.objects.create(
            name='Linked', slug='linked',
            start_date=datetime.date(2026, 5, 1),
            event_series=self.series,
        )
        original_series_id = sprint.event_series_id

        response = self.client.post(
            f'/studio/sprints/{sprint.pk}/edit',
            {
                'name': sprint.name,
                'slug': sprint.slug,
                'start_date': sprint.start_date.isoformat(),
                'duration_weeks': str(sprint.duration_weeks),
                'status': sprint.status,
                'event_series': '99999',  # non-existent
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            'Selected event series does not exist.',
            status_code=400,
        )
        sprint.refresh_from_db()
        # The FK did NOT change.
        self.assertEqual(sprint.event_series_id, original_series_id)

    def test_create_with_invalid_event_series_id_returns_400_and_no_write(self):
        before = Sprint.objects.count()
        response = self.client.post('/studio/sprints/new', {
            'name': 'Broken',
            'slug': 'broken',
            'start_date': '2026-05-01',
            'duration_weeks': '6',
            'status': 'draft',
            'event_series': '99999',
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            'Selected event series does not exist.',
            status_code=400,
        )
        self.assertEqual(Sprint.objects.count(), before)


class SprintDetailEventSeriesSectionTest(TestCase):
    """Studio sprint detail renders linked series or empty-state CTA."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.series = _make_event_series()
        cls.event_1 = _make_event(cls.series, position=1)
        cls.event_2 = _make_event(cls.series, position=2)
        cls.linked_sprint = Sprint.objects.create(
            name='L', slug='l',
            start_date=datetime.date(2026, 5, 1),
            event_series=cls.series,
        )
        cls.unlinked_sprint = Sprint.objects.create(
            name='U', slug='u',
            start_date=datetime.date(2026, 5, 1),
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def test_linked_sprint_shows_series_name_and_event_rows(self):
        response = self.client.get(
            f'/studio/sprints/{self.linked_sprint.pk}/',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'data-testid="sprint-event-series-link"',
        )
        # Series name links to the studio event-series detail.
        self.assertContains(
            response,
            f'/studio/event-series/{self.series.pk}/',
        )
        self.assertContains(
            response, 'data-testid="sprint-event-series-count"',
        )
        # One row per event.
        self.assertContains(
            response, 'data-testid="sprint-event-series-row"', count=2,
        )
        # Each event title links to its Studio edit page.
        self.assertContains(
            response, f'/studio/events/{self.event_1.pk}/edit',
        )
        self.assertContains(
            response, f'/studio/events/{self.event_2.pk}/edit',
        )

    def test_unlinked_sprint_shows_empty_state_and_cta(self):
        response = self.client.get(
            f'/studio/sprints/{self.unlinked_sprint.pk}/',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'data-testid="sprint-event-series-empty"',
        )
        self.assertContains(
            response, 'data-testid="sprint-event-series-link-cta"',
        )
        # CTA points to the edit form with the anchor for the field.
        self.assertContains(
            response,
            f'/studio/sprints/{self.unlinked_sprint.pk}/edit#event-series-field',
        )
        # No occurrence table is rendered.
        self.assertNotContains(
            response, 'data-testid="sprint-event-series-row"',
        )

    def test_linked_series_with_no_events_shows_empty_events_state(self):
        empty_series = _make_event_series(
            name='Empty series', slug='empty-series',
        )
        sprint = Sprint.objects.create(
            name='Empty', slug='empty-link',
            start_date=datetime.date(2026, 5, 1),
            event_series=empty_series,
        )
        response = self.client.get(f'/studio/sprints/{sprint.pk}/')
        self.assertEqual(response.status_code, 200)
        # The series is linked, so the link still renders ...
        self.assertContains(
            response, 'data-testid="sprint-event-series-link"',
        )
        # ... but there is an inner empty-events note (Studio-only copy).
        self.assertContains(
            response,
            'data-testid="sprint-event-series-empty-events"',
        )
        self.assertNotContains(
            response, 'data-testid="sprint-event-series-row"',
        )
