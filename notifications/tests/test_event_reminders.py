"""Tests for the event reminder background job.

All tests use freezegun to fix the clock so that time-window logic
(23h45m-24h15m and 45m-1h15m) is deterministic.
"""

from datetime import datetime, timedelta, timezone as dt_tz
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from freezegun import freeze_time

from events.models import Event, EventRegistration
from notifications.models import Notification, EventReminderLog
from notifications.services.event_reminders import check_event_reminders

User = get_user_model()

# Fix a reference time for all tests
FROZEN_NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=dt_tz.utc)


class CheckEventRemindersTest(TestCase):
    """Tests for the check_event_reminders background job.

    Every test freezes time to FROZEN_NOW so window calculations are exact.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='testuser@example.com', password='testpass123',
        )
        self.user2 = User.objects.create_user(
            email='testuser2@example.com', password='testpass123',
        )

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_24h_reminder_for_registered_users(self, mock_slack):
        """Events starting in ~24h should get reminders for registered users."""
        event = Event.objects.create(
            title='24h Event', slug='event-24h',
            start_datetime=FROZEN_NOW + timedelta(hours=24),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)
        EventRegistration.objects.create(event=event, user=self.user2)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 2)
        notif = Notification.objects.filter(user=self.user).first()
        self.assertIn('24 hours', notif.title)
        self.assertEqual(notif.notification_type, 'event_reminder')

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_1h_reminder_for_registered_users(self, mock_slack):
        """Events starting in ~1h should get reminders for registered users."""
        event = Event.objects.create(
            title='1h Event', slug='event-1h',
            start_datetime=FROZEN_NOW + timedelta(hours=1),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 1)
        notif = Notification.objects.first()
        self.assertIn('1 hour', notif.title)

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_no_reminder_for_unregistered_users(self, mock_slack):
        """Users who have not registered should not get reminders."""
        Event.objects.create(
            title='Event No Reg', slug='event-no-reg',
            start_datetime=FROZEN_NOW + timedelta(hours=24),
            status='upcoming',
        )
        # No registrations

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 0)

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_no_reminder_for_draft_events(self, mock_slack):
        """Draft events should not trigger reminders."""
        event = Event.objects.create(
            title='Draft Event', slug='event-draft',
            start_datetime=FROZEN_NOW + timedelta(hours=24),
            status='draft',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 0)

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_deduplication_no_double_reminders(self, mock_slack):
        """Running the job twice should not create duplicate reminders."""
        event = Event.objects.create(
            title='Dedup Event', slug='event-dedup',
            start_datetime=FROZEN_NOW + timedelta(hours=24),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()
        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(EventReminderLog.objects.count(), 1)

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_event_outside_window_gets_no_reminder(self, mock_slack):
        """Events not in the 24h or 1h window should not get reminders."""
        # Event in 12 hours - outside both windows
        event = Event.objects.create(
            title='12h Event', slug='event-12h',
            start_datetime=FROZEN_NOW + timedelta(hours=12),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 0)

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_24h_reminder_posts_to_slack(self, mock_slack):
        """24h reminders should post to Slack."""
        event = Event.objects.create(
            title='Slack Event', slug='event-slack',
            start_datetime=FROZEN_NOW + timedelta(hours=24),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        mock_slack.assert_called_once_with('event', event)

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_1h_reminder_does_not_post_to_slack(self, mock_slack):
        """1h reminders should NOT post to Slack (per spec: avoid noise)."""
        event = Event.objects.create(
            title='1h Event No Slack', slug='event-1h-no-slack',
            start_datetime=FROZEN_NOW + timedelta(hours=1),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        mock_slack.assert_not_called()

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_event_at_edge_of_24h_window_start(self, mock_slack):
        """Event at exactly 23h45m from now is inside the 24h window."""
        event = Event.objects.create(
            title='Edge Start', slug='event-edge-start',
            start_datetime=FROZEN_NOW + timedelta(hours=23, minutes=45),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 1)

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_event_just_outside_24h_window(self, mock_slack):
        """Event at 23h44m from now is outside the 24h window."""
        event = Event.objects.create(
            title='Outside Window', slug='event-outside',
            start_datetime=FROZEN_NOW + timedelta(hours=23, minutes=44),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 0)

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_event_at_edge_of_1h_window_end(self, mock_slack):
        """Event at exactly 1h15m from now is inside the 1h window."""
        event = Event.objects.create(
            title='1h Edge End', slug='event-1h-edge',
            start_datetime=FROZEN_NOW + timedelta(hours=1, minutes=15),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 1)

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_event_just_outside_1h_window(self, mock_slack):
        """Event at 1h16m from now is outside the 1h window."""
        event = Event.objects.create(
            title='1h Outside', slug='event-1h-outside',
            start_datetime=FROZEN_NOW + timedelta(hours=1, minutes=16),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 0)
