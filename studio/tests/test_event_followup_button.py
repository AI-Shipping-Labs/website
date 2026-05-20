"""Studio "Send follow-up now" button tests (issue #680).

Coverage:

- The button is present and enabled when the event has ended AND
  has a recording URL.
- The button is disabled when the gate is unmet (no recording URL, or
  the event is still upcoming).
- POSTing to the send-followup endpoint enqueues the fan-out and
  flashes a success message.
- A second press flashes "already sent" without re-enqueuing.
- Gate failures (no recording / not past) flash an error and do
  NOT enqueue.
- The ``post_event_summary`` field is persisted via the edit POST.

Issue #713: gates are now time-derived. Fixtures use past timestamps
to mark an event as finished rather than (only) ``status='completed'``.
"""

from datetime import UTC, datetime, timedelta  # noqa: F401  (UTC, datetime used in summary fixtures)
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone as djtimezone

from events.models import Event, EventRegistration
from notifications.models import EventReminderLog

User = get_user_model()


def _past_start_end():
    """Return ``(start, end)`` with the event already finished."""
    now = djtimezone.now()
    return now - timedelta(hours=3), now - timedelta(hours=1)


class StaffMixin:
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff = User.objects.create_user(
            email='staff-followup@test.com', password='pass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff-followup@test.com', password='pass')


