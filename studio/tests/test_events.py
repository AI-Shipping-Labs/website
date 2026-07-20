"""Tests for studio event views.

Verifies:
- Event list with search and status filter
- Event list renders ``New event`` and ``New event series`` create buttons
- Event create form (GET and POST) creates a Studio-origin row (issue #574)
- Event create validation: missing title, missing date, duplicate slug
- Event edit form (GET and POST) with pre-populated date/time/duration
- Synced events: description read-only, operational fields editable, GitHub link shown
- Status transitions
- Date/time picker UX: separate Date, Time, Duration fields
- end_datetime computed from start_datetime + duration
- Duration defaults to 1 hour when left blank
- No datetime-local inputs on the form
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from freezegun import freeze_time

from content.access import LEVEL_MAIN, LEVEL_OPEN, LEVEL_PREMIUM
from events.models import Event, EventSeries
from tests.fixtures import StaffUserMixin, TierSetupMixin

User = get_user_model()

# date-rot-ok: this fixed instant deliberately verifies the exact UTC and
# Europe/Berlin summer-time date copy. Freezing Django time before its one-hour
# fallback end keeps Upcoming-list membership independent of the host clock.
FIXED_TIMEZONE_EVENT_START = datetime(
    2026, 7, 20, 12, 0, tzinfo=ZoneInfo('UTC'),
)
FROZEN_TIME_BEFORE_EVENT_END = '2026-07-19T12:00:00Z'


class StudioEventListTest(StaffUserMixin, TestCase):
    """Test event list view."""

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_list_returns_200(self):
        response = self.client.get('/studio/events/')
        self.assertEqual(response.status_code, 200)

    def test_list_uses_correct_template(self):
        response = self.client.get('/studio/events/')
        self.assertTemplateUsed(response, 'studio/events/list.html')

    def test_list_shows_events(self):
        Event.objects.create(
            title='Test Event', slug='test-event',
            start_datetime=timezone.now(),
        )
        response = self.client.get('/studio/events/')
        self.assertContains(response, 'Test Event')

    def test_list_shows_kind_and_platform_icons(self):
        Event.objects.create(
            title='Workshop Event',
            slug='workshop-event',
            start_datetime=timezone.now(),
            kind='workshop',
            platform='custom',
        )
        response = self.client.get('/studio/events/')
        self.assertContains(response, '>Kind</th>')
        self.assertContains(response, '>Platform</th>')
        self.assertContains(response, 'data-testid="event-kind-icon"')
        self.assertContains(response, 'data-lucide="wrench"')
        self.assertContains(response, 'aria-label="Workshop"')
        self.assertContains(response, 'data-testid="event-platform-icon"')
        self.assertContains(response, 'data-lucide="link"')
        self.assertContains(response, 'aria-label="Custom URL"')
        self.assertNotContains(response, '>Workshop<')
        self.assertNotContains(response, '>Custom URL<')

    def test_list_has_no_status_or_origin_columns(self):
        Event.objects.create(
            title='Compact Event',
            slug='compact-event',
            start_datetime=timezone.now(),
            origin='github',
            source_repo='AI-Shipping-Labs/content',
        )
        response = self.client.get('/studio/events/')
        self.assertNotContains(response, '>Status</th>')
        self.assertNotContains(response, 'data-label="Status"')
        self.assertNotContains(response, '>Origin</th>')
        self.assertNotContains(response, 'data-label="Origin"')

    def test_github_origin_renders_icon_next_to_title(self):
        Event.objects.create(
            title='GitHub Event',
            slug='github-icon-event',
            start_datetime=timezone.now(),
            origin='github',
            source_repo='AI-Shipping-Labs/content',
        )
        Event.objects.create(
            title='Studio Event',
            slug='studio-icon-event',
            start_datetime=timezone.now(),
            origin='studio',
        )
        response = self.client.get('/studio/events/')
        body = response.content.decode()
        github_row = body[
            body.index('GitHub Event'):body.index('Studio Event')
        ]
        self.assertIn('data-testid="origin-github-icon"', github_row)
        self.assertIn('aria-label="Synced from GitHub"', github_row)
        studio_row = body[body.index('Studio Event'):]
        self.assertNotIn('data-testid="origin-github-icon"', studio_row)

    def test_series_renders_compact_icon_link(self):
        series = EventSeries.objects.create(
            name='Friday Builds',
            start_time=datetime(2026, 6, 1, 18, 0).time(),
        )
        Event.objects.create(
            title='Series Event',
            slug='series-event',
            start_datetime=timezone.now(),
            event_series=series,
        )
        response = self.client.get('/studio/events/')
        self.assertContains(response, 'data-testid="event-series-link"')
        self.assertContains(response, 'aria-label="Series: Friday Builds"')
        self.assertContains(response, 'title="Friday Builds"')
        self.assertContains(response, 'data-lucide="layers"')
        self.assertNotContains(response, '>Friday Builds<')

    @freeze_time(FROZEN_TIME_BEFORE_EVENT_END)
    def test_list_renders_operator_timezone_date(self):
        self.staff.preferred_timezone = 'Europe/Berlin'
        self.staff.save(update_fields=['preferred_timezone'])
        Event.objects.create(
            title='Berlin Date Event',
            slug='berlin-date-event',
            start_datetime=FIXED_TIMEZONE_EVENT_START,
        )
        response = self.client.get('/studio/events/')
        self.assertContains(response, 'data-testid="event-row-date"')
        self.assertContains(response, '2026-07-20 14:00 Europe/Berlin')
        self.assertNotContains(response, '2026-07-20 12:00 Europe/Berlin')

    @freeze_time(FROZEN_TIME_BEFORE_EVENT_END)
    def test_list_renders_utc_label_without_preference(self):
        Event.objects.create(
            title='UTC Date Event',
            slug='utc-date-event',
            start_datetime=FIXED_TIMEZONE_EVENT_START,
        )
        response = self.client.get('/studio/events/')
        self.assertContains(response, 'data-testid="event-row-date"')
        self.assertContains(response, '2026-07-20 12:00 UTC')

    def test_list_renders_create_buttons(self):
        """Both ``New event`` and ``New event series`` buttons are present."""
        response = self.client.get('/studio/events/')
        self.assertContains(response, 'data-testid="event-new-button"')
        self.assertContains(response, '>New event<')
        self.assertContains(response, 'data-testid="event-series-new-button"')
        self.assertContains(response, '>New event series<')

    def test_list_new_event_button_links_to_create_url(self):
        """The new button routes to /studio/events/new."""
        response = self.client.get('/studio/events/')
        self.assertContains(response, 'href="/studio/events/new"')

    def test_list_filter_by_status(self):
        Event.objects.create(
            title='UpcomingEventXYZ', slug='upcoming',
            start_datetime=timezone.now(), status='upcoming',
        )
        Event.objects.create(
            title='DraftEventXYZ', slug='draft',
            start_datetime=timezone.now(), status='draft',
        )
        response = self.client.get('/studio/events/?status=upcoming')
        self.assertContains(response, 'UpcomingEventXYZ')
        self.assertNotContains(response, 'DraftEventXYZ')

    def test_list_search(self):
        Event.objects.create(
            title='Python Workshop', slug='python',
            start_datetime=timezone.now(),
        )
        Event.objects.create(
            title='Java Workshop', slug='java',
            start_datetime=timezone.now(),
        )
        response = self.client.get('/studio/events/?q=Python')
        self.assertContains(response, 'Python Workshop')
        self.assertNotContains(response, 'Java Workshop')


class StudioEventListStatusGroupingTest(StaffUserMixin, TestCase):
    """Upcoming/Past grouping for the default and dedicated past lists."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        now = timezone.now()
        cls.up_soon = Event.objects.create(
            title='UpSoon', slug='up-soon', status='upcoming',
            start_datetime=now + timedelta(days=1),
            end_datetime=now + timedelta(days=1, hours=1),
        )
        cls.up_later = Event.objects.create(
            title='UpLater', slug='up-later', status='upcoming',
            start_datetime=now + timedelta(days=10),
            end_datetime=now + timedelta(days=10, hours=1),
        )
        cls.past_recent = Event.objects.create(
            title='PastRecent', slug='past-recent', status='completed',
            start_datetime=now - timedelta(days=1, hours=1),
            end_datetime=now - timedelta(days=1),
        )
        cls.past_old = Event.objects.create(
            title='PastOld', slug='past-old', status='completed',
            start_datetime=now - timedelta(days=30, hours=1),
            end_datetime=now - timedelta(days=30),
        )
        cls.completed_future = Event.objects.create(
            title='CompletedFuture', slug='completed-future', status='completed',
            start_datetime=now + timedelta(days=5),
            end_datetime=now + timedelta(days=5, hours=1),
        )
        cls.draft = Event.objects.create(
            title='DraftFuture', slug='draft-future', status='draft',
            start_datetime=now + timedelta(days=3),
            end_datetime=now + timedelta(days=3, hours=1),
        )
        cls.cancelled = Event.objects.create(
            title='CancelledEv', slug='cancelled-ev', status='cancelled',
            start_datetime=now + timedelta(days=2),
            end_datetime=now + timedelta(days=2, hours=1),
        )

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def _ctx(self, response, key):
        return [e.pk for e in response.context[key]]

    def test_status_column_and_badges_are_not_rendered(self):
        response = self.client.get('/studio/events/')
        self.assertNotContains(response, 'data-testid="event-time-chip"')
        self.assertNotContains(response, 'data-testid="event-status-badge"')
        self.assertNotContains(response, '>Status</th>')

    def test_upcoming_group_membership(self):
        response = self.client.get('/studio/events/')
        upcoming = self._ctx(response, 'upcoming_events')
        self.assertIn(self.up_soon.pk, upcoming)
        self.assertIn(self.up_later.pk, upcoming)
        self.assertIn(self.draft.pk, upcoming)
        self.assertNotIn(self.completed_future.pk, upcoming)
        self.assertNotIn(self.cancelled.pk, upcoming)
        self.assertNotIn(self.past_recent.pk, upcoming)

    def test_past_group_membership(self):
        response = self.client.get('/studio/events/past/')
        past = self._ctx(response, 'past_events')
        self.assertIn(self.past_recent.pk, past)
        self.assertIn(self.past_old.pk, past)
        self.assertIn(self.completed_future.pk, past)
        self.assertIn(self.cancelled.pk, past)
        self.assertNotIn(self.up_soon.pk, past)

    def test_upcoming_sorted_soonest_first(self):
        response = self.client.get('/studio/events/')
        upcoming = self._ctx(response, 'upcoming_events')
        # up_soon (1d) before up_later (10d).
        self.assertLess(
            upcoming.index(self.up_soon.pk),
            upcoming.index(self.up_later.pk),
        )

    def test_past_sorted_most_recent_first(self):
        response = self.client.get('/studio/events/past/')
        past = self._ctx(response, 'past_events')
        # past_recent (1d ago) before past_old (30d ago).
        self.assertLess(
            past.index(self.past_recent.pk),
            past.index(self.past_old.pk),
        )

    def test_completed_future_labelled_past(self):
        response = self.client.get('/studio/events/past/')
        event = next(
            e for e in response.context['past_events']
            if e.pk == self.completed_future.pk
        )
        self.assertEqual(event.derived_status, 'past')
        self.assertEqual(event.derived_status_label, 'Past')

    def test_draft_labelled_draft(self):
        response = self.client.get('/studio/events/')
        event = next(
            e for e in response.context['upcoming_events']
            if e.pk == self.draft.pk
        )
        self.assertEqual(event.derived_status, 'draft')
        self.assertEqual(event.derived_status_label, 'Draft')

    def test_cancelled_labelled_cancelled(self):
        response = self.client.get('/studio/events/past/')
        event = next(
            e for e in response.context['past_events']
            if e.pk == self.cancelled.pk
        )
        self.assertEqual(event.derived_status, 'cancelled')
        self.assertEqual(event.derived_status_label, 'Cancelled')

    def test_past_event_labelled_past(self):
        response = self.client.get('/studio/events/past/')
        event = next(
            e for e in response.context['past_events']
            if e.pk == self.past_recent.pk
        )
        self.assertEqual(event.derived_status, 'past')
        self.assertEqual(event.derived_status_label, 'Past')

    def test_section_headings_show_counts(self):
        response = self.client.get('/studio/events/')
        self.assertEqual(response.context['upcoming_count'], 3)
        self.assertEqual(response.context['past_count'], 4)
        self.assertContains(response, 'Upcoming (3)')
        self.assertNotContains(response, 'data-testid="event-section-past"')
        self.assertContains(response, 'Past events (4)')

    def test_default_page_hides_past_section(self):
        response = self.client.get('/studio/events/')
        self.assertContains(response, 'data-testid="event-section-upcoming"')
        self.assertNotContains(response, 'data-testid="event-section-past"')
        self.assertNotContains(response, 'PastRecent')
        self.assertNotContains(response, 'PastOld')

    def test_status_filter_narrows_rows(self):
        response = self.client.get('/studio/events/?status=draft')
        upcoming = self._ctx(response, 'upcoming_events')
        self.assertEqual(upcoming, [self.draft.pk])

    def test_search_narrows_rows(self):
        response = self.client.get('/studio/events/?q=UpSoon')
        self.assertEqual(self._ctx(response, 'upcoming_events'), [self.up_soon.pk])

    def test_past_search_narrows_rows(self):
        response = self.client.get('/studio/events/past/?q=PastRecent')
        self.assertEqual(
            self._ctx(response, 'past_events'),
            [self.past_recent.pk],
        )

    def test_empty_section_hidden(self):
        """status=draft yields only an upcoming row; past section hidden."""
        response = self.client.get('/studio/events/?status=draft')
        self.assertContains(response, 'data-testid="event-section-upcoming"')
        self.assertNotContains(response, 'data-testid="event-section-past"')


