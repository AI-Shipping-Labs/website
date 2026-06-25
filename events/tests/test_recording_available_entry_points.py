"""Entry-point tests for the recording-available campaign (issue #1076).

Covers the two surfaces that deep-link to the pre-filled draft campaign:

- the host recording-ready email CTA (``event_recording_ready.md``);
- the Studio event-edit page button.

Neither sends — both only link to the draft create flow for review.
"""

from datetime import datetime
from datetime import timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag

from events.models import Event
from events.services.recording_ready_notification import _build_context

User = get_user_model()
UTC = dt_timezone.utc


@tag('core')
class RecordingReadyEmailCtaTest(TestCase):
    def test_context_includes_campaign_prefill_url(self):
        event = Event.objects.create(
            title='Agents Workshop', slug='agents-workshop',
            start_datetime=datetime(2026, 6, 8, 16, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 6, 8, 17, 0, tzinfo=UTC),
            status='completed',
            recording_url='https://youtube.com/watch?v=agents',
        )
        context = _build_context(event)
        self.assertIn('campaign_prefill_url', context)
        self.assertIn(
            f'/studio/campaigns/new?event={event.pk}'
            '&template=recording_available',
            context['campaign_prefill_url'],
        )


@tag('core')
class RecordingAvailableEventButtonTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff-rec@test.com', password='pw', is_staff=True,
        )
        cls.event = Event.objects.create(
            title='Recap Event', slug='recap-event',
            start_datetime=datetime(2026, 6, 8, 16, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 6, 8, 17, 0, tzinfo=UTC),
            status='completed',
            recording_url='https://youtube.com/watch?v=recap',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff-rec@test.com', password='pw')

    def test_event_edit_page_shows_recording_campaign_button(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(
            response, 'data-testid="email-registrants-recording-button"',
        )
        self.assertContains(response, 'Email registrants: recording available')
        # Distinct from the transactional follow-up button.
        self.assertContains(response, 'data-testid="send-followup-button"')

    def test_button_links_to_prefill_flow(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(
            response,
            f'/studio/campaigns/new?event={self.event.pk}'
            '&amp;template=recording_available',
        )
