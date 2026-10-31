"""Calendar invites are sent from content@, not the transactional noreply@.

Gmail only auto-adds an invite when the organizer is a sender the invitee has
interacted with; noreply@ is not, so invites silently stay out of calendars.
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from email_app.services.email_classification import relay_sender_id
from events.models import Event, EventRegistration
from events.services.calendar_invite import generate_ics
from events.services.registration_email import send_registration_confirmation


class CalendarInviteSenderTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='invitee@example.com')
        cls.event = Event.objects.create(
            title='Sender Workshop', slug='sender-workshop',
            start_datetime=timezone.now() + timezone.timedelta(days=3),
            status='upcoming',
        )
        cls.registration = EventRegistration.objects.create(
            event=cls.event, user=cls.user,
        )

    def test_ics_organizer_is_content_mailbox(self):
        ics = generate_ics(self.event).decode()
        self.assertIn('mailto:content@aishippinglabs.com', ics)
        self.assertNotIn('noreply@', ics)

    @override_settings(RELAY_TRANSACTIONAL_FROM_EMAIL='noreply@aishippinglabs.com')
    @patch('events.services.registration_email.send_relay_transactional')
    def test_registration_invite_requests_content_sender(self, mock_send):
        mock_send.return_value.message_id = 'relay-1'

        send_registration_confirmation(self.registration)

        self.assertEqual(mock_send.call_args.kwargs['sender_id'], 'content')

    def test_relay_sender_id_from_display_address(self):
        self.assertEqual(
            relay_sender_id('AI Shipping Labs <Content@aishippinglabs.com>'),
            'content',
        )