class StudioEventListEmptyStateTest(StaffUserMixin, TestCase):
    """Empty-state behaviour for the grouped events list (#820)."""

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_no_events_renders_upcoming_empty_state(self):
        response = self.client.get('/studio/events/')
        self.assertContains(response, 'data-testid="event-empty-state-upcoming"')
        self.assertContains(response, 'No upcoming events.')
        self.assertContains(response, '>New event<')
        self.assertContains(response, '>Past events<')

    def test_no_upcoming_with_past_renders_upcoming_empty_state(self):
        Event.objects.create(
            title='Old Event',
            slug='old-event-empty',
            start_datetime=timezone.now() - timedelta(days=2),
            end_datetime=timezone.now() - timedelta(days=2, hours=-1),
            status='completed',
        )
        response = self.client.get('/studio/events/')
        self.assertContains(response, 'data-testid="event-empty-state-upcoming"')
        self.assertContains(response, 'No upcoming events.')
        self.assertContains(response, 'Past events (1)')
        self.assertNotContains(response, 'Old Event')

    def test_no_past_renders_past_empty_state(self):
        Event.objects.create(
            title='Future Event',
            slug='future-event-empty',
            start_datetime=timezone.now() + timedelta(days=2),
        )
        response = self.client.get('/studio/events/past/')
        self.assertContains(response, 'data-testid="event-empty-state-past"')
        self.assertContains(response, 'No past events yet.')
        self.assertNotContains(response, 'Future Event')

    def test_filter_no_match_renders_filtered_empty_state(self):
        Event.objects.create(
            title='Real Event', slug='real',
            start_datetime=timezone.now(),
        )
        response = self.client.get('/studio/events/?q=zzz-no-match')
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'data-testid="studio-empty-state-filter"'
        )


