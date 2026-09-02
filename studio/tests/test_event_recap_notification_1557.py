"""Studio control panel and explicit action for event recap notices."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag
from django.utils import timezone

from events.models import Event, EventRegistration

User = get_user_model()


@tag('core')
class StudioEventRecapNotificationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.staff = User.objects.create_user(
            email='recap-studio-staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='recap-studio-member@test.com', email_verified=True,
        )
        cls.event = Event.objects.create(
            title='Studio Recap Event',
            slug='studio-recap-event',
            description='A Studio event.',
            start_datetime=now - timedelta(hours=3),
            end_datetime=now - timedelta(hours=1),
            status='completed',
            published=True,
            recap_notes='## Recap\n\nStudio notes.',
        )
        EventRegistration.objects.create(event=cls.event, user=cls.member)

    def setUp(self):
        self.client = Client()
        self.client.login(
            email='recap-studio-staff@test.com', password='pw',
        )

    def test_edit_page_shows_public_link_counts_and_explicit_button(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')

        self.assertContains(response, 'data-testid="recap-ready-panel"')
        self.assertContains(response, 'data-testid="notify-recap-ready-button"')
        self.assertContains(response, 'data-testid="recap-ready-eligible-count">1<')
        self.assertContains(
            response,
            f'https://aishippinglabs.com{self.event.get_recap_url()}',
        )
        self.assertContains(
            response,
            'saving the recap never sends automatically',
        )

    def test_edit_page_disables_button_until_event_has_ended(self):
        future = Event.objects.create(
            title='Future Recap Event',
            slug='future-recap-event',
            description='Not ended yet.',
            start_datetime=timezone.now() + timedelta(hours=1),
            end_datetime=timezone.now() + timedelta(hours=2),
            status='upcoming',
            published=True,
            recap_notes='## Future recap',
        )

        response = self.client.get(f'/studio/events/{future.pk}/edit')

        self.assertContains(
            response,
            'data-testid="notify-recap-ready-button-disabled"',
        )
        self.assertContains(response, 'event has ended')

    @patch('studio.views.events.notify_recap_ready')
    def test_post_action_flashes_delivery_counts_and_returns_to_panel(
        self, mock_notify,
    ):
        mock_notify.return_value = {
            'emailed': 1,
            'notified': 1,
            'already_sent': 0,
            'skipped_inactive': 0,
            'failed': 0,
        }

        response = self.client.post(
            f'/studio/events/{self.event.pk}/notify-recap-ready',
        )

        self.assertEqual(response.status_code, 302)
        mock_notify.assert_called_once_with(self.event, actor=self.staff)
        self.assertEqual(mock_notify.call_args.kwargs['actor'], self.staff)
        self.assertTrue(response['Location'].endswith('#recap-ready-panel'))
        follow_response = self.client.get(response['Location'])
        self.assertContains(follow_response, 'Recap-ready notification complete')
        self.assertContains(follow_response, '1 emailed')