class StudioEventFormButtonStateTest(StaffMixin, TestCase):
    """The form template renders the right button state based on the gate."""

    def test_button_enabled_when_past_with_recording(self):
        start, end = _past_start_end()
        event = Event.objects.create(
            title='Recap Enabled',
            slug='recap-enabled',
            start_datetime=start,
            end_datetime=end,
            status='completed',
            recording_url='https://youtube.com/watch?v=enabled',
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertContains(response, 'data-testid="send-followup-button"')
        self.assertNotContains(response, 'data-testid="send-followup-button-disabled"')

    def test_button_disabled_when_no_recording(self):
        start, end = _past_start_end()
        event = Event.objects.create(
            title='Recap No Recording',
            slug='recap-no-rec',
            start_datetime=start,
            end_datetime=end,
            status='completed',
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertContains(response, 'data-testid="send-followup-button-disabled"')
        self.assertContains(response, 'Set a recording URL to enable.')

    def test_button_disabled_when_future(self):
        # Issue #713: gate is ``is_past``; a future event disables.
        event = Event.objects.create(
            title='Recap Future',
            slug='recap-future',
            start_datetime=djtimezone.now() + timedelta(hours=2),
            end_datetime=djtimezone.now() + timedelta(hours=3),
            status='upcoming',
            recording_url='https://youtube.com/watch?v=future',
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertContains(response, 'data-testid="send-followup-button-disabled"')
        self.assertContains(response, 'Available once the event has ended')

    def test_post_event_summary_field_rendered(self):
        event = Event.objects.create(
            title='Has Summary',
            slug='has-summary',
            start_datetime=datetime(2026, 6, 8, 16, 0, tzinfo=UTC),
            status='completed',
            recording_url='https://youtube.com/watch?v=has',
            post_event_summary='# Recap body\n\nThanks for joining.',
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertContains(response, 'data-testid="post-event-summary"')
        self.assertContains(response, '# Recap body')


class StudioSendFollowupEndpointTest(StaffMixin, TestCase):
    """POSTing to ``/studio/events/<id>/send-followup`` triggers the fan-out."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        start, end = _past_start_end()
        cls.event = Event.objects.create(
            title='Send Followup',
            slug='send-followup',
            start_datetime=start,
            end_datetime=end,
            status='completed',
            recording_url='https://youtube.com/watch?v=send',
        )
        cls.attendee = User.objects.create_user(email='attendee@test.com')

    def setUp(self):
        super().setUp()
        EventRegistration.objects.create(event=self.event, user=self.attendee)

    @patch(
        'studio.views.events.enqueue_post_event_followup',
    )
    def test_post_enqueues_fanout_and_flashes_success(self, mock_enq):
        response = self.client.post(
            f'/studio/events/{self.event.pk}/send-followup',
            follow=True,
        )
        mock_enq.assert_called_once_with(self.event.pk)
        # Redirects back to the edit page.
        self.assertEqual(response.status_code, 200)
        # Success message says how many attendees were queued.
        self.assertContains(response, 'Follow-up email queued for 1 attendee')

    @patch(
        'studio.views.events.enqueue_post_event_followup',
    )
    def test_second_press_flashes_already_sent(self, mock_enq):
        # Simulate a prior fan-out by persisting a log row.
        EventReminderLog.objects.create(
            event=self.event, user=self.attendee, interval='followup',
        )

        response = self.client.post(
            f'/studio/events/{self.event.pk}/send-followup',
            follow=True,
        )
        mock_enq.assert_not_called()
        self.assertContains(
            response,
            'A post-event follow-up has already been sent for this event.',
        )

    @patch(
        'studio.views.events.enqueue_post_event_followup',
    )
    def test_no_recording_returns_error(self, mock_enq):
        start, end = _past_start_end()
        bad_event = Event.objects.create(
            title='No Recording',
            slug='no-rec-event',
            start_datetime=start,
            end_datetime=end,
            status='completed',
        )
        response = self.client.post(
            f'/studio/events/{bad_event.pk}/send-followup',
            follow=True,
        )
        mock_enq.assert_not_called()
        self.assertContains(response, 'Set a recording URL before sending')

    @patch(
        'studio.views.events.enqueue_post_event_followup',
    )
    def test_not_past_returns_error(self, mock_enq):
        upcoming_event = Event.objects.create(
            title='Still Upcoming',
            slug='still-upcoming',
            start_datetime=djtimezone.now() + timedelta(hours=2),
            end_datetime=djtimezone.now() + timedelta(hours=3),
            status='upcoming',
            recording_url='https://youtube.com/watch?v=upcoming-evt',
        )
        response = self.client.post(
            f'/studio/events/{upcoming_event.pk}/send-followup',
            follow=True,
        )
        mock_enq.assert_not_called()
        self.assertContains(
            response,
            'Follow-up emails can only be sent after the event has ended.',
        )

    def test_get_method_not_allowed(self):
        response = self.client.get(
            f'/studio/events/{self.event.pk}/send-followup',
        )
        self.assertEqual(response.status_code, 405)

    def test_non_staff_forbidden(self):
        User.objects.create_user(email='plain@test.com', password='pass')
        client = Client()
        client.login(email='plain@test.com', password='pass')
        response = client.post(
            f'/studio/events/{self.event.pk}/send-followup',
        )
        self.assertEqual(response.status_code, 403)


class StudioEventEditPersistsPostEventSummaryTest(StaffMixin, TestCase):
    """POSTing the edit form writes ``post_event_summary`` back to the row."""

    def test_post_persists_summary(self):
        event = Event.objects.create(
            title='Persists Summary',
            slug='persists-summary',
            start_datetime=datetime(2026, 6, 8, 16, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 6, 8, 17, 0, tzinfo=UTC),
            status='completed',
            timezone='UTC',
            recording_url='https://youtube.com/watch?v=persists',
        )
        post_data = {
            'title': 'Persists Summary',
            'slug': 'persists-summary',
            'description': '',
            'status': 'completed',
            'platform': 'zoom',
            'external_host': '',
            'event_date': '08/06/2026',
            'event_time': '16:00',
            'duration_hours': '1',
            'timezone': 'UTC',
            'max_participants': '',
            'location': '',
            'required_level': '0',
            'tags': '',
            'post_event_summary': 'A new recap message.',
        }
        response = self.client.post(
            f'/studio/events/{event.pk}/edit', post_data,
        )
        self.assertEqual(response.status_code, 302)

        event.refresh_from_db()
        self.assertEqual(event.post_event_summary, 'A new recap message.')