class StudioEventPastPaginationTest(StaffUserMixin, TestCase):
    """Past events are paginated and retain active filters."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        now = timezone.now()
        for index in range(30):
            Event.objects.create(
                title=f'Past Page Event {index:02d}',
                slug=f'past-page-event-{index:02d}',
                start_datetime=now - timedelta(days=index + 1),
                end_datetime=now - timedelta(days=index + 1, hours=-1),
                status='completed',
            )
        cls.upcoming = Event.objects.create(
            title='Upcoming Not Past',
            slug='upcoming-not-past',
            start_datetime=now + timedelta(days=3),
            status='upcoming',
        )
        cls.cancelled = Event.objects.create(
            title='Cancelled Past Filter',
            slug='cancelled-past-filter',
            start_datetime=now + timedelta(days=1),
            end_datetime=now + timedelta(days=1, hours=1),
            status='cancelled',
        )

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_past_view_first_page_is_past_only_and_paginated(self):
        response = self.client.get('/studio/events/past/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['past_events']), 25)
        self.assertEqual(response.context['past_count'], 31)
        self.assertContains(response, 'Past Page Event 00')
        self.assertNotContains(response, 'Upcoming Not Past')
        self.assertContains(response, 'data-testid="event-past-list-pager"')
        self.assertContains(response, '?page=2')

    def test_past_view_second_page_returns_remaining_rows(self):
        response = self.client.get('/studio/events/past/?page=2')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page'].number, 2)
        self.assertEqual(len(response.context['past_events']), 6)

    def test_past_view_page_out_of_range_clamps(self):
        response = self.client.get('/studio/events/past/?page=999')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page'].number, 2)

    def test_past_view_filters_and_pager_preserve_querystring(self):
        response = self.client.get(
            '/studio/events/past/?q=Past+Page&status=completed'
        )
        self.assertEqual(response.context['past_count'], 30)
        self.assertContains(response, 'Past Page Event 00')
        self.assertNotContains(response, 'Cancelled Past Filter')
        self.assertContains(response, '?q=Past+Page&amp;status=completed&amp;page=2')


class StudioEventCreateTest(StaffUserMixin, TestCase):
    """Test the studio event create flow (issue #574).

    A POST to ``/studio/events/new`` creates a ``origin='studio'`` Event
    and redirects the admin to the new event's edit page. Validation
    errors re-render the form with the submitted values preserved.
    """

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_create_form_get_returns_200(self):
        response = self.client.get('/studio/events/new')
        self.assertEqual(response.status_code, 200)

    def test_create_form_uses_form_template(self):
        response = self.client.get('/studio/events/new')
        self.assertTemplateUsed(response, 'studio/events/form.html')

    def test_create_form_has_no_event_in_context(self):
        response = self.client.get('/studio/events/new')
        self.assertIsNone(response.context['event'])

    def test_create_form_renders_new_event_heading(self):
        response = self.client.get('/studio/events/new')
        self.assertContains(response, 'New Event')

    def test_create_form_hides_sidebar_panels(self):
        """The right-hand sidebar only renders when an event exists."""
        response = self.client.get('/studio/events/new')
        self.assertNotContains(response, 'data-testid="event-state-panel"')
        self.assertNotContains(response, 'data-testid="zoom-meeting-panel"')

    def test_create_form_anonymous_redirects_to_login(self):
        self.client.logout()
        response = self.client.get('/studio/events/new')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_create_form_non_staff_forbidden(self):
        from accounts.models import User
        self.client.logout()
        user = User.objects.create_user(
            email='member-574@test.com', password='pw',
            is_staff=False,
        )
        user.email_verified = True
        user.save()
        self.client.login(email='member-574@test.com', password='pw')
        response = self.client.get('/studio/events/new')
        self.assertEqual(response.status_code, 403)

    def test_post_with_valid_data_creates_event(self):
        response = self.client.post('/studio/events/new', {
            'title': 'Office Hours May 21',
            'slug': '',
            'event_date': '21/05/2026',
            'event_time': '18:00',
            'duration_hours': '',
        })
        events = Event.objects.filter(title='Office Hours May 21')
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], f'/studio/events/{event.pk}/edit')

    def test_post_created_event_has_studio_origin(self):
        self.client.post('/studio/events/new', {
            'title': 'Origin Check',
            'event_date': '10/06/2026',
            'event_time': '10:00',
        })
        event = Event.objects.get(title='Origin Check')
        self.assertEqual(event.origin, 'studio')
        # The origin invariant treats None and '' as equivalent
        # (both falsy per ``bool(source_repo)``); we accept either.
        self.assertFalse(bool(event.source_repo))

    def test_post_created_event_uses_defaults(self):
        """Status defaults to draft; required_level to 0; timezone to admin TZ.

        Issue #665: when the admin has no ``preferred_timezone`` set
        (the default for the StaffUserMixin fixture) the form picker
        falls back to ``settings.TIME_ZONE`` (UTC), never the historical
        'Europe/Berlin' hardcode.
        """
        self.client.post('/studio/events/new', {
            'title': 'Defaults Check',
            'event_date': '10/06/2026',
            'event_time': '10:00',
        })
        event = Event.objects.get(title='Defaults Check')
        self.assertEqual(event.status, 'draft')
        self.assertEqual(event.required_level, 0)
        self.assertEqual(event.timezone, 'UTC')
        self.assertEqual(event.platform, 'zoom')
        self.assertEqual(event.kind, 'standard')
        self.assertTrue(event.published)

    def test_create_studio_event_defaults_required_level_zero(self):
        self.client.post('/studio/events/new', {
            'title': 'Default Level Event',
            'event_date': '10/06/2026',
            'event_time': '10:00',
        })

        event = Event.objects.get(title='Default Level Event')
        self.assertEqual(event.required_level, LEVEL_OPEN)

    def test_create_studio_event_persists_required_level(self):
        self.client.post('/studio/events/new', {
            'title': 'Premium Level Event',
            'event_date': '10/06/2026',
            'event_time': '10:00',
            'required_level': str(LEVEL_PREMIUM),
        })

        event = Event.objects.get(title='Premium Level Event')
        self.assertEqual(event.required_level, LEVEL_PREMIUM)

    def test_create_studio_event_rejects_integer_invalid_required_level(self):
        for tampered_value in ('5', '999'):
            with self.subTest(tampered_value=tampered_value):
                title = f'Invalid Level Event {tampered_value}'
                response = self.client.post('/studio/events/new', {
                    'title': title,
                    'event_date': '10/06/2026',
                    'event_time': '10:00',
                    'required_level': tampered_value,
                })

                self.assertEqual(response.status_code, 302)
                event = Event.objects.get(title=title)
                self.assertEqual(event.required_level, LEVEL_OPEN)

    def test_post_blank_slug_is_derived_from_title(self):
        self.client.post('/studio/events/new', {
            'title': 'Hello World Event',
            'slug': '',
            'event_date': '10/06/2026',
            'event_time': '10:00',
        })
        event = Event.objects.get(title='Hello World Event')
        self.assertEqual(event.slug, 'hello-world-event')

    def test_post_blank_duration_defaults_to_one_hour(self):
        self.client.post('/studio/events/new', {
            'title': 'Default Duration',
            'event_date': '10/06/2026',
            'event_time': '10:00',
            'duration_hours': '',
        })
        event = Event.objects.get(title='Default Duration')
        delta = event.end_datetime - event.start_datetime
        self.assertEqual(delta.total_seconds(), 3600)

    def test_post_empty_title_rerenders_with_error(self):
        response = self.client.post('/studio/events/new', {
            'title': '',
            'event_date': '10/06/2026',
            'event_time': '10:00',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="error-title"')
        self.assertEqual(Event.objects.count(), 0)

    def test_post_empty_title_preserves_other_inputs(self):
        response = self.client.post('/studio/events/new', {
            'title': '',
            'event_date': '10/06/2026',
            'event_time': '10:00',
        })
        # Date field is repopulated
        self.assertContains(response, 'value="10/06/2026"')

    def test_post_invalid_date_rerenders_with_error(self):
        response = self.client.post('/studio/events/new', {
            'title': 'Bad Date',
            'event_date': 'not-a-date',
            'event_time': '10:00',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="error-event-date"')
        self.assertEqual(Event.objects.count(), 0)

    def test_post_invalid_date_preserves_title(self):
        response = self.client.post('/studio/events/new', {
            'title': 'Quick Demo',
            'event_date': '',
            'event_time': '10:00',
        })
        self.assertContains(response, 'value="Quick Demo"')

    def test_post_duplicate_slug_rerenders_with_error(self):
        Event.objects.create(
            title='Existing', slug='office-hours',
            start_datetime=datetime(2026, 6, 1, 10, 0),
        )
        response = self.client.post('/studio/events/new', {
            'title': 'Office Hours',
            'slug': 'office-hours',
            'event_date': '10/06/2026',
            'event_time': '10:00',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="error-slug"')
        # Only the pre-existing row exists
        self.assertEqual(Event.objects.filter(slug='office-hours').count(), 1)

    def test_post_saves_explicit_status(self):
        self.client.post('/studio/events/new', {
            'title': 'Upcoming Talk',
            'event_date': '10/06/2026',
            'event_time': '10:00',
            'status': 'upcoming',
        })
        event = Event.objects.get(title='Upcoming Talk')
        self.assertEqual(event.status, 'upcoming')

    def test_created_event_appears_on_list(self):
        self.client.post('/studio/events/new', {
            'title': 'Visible On List',
            'event_date': '21/07/2026',
            'event_time': '10:00',
        })
        response = self.client.get('/studio/events/')
        self.assertContains(response, 'Visible On List')


class StudioEventEditTest(StaffUserMixin, TierSetupMixin, TestCase):
    """Test event editing with pre-populated date/time/duration fields."""

    def setUp(self):
        self.client.login(**self.staff_credentials)
        # Issue #665: pin the stored TZ to UTC so the naive datetime
        # (interpreted as UTC under USE_TZ=True) renders unchanged in
        # the edit form's date/time inputs.
        self.event = Event.objects.create(
            title='Edit Event', slug='edit-event',
            start_datetime=datetime(2026, 6, 1, 10, 0),
            end_datetime=datetime(2026, 6, 1, 11, 30),
            status='draft',
            timezone='UTC',
        )

    def _edit_payload(self, **overrides):
        data = {
            'title': self.event.title,
            'slug': self.event.slug,
            'event_date': '01/06/2026',
            'event_time': '10:00',
            'duration_hours': '1.5',
            'timezone': self.event.timezone,
            'status': self.event.status,
            'required_level': str(self.event.required_level),
            'tags': ', '.join(self.event.tags),
        }
        data.update(overrides)
        return data

    def test_edit_form_returns_200(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertEqual(response.status_code, 200)

    def test_edit_form_selects_use_studio_select_class(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')

        self.assertContains(response, 'select.studio-select')
        content = response.content.decode()
        status_pos = content.index('name="status"')
        status_tag = content[content.rfind('<select', 0, status_pos):status_pos + 250]
        platform_pos = content.index('name="platform"')
        platform_tag = content[content.rfind('<select', 0, platform_pos):platform_pos + 250]
        self.assertIn('studio-select', status_tag)
        self.assertIn('studio-select', platform_tag)

    def test_edit_form_has_no_datetime_local_input(self):
        """The old datetime-local inputs must be removed from edit form."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
        self.assertNotIn('type="datetime-local"', content)
        self.assertNotIn('name="start_datetime"', content)
        self.assertNotIn('name="end_datetime"', content)

    def test_edit_form_prepopulates_date(self):
        """Edit form pre-populates Date field from stored start_datetime."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
        self.assertIn('01/06/2026', content)

    def test_edit_form_prepopulates_time(self):
        """Edit form pre-populates Time field from stored start_datetime."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
        self.assertIn('value="10:00"', content)

    def test_edit_form_prepopulates_duration(self):
        """Edit form pre-populates Duration from end - start (1.5 hours)."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
        self.assertIn('value="1.5"', content)

    def test_edit_form_prepopulates_duration_default_1_when_no_end(self):
        """Duration defaults to 1 when end_datetime is null."""
        self.event.end_datetime = None
        self.event.save()
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
        self.assertIn('value="1"', content)

    def test_edit_form_shows_datetime_summary(self):
        """Edit form shows a resolved datetime summary line.

        Issue #855: the resolved line is now explicit about its timezone,
        so the label reads "Resolved (UTC):" instead of the old bare
        "Resolved:".
        """
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
        self.assertIn('Resolved (UTC):', content)

    def test_edit_form_shows_enabled_required_level_select_with_options(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
        import re
        select_match = re.search(
            r'<select[^>]*name="required_level"[^>]*>', content,
        )
        self.assertIsNotNone(select_match)
        self.assertNotIn('disabled', select_match.group(0))
        self.assertContains(response, 'Free (0)')
        self.assertContains(response, 'Basic (10)')
        self.assertContains(response, 'Main (20)')
        self.assertContains(response, 'Premium (30)')

    def test_edit_event_get_preselects_current_level(self):
        self.event.required_level = LEVEL_MAIN
        self.event.save(update_fields=['required_level'])

        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(response, '<option value="20" selected>Main (20)</option>')

    def test_edit_event_post(self):
        """Edit an event using the new date/time/duration fields."""
        self.client.post(f'/studio/events/{self.event.pk}/edit', {
            'title': 'Updated Event',
            'slug': 'edit-event',
            'event_date': '15/12/2024',
            'event_time': '14:00',
            'duration_hours': '2',
            'timezone': 'UTC',
            'status': 'upcoming',
            'required_level': '10',
            'tags': 'event, , live ,, workshop ',
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, 'Updated Event')
        self.assertEqual(self.event.status, 'upcoming')
        self.assertEqual(self.event.tags, ['event', 'live', 'workshop'])

    def test_edit_studio_event_changes_required_level(self):
        self.client.post(
            f'/studio/events/{self.event.pk}/edit',
            self._edit_payload(required_level=str(LEVEL_MAIN)),
        )

        self.event.refresh_from_db()
        self.assertEqual(self.event.required_level, LEVEL_MAIN)

    def test_bad_required_level_value_does_not_500(self):
        self.event.required_level = LEVEL_MAIN
        self.event.save(update_fields=['required_level'])

        response = self.client.post(
            f'/studio/events/{self.event.pk}/edit',
            self._edit_payload(required_level='notanumber'),
        )

        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.required_level, LEVEL_MAIN)

    def test_integer_invalid_required_level_keeps_existing_value(self):
        for tampered_value in ('5', '999'):
            with self.subTest(tampered_value=tampered_value):
                self.event.required_level = LEVEL_MAIN
                self.event.save(update_fields=['required_level'])

                response = self.client.post(
                    f'/studio/events/{self.event.pk}/edit',
                    self._edit_payload(required_level=tampered_value),
                )

                self.assertEqual(response.status_code, 302)
                self.event.refresh_from_db()
                self.assertEqual(self.event.required_level, LEVEL_MAIN)

    def test_required_level_change_updates_event_access_gating(self):
        self.event.status = 'upcoming'
        self.event.published = True
        self.event.save(update_fields=['status', 'published'])
        future_date = (timezone.now() + timedelta(days=7)).strftime('%d/%m/%Y')

        self.client.post(
            f'/studio/events/{self.event.pk}/edit',
            self._edit_payload(
                event_date=future_date,
                status='upcoming',
                required_level=str(LEVEL_MAIN),
            ),
        )
        self.event.refresh_from_db()
        self.assertEqual(self.event.required_level, LEVEL_MAIN)

        free_user = User.objects.create_user(
            email='free-event-gate@test.com',
            password='pass',
            email_verified=True,
        )
        free_user.tier = self.free_tier
        free_user.save()
        main_user = User.objects.create_user(
            email='main-event-gate@test.com',
            password='pass',
            email_verified=True,
        )
        main_user.tier = self.main_tier
        main_user.save()

        self.client.logout()
        self.client.login(email='free-event-gate@test.com', password='pass')
        response = self.client.get(self.event.get_absolute_url())
        self.assertFalse(response.context['has_access'])
        self.assertContains(response, 'Upgrade to Main to attend')
        self.assertNotContains(response, 'id="register-btn"')
        register_response = self.client.post(
            f'/api/events/{self.event.slug}/register',
        )
        self.assertEqual(register_response.status_code, 403)

        self.client.logout()
        self.client.login(email='main-event-gate@test.com', password='pass')
        response = self.client.get(self.event.get_absolute_url())
        self.assertTrue(response.context['has_access'])
        self.assertContains(response, 'id="register-btn"')
        self.assertNotContains(response, 'Upgrade to Main')
        register_response = self.client.post(
            f'/api/events/{self.event.slug}/register',
        )
        self.assertEqual(register_response.status_code, 201)

    def test_edit_event_saves_correct_datetimes(self):
        """Editing with time=09:00 in UTC + duration=3 stores 09:00 UTC start."""
        self.client.post(f'/studio/events/{self.event.pk}/edit', {
            'title': 'Edit Event',
            'slug': 'edit-event',
            'event_date': '01/06/2026',
            'event_time': '09:00',
            'duration_hours': '3',
            # Issue #665: form posts the IANA name; storage is UTC.
            'timezone': 'UTC',
            'status': 'draft',
            'required_level': '0',
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.start_datetime.year, 2026)
        self.assertEqual(self.event.start_datetime.month, 6)
        self.assertEqual(self.event.start_datetime.day, 1)
        self.assertEqual(self.event.start_datetime.hour, 9)
        self.assertEqual(self.event.start_datetime.minute, 0)
        self.assertEqual(self.event.end_datetime.hour, 12)
        self.assertEqual(self.event.end_datetime.minute, 0)
        self.assertEqual(self.event.timezone, 'UTC')

    def test_edit_event_in_new_york_persists_utc_instant(self):
        """Posting tz=America/New_York stores the equivalent UTC instant.

        Issue #665 acceptance: date=15/06/2027, time=14:30,
        tz=America/New_York persists start_datetime = 2027-06-15T18:30Z
        and event.timezone='America/New_York'.
        """
        self.client.post(f'/studio/events/{self.event.pk}/edit', {
            'title': 'Edit Event',
            'slug': 'edit-event',
            'event_date': '15/06/2027',
            'event_time': '14:30',
            'duration_hours': '1',
            'timezone': 'America/New_York',
            'status': 'draft',
            'required_level': '0',
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.timezone, 'America/New_York')
        # UTC instant equivalent to 2027-06-15T14:30 in NYC (DST, UTC-4).
        from datetime import UTC
        from datetime import datetime as _dt
        self.assertEqual(
            self.event.start_datetime,
            _dt(2027, 6, 15, 18, 30, tzinfo=UTC),
        )

    def test_edit_event_status_transitions(self):
        """Test status can be changed from draft to upcoming."""
        self.client.post(f'/studio/events/{self.event.pk}/edit', {
            'title': 'Edit Event',
            'slug': 'edit-event',
            'event_date': '01/12/2024',
            'event_time': '10:00',
            'duration_hours': '1',
            'timezone': 'Europe/Berlin',
            'status': 'upcoming',
            'required_level': '0',
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, 'upcoming')

    def test_edit_nonexistent_event_returns_404(self):
        response = self.client.get('/studio/events/99999/edit')
        self.assertEqual(response.status_code, 404)


class StudioEventSyncedTest(StaffUserMixin, TestCase):
    """Test that synced events show GitHub link and have read-only content fields."""

    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.event = Event.objects.create(
            title='Synced Event', slug='synced-event',
            description='Original description',
            start_datetime=datetime(2026, 6, 1, 10, 0),
            end_datetime=datetime(2026, 6, 1, 11, 0),
            status='draft',
            origin='github',
            source_repo='AI-Shipping-Labs/content',
            source_path='my-event.md',
        )

    def test_synced_event_shows_origin_panel(self):
        """Synced events display the shared origin panel."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(response, 'data-testid="origin-panel"')
        self.assertContains(response, 'Synced from GitHub')
        self.assertContains(response, 'AI-Shipping-Labs/content')
        self.assertContains(response, 'my-event.md')
        self.assertNotContains(response, 'data-testid="synced-banner"')

    def test_synced_event_shows_edit_on_github_link(self):
        """Synced events show an 'Edit on GitHub' link."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(response, 'Edit on GitHub')

    def test_synced_event_description_is_disabled(self):
        """Description field is disabled for synced events."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
        # The description textarea should have the disabled attribute
        # Find the description textarea and check it has disabled
        self.assertIn('name="description"', content)
        # Check that there is a disabled textarea for description
        import re
        desc_match = re.search(
            r'<textarea[^>]*name="description"[^>]*>', content
        )
        self.assertIsNotNone(desc_match)
        self.assertIn('disabled', desc_match.group(0))

    def test_synced_event_title_is_disabled(self):
        """Title field is disabled for synced events."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
        import re
        title_match = re.search(
            r'<input[^>]*name="title"[^>]*>', content
        )
        self.assertIsNotNone(title_match)
        self.assertIn('disabled', title_match.group(0))

    def test_synced_event_status_is_editable(self):
        """Status field remains editable for synced events."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
        import re
        status_match = re.search(
            r'<select[^>]*name="status"[^>]*>', content
        )
        self.assertIsNotNone(status_match)
        self.assertNotIn('disabled', status_match.group(0))

    def test_required_level_select_disabled_for_synced_event(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
        import re
        level_match = re.search(
            r'<select[^>]*name="required_level"[^>]*>', content,
        )
        self.assertIsNotNone(level_match)
        self.assertIn('disabled', level_match.group(0))

    def test_synced_event_has_no_max_participants_input(self):
        """Issue #984: the Max Participants input was removed entirely."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        content = response.content.decode()
        self.assertNotIn('name="max_participants"', content)

    def test_synced_event_post_updates_operational_fields(self):
        """POST to synced event updates status but not description."""
        self.client.post(f'/studio/events/{self.event.pk}/edit', {
            'status': 'upcoming',
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, 'upcoming')
        # Description should not change
        self.assertEqual(self.event.description, 'Original description')

    def test_synced_event_post_does_not_change_title(self):
        """POST to synced event does not change the title."""
        self.client.post(f'/studio/events/{self.event.pk}/edit', {
            'title': 'Hacked Title',
            'status': 'draft',
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, 'Synced Event')

    def test_synced_event_post_does_not_change_required_level(self):
        self.event.required_level = LEVEL_OPEN
        self.event.save(update_fields=['required_level'])

        self.client.post(f'/studio/events/{self.event.pk}/edit', {
            'status': 'upcoming',
            'required_level': str(LEVEL_PREMIUM),
        })

        self.event.refresh_from_db()
        self.assertEqual(self.event.required_level, LEVEL_OPEN)

    def test_synced_event_shows_view_event_title(self):
        """Synced event page shows 'View Event' instead of 'Edit Event'."""
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(response, 'View Event')

    def test_non_synced_event_has_no_origin_panel(self):
        """Non-synced events do not show source metadata UI."""
        event = Event.objects.create(
            title='Local Event', slug='local-event',
            start_datetime=datetime(2026, 6, 1, 10, 0),
            status='draft',
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertNotContains(response, 'data-testid="synced-banner"')
        self.assertNotContains(response, 'data-testid="origin-panel"')


class StudioEventCreateZoomTest(StaffUserMixin, TestCase):
    """Test Studio endpoint for creating Zoom meetings for events."""

    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.event = Event.objects.create(
            title='Live Event', slug='live-event',
            start_datetime=timezone.now(),
            timezone='Europe/Berlin',
            status='draft',
        )

    def test_create_zoom_success(self):
        from unittest.mock import MagicMock, patch

        from django.test import override_settings

        with override_settings(
            ZOOM_CLIENT_ID='test-client-id',
            ZOOM_CLIENT_SECRET='test-client-secret',
            ZOOM_ACCOUNT_ID='test-account-id',
        ):
            with patch('integrations.services.zoom.requests.post') as mock_post:
                from integrations.services import zoom
                zoom.clear_token_cache()

                token_resp = MagicMock()
                token_resp.status_code = 200
                token_resp.json.return_value = {
                    'access_token': 'tok', 'expires_in': 3600,
                }
                meeting_resp = MagicMock()
                meeting_resp.status_code = 201
                meeting_resp.json.return_value = {
                    'id': 12345678900,
                    'join_url': 'https://zoom.us/j/12345678900',
                }
                mock_post.side_effect = [token_resp, meeting_resp]

                response = self.client.post(
                    f'/studio/events/{self.event.pk}/create-zoom',
                )
                self.assertEqual(response.status_code, 200)
                self.event.refresh_from_db()
                self.assertEqual(self.event.zoom_meeting_id, '12345678900')
                self.assertEqual(
                    self.event.zoom_join_url, 'https://zoom.us/j/12345678900',
                )

    def test_create_zoom_already_has_meeting(self):
        self.event.zoom_meeting_id = 'existing-id'
        self.event.save(update_fields=['zoom_meeting_id'])
        response = self.client.post(
            f'/studio/events/{self.event.pk}/create-zoom',
        )
        self.assertEqual(response.status_code, 400)

    def test_create_zoom_nonexistent_event(self):
        response = self.client.post('/studio/events/99999/create-zoom')
        self.assertEqual(response.status_code, 404)

    def test_create_zoom_requires_post(self):
        response = self.client.get(
            f'/studio/events/{self.event.pk}/create-zoom',
        )
        self.assertEqual(response.status_code, 405)


class StudioEventDateTimeParsingTest(TestCase):
    """Test the _parse_event_datetime helper function directly.

    Issue #665: the parser now takes a TZ argument and returns
    timezone-aware UTC datetimes. The local wall-clock value is
    interpreted in that TZ before being converted.
    """

    def test_parse_in_utc_returns_aware_utc(self):
        from datetime import UTC
        from datetime import datetime as _dt

        from django.http import QueryDict

        from studio.views.events import _parse_event_datetime

        data = QueryDict(mutable=True)
        data['event_date'] = '15/03/2026'
        data['event_time'] = '14:30'
        data['duration_hours'] = '2'

        start_dt, end_dt = _parse_event_datetime(data, 'UTC')
        self.assertEqual(start_dt, _dt(2026, 3, 15, 14, 30, tzinfo=UTC))
        self.assertEqual(end_dt, _dt(2026, 3, 15, 16, 30, tzinfo=UTC))

    def test_parse_in_new_york_converts_to_utc(self):
        """14:30 in NYC on 15/06/2027 is 18:30 UTC (DST)."""
        from datetime import UTC
        from datetime import datetime as _dt

        from django.http import QueryDict

        from studio.views.events import _parse_event_datetime

        data = QueryDict(mutable=True)
        data['event_date'] = '15/06/2027'
        data['event_time'] = '14:30'
        data['duration_hours'] = '1'

        start_dt, end_dt = _parse_event_datetime(data, 'America/New_York')
        self.assertEqual(start_dt, _dt(2027, 6, 15, 18, 30, tzinfo=UTC))
        self.assertEqual(end_dt, _dt(2027, 6, 15, 19, 30, tzinfo=UTC))

    def test_parse_empty_duration_defaults_to_1_hour(self):
        from django.http import QueryDict

        from studio.views.events import _parse_event_datetime

        data = QueryDict(mutable=True)
        data['event_date'] = '20/06/2026'
        data['event_time'] = '09:00'
        data['duration_hours'] = ''

        start_dt, end_dt = _parse_event_datetime(data, 'UTC')
        self.assertEqual((end_dt - start_dt).total_seconds(), 3600)

    def test_parse_fractional_duration(self):
        from django.http import QueryDict

        from studio.views.events import _parse_event_datetime

        data = QueryDict(mutable=True)
        data['event_date'] = '01/01/2026'
        data['event_time'] = '10:00'
        data['duration_hours'] = '1.5'

        start_dt, end_dt = _parse_event_datetime(data, 'UTC')
        self.assertEqual((end_dt - start_dt).total_seconds(), 1.5 * 3600)


class StudioEventFormContextTest(TestCase):
    """Test the _event_form_context helper function."""

    def test_context_for_new_event(self):
        from studio.views.events import _event_form_context

        context = _event_form_context(None, 'UTC')
        self.assertEqual(context['event_date'], '')
        self.assertEqual(context['event_time'], '')
        self.assertEqual(context['duration_hours'], '1')

    def test_context_for_existing_event_renders_in_event_tz(self):
        """Stored UTC instant is rendered in event.timezone for the picker."""
        from datetime import UTC
        from datetime import datetime as _dt

        from studio.views.events import _event_form_context

        # 18:30 UTC on 2027-06-15 is 14:30 in America/New_York.
        event = Event.objects.create(
            title='Test', slug='test-ctx',
            start_datetime=_dt(2027, 6, 15, 18, 30, tzinfo=UTC),
            end_datetime=_dt(2027, 6, 15, 20, 0, tzinfo=UTC),
            timezone='America/New_York',
        )
        context = _event_form_context(event, 'UTC')
        self.assertEqual(context['event_date'], '15/06/2027')
        self.assertEqual(context['event_time'], '14:30')
        self.assertEqual(context['duration_hours'], '1.5')
        self.assertEqual(context['timezone_value'], 'America/New_York')

    def test_context_for_existing_event_without_end(self):
        from datetime import UTC
        from datetime import datetime as _dt

        from studio.views.events import _event_form_context

        event = Event.objects.create(
            title='Test', slug='test-ctx-no-end',
            start_datetime=_dt(2026, 6, 1, 10, 0, tzinfo=UTC),
            end_datetime=None,
            timezone='UTC',
        )
        context = _event_form_context(event, 'UTC')
        self.assertEqual(context['event_date'], '01/06/2026')
        self.assertEqual(context['event_time'], '10:00')
        self.assertEqual(context['duration_hours'], '1')

    def test_context_for_whole_number_duration(self):
        from datetime import UTC
        from datetime import datetime as _dt

        from studio.views.events import _event_form_context

        event = Event.objects.create(
            title='Test', slug='test-ctx-whole',
            start_datetime=_dt(2026, 6, 1, 10, 0, tzinfo=UTC),
            end_datetime=_dt(2026, 6, 1, 12, 0, tzinfo=UTC),
            timezone='UTC',
        )
        context = _event_form_context(event, 'UTC')
        self.assertEqual(context['duration_hours'], '2')
