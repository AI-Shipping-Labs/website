"""Studio "Send follow-up now" button tests (issue #680).

Coverage:

- The button is present and enabled when the event is completed AND
  has a recording URL.
- The button is disabled when the gate is unmet (no recording URL, or
  status != completed).
- POSTing to the send-followup endpoint enqueues the fan-out and
  flashes a success message.
- A second press flashes "already sent" without re-enqueuing.
- Gate failures (no recording / not completed) flash an error and do
  NOT enqueue.
- The ``post_event_summary`` field is persisted via the edit POST.
"""

from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from events.models import Event, EventRegistration
from notifications.models import EventReminderLog

User = get_user_model()


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

    def test_button_enabled_when_completed_with_recording(self):
        event = Event.objects.create(
            title='Recap Enabled',
            slug='recap-enabled',
            start_datetime=datetime(2026, 6, 8, 16, 0, tzinfo=UTC),
            status='completed',
            recording_url='https://youtube.com/watch?v=enabled',
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertContains(response, 'data-testid="send-followup-button"')
        self.assertNotContains(response, 'data-testid="send-followup-button-disabled"')

    def test_button_disabled_when_no_recording(self):
        event = Event.objects.create(
            title='Recap No Recording',
            slug='recap-no-rec',
            start_datetime=datetime(2026, 6, 8, 16, 0, tzinfo=UTC),
            status='completed',
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertContains(response, 'data-testid="send-followup-button-disabled"')
        self.assertContains(response, 'Set a recording URL to enable.')

    def test_button_disabled_when_not_completed(self):
        event = Event.objects.create(
            title='Recap Upcoming',
            slug='recap-upcoming',
            start_datetime=datetime(2026, 6, 8, 16, 0, tzinfo=UTC),
            status='upcoming',
            recording_url='https://youtube.com/watch?v=upcoming',
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertContains(response, 'data-testid="send-followup-button-disabled"')
        self.assertContains(response, 'Available once the event status is Completed.')

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
        cls.event = Event.objects.create(
            title='Send Followup',
            slug='send-followup',
            start_datetime=datetime(2026, 6, 8, 16, 0, tzinfo=UTC),
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
        bad_event = Event.objects.create(
            title='No Recording',
            slug='no-rec-event',
            start_datetime=datetime(2026, 6, 8, 16, 0, tzinfo=UTC),
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
    def test_not_completed_returns_error(self, mock_enq):
        upcoming_event = Event.objects.create(
            title='Still Upcoming',
            slug='still-upcoming',
            start_datetime=datetime(2026, 6, 8, 16, 0, tzinfo=UTC),
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
            'Follow-up emails can only be sent for completed events.',
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
