"""Tests for the event reminder background job."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from events.models import Event, EventRegistration
from notifications.models import Notification, EventReminderLog
from notifications.services.event_reminders import check_event_reminders

User = get_user_model()


class CheckEventRemindersTest(TestCase):
    """Tests for the check_event_reminders background job."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='testuser@example.com', password='testpass123',
        )
        self.user2 = User.objects.create_user(
            email='testuser2@example.com', password='testpass123',
        )

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_24h_reminder_for_registered_users(self, mock_slack):
        """Events starting in ~24h should get reminders for registered users."""
        event = Event.objects.create(
            title='24h Event', slug='event-24h',
            start_datetime=timezone.now() + timedelta(hours=24),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)
        EventRegistration.objects.create(event=event, user=self.user2)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 2)
        notif = Notification.objects.filter(user=self.user).first()
        self.assertIn('24 hours', notif.title)
        self.assertEqual(notif.notification_type, 'event_reminder')

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_1h_reminder_for_registered_users(self, mock_slack):
        """Events starting in ~1h should get reminders for registered users."""
        event = Event.objects.create(
            title='1h Event', slug='event-1h',
            start_datetime=timezone.now() + timedelta(hours=1),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 1)
        notif = Notification.objects.first()
        self.assertIn('1 hour', notif.title)

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_no_reminder_for_unregistered_users(self, mock_slack):
        """Users who have not registered should not get reminders."""
        event = Event.objects.create(
            title='Event No Reg', slug='event-no-reg',
            start_datetime=timezone.now() + timedelta(hours=24),
            status='upcoming',
        )
        # No registrations

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 0)

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_no_reminder_for_draft_events(self, mock_slack):
        """Draft events should not trigger reminders."""
        event = Event.objects.create(
            title='Draft Event', slug='event-draft',
            start_datetime=timezone.now() + timedelta(hours=24),
            status='draft',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 0)

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_deduplication_no_double_reminders(self, mock_slack):
        """Running the job twice should not create duplicate reminders."""
        event = Event.objects.create(
            title='Dedup Event', slug='event-dedup',
            start_datetime=timezone.now() + timedelta(hours=24),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()
        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(EventReminderLog.objects.count(), 1)

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_event_outside_window_gets_no_reminder(self, mock_slack):
        """Events not in the 24h or 1h window should not get reminders."""
        # Event in 12 hours - outside both windows
        event = Event.objects.create(
            title='12h Event', slug='event-12h',
            start_datetime=timezone.now() + timedelta(hours=12),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 0)

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_24h_reminder_posts_to_slack(self, mock_slack):
        """24h reminders should post to Slack."""
        event = Event.objects.create(
            title='Slack Event', slug='event-slack',
            start_datetime=timezone.now() + timedelta(hours=24),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        mock_slack.assert_called_once_with('event', event)

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_1h_reminder_does_not_post_to_slack(self, mock_slack):
        """1h reminders should NOT post to Slack (per spec: avoid noise)."""
        event = Event.objects.create(
            title='1h Event No Slack', slug='event-1h-no-slack',
            start_datetime=timezone.now() + timedelta(hours=1),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        mock_slack.assert_not_called()
