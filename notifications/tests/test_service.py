"""Tests for NotificationService.notify() and event reminders."""

from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from content.models import Article, Course, Recording, Download
from events.models import Event, EventRegistration
from notifications.models import Notification, EventReminderLog
from notifications.services.notification_service import NotificationService
from voting.models import Poll

User = get_user_model()


class NotificationServiceNotifyTest(TestCase):
    """Tests for NotificationService.notify()."""

    def setUp(self):
        from payments.models import Tier

        self.free_tier = Tier.objects.get(slug='free')
        self.basic_tier = Tier.objects.get(slug='basic')
        self.main_tier = Tier.objects.get(slug='main')

        # Create users with different tiers
        self.free_user = User.objects.create_user(
            email='free@example.com', password='test123',
        )
        self.free_user.tier = self.free_tier
        self.free_user.save()

        self.basic_user = User.objects.create_user(
            email='basic@example.com', password='test123',
        )
        self.basic_user.tier = self.basic_tier
        self.basic_user.save()

        self.main_user = User.objects.create_user(
            email='main@example.com', password='test123',
        )
        self.main_user.tier = self.main_tier
        self.main_user.save()

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_notify_article_creates_notifications_for_all_users(self, mock_slack):
        article = Article.objects.create(
            title='Test Article', slug='test-article',
            date=date(2025, 1, 1), published=True,
            required_level=0,
        )
        NotificationService.notify('article', article.pk)
        # All 3 users should get a notification (level 0 = open)
        self.assertEqual(Notification.objects.count(), 3)

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_notify_article_with_level_filter(self, mock_slack):
        article = Article.objects.create(
            title='Basic Article', slug='basic-article',
            date=date(2025, 1, 1), published=True,
            required_level=10,  # Basic and above
        )
        NotificationService.notify('article', article.pk)
        # Only basic and main users should get notifications
        self.assertEqual(Notification.objects.count(), 2)
        users_notified = set(
            Notification.objects.values_list('user__email', flat=True),
        )
        self.assertIn('basic@example.com', users_notified)
        self.assertIn('main@example.com', users_notified)
        self.assertNotIn('free@example.com', users_notified)

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_notify_article_notification_fields(self, mock_slack):
        article = Article.objects.create(
            title='Test Article', slug='test-article',
            date=date(2025, 1, 1), published=True,
            description='This is a test article description.',
            required_level=0,
        )
        NotificationService.notify('article', article.pk)
        n = Notification.objects.filter(user=self.free_user).first()
        self.assertIn('Test Article', n.title)
        self.assertEqual(n.body, 'This is a test article description.')
        self.assertEqual(n.url, '/blog/test-article')
        self.assertEqual(n.notification_type, 'new_content')
        self.assertFalse(n.read)

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_notify_course(self, mock_slack):
        course = Course.objects.create(
            title='Test Course', slug='test-course',
            status='published', required_level=0,
        )
        NotificationService.notify('course', course.pk)
        self.assertEqual(Notification.objects.count(), 3)
        n = Notification.objects.first()
        self.assertIn('Test Course', n.title)

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_notify_event(self, mock_slack):
        event = Event.objects.create(
            title='Test Event', slug='test-event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming', required_level=0,
        )
        NotificationService.notify('event', event.pk)
        self.assertEqual(Notification.objects.count(), 3)

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_notify_recording(self, mock_slack):
        recording = Recording.objects.create(
            title='Test Recording', slug='test-recording',
            date=date(2025, 1, 1), published=True,
            required_level=0,
        )
        NotificationService.notify('recording', recording.pk)
        self.assertEqual(Notification.objects.count(), 3)

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_notify_download(self, mock_slack):
        download = Download.objects.create(
            title='Test Download', slug='test-download',
            file_url='https://example.com/file.pdf',
            published=True, required_level=0,
        )
        NotificationService.notify('download', download.pk)
        self.assertEqual(Notification.objects.count(), 3)

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_notify_poll(self, mock_slack):
        poll = Poll.objects.create(
            title='Test Poll', status='open',
        )
        NotificationService.notify('poll', poll.pk)
        # Poll required_level is auto-set to 20 (main) for topic type
        users_notified = set(
            Notification.objects.values_list('user__email', flat=True),
        )
        self.assertIn('main@example.com', users_notified)

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_notify_calls_slack_announcement(self, mock_slack):
        article = Article.objects.create(
            title='Slack Article', slug='slack-article',
            date=date(2025, 1, 1), published=True,
            required_level=0,
        )
        NotificationService.notify('article', article.pk)
        mock_slack.assert_called_once_with('article', article)

    def test_notify_unknown_content_type_does_not_crash(self):
        """Unknown content types should be handled gracefully."""
        NotificationService.notify('unknown_type', 1)
        self.assertEqual(Notification.objects.count(), 0)

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_notify_nonexistent_content_does_not_crash(self, mock_slack):
        """Non-existent content IDs should be handled gracefully."""
        NotificationService.notify('article', 99999)
        self.assertEqual(Notification.objects.count(), 0)


class EventReminderServiceTest(TestCase):
    """Tests for NotificationService.create_event_reminder()."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='testuser@example.com', password='testpass123',
        )
        self.event = Event.objects.create(
            title='Test Event', slug='test-event-reminder',
            start_datetime=timezone.now() + timedelta(hours=24),
            status='upcoming',
        )

    def test_create_event_reminder_creates_notification(self):
        result = NotificationService.create_event_reminder(
            event=self.event, user=self.user, interval='24h',
            title='Reminder: Test Event',
            body='Starts in 24 hours.',
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.notification_type, 'event_reminder')
        self.assertEqual(result.title, 'Reminder: Test Event')

    def test_create_event_reminder_creates_log(self):
        NotificationService.create_event_reminder(
            event=self.event, user=self.user, interval='24h',
            title='Reminder', body='Test',
        )
        self.assertEqual(EventReminderLog.objects.count(), 1)
        log = EventReminderLog.objects.first()
        self.assertEqual(log.event, self.event)
        self.assertEqual(log.user, self.user)
        self.assertEqual(log.interval, '24h')

    def test_duplicate_reminder_returns_none(self):
        NotificationService.create_event_reminder(
            event=self.event, user=self.user, interval='24h',
            title='First', body='First',
        )
        result = NotificationService.create_event_reminder(
            event=self.event, user=self.user, interval='24h',
            title='Second', body='Second',
        )
        self.assertIsNone(result)
        # Only one notification should exist
        self.assertEqual(Notification.objects.count(), 1)

    def test_different_interval_creates_new_reminder(self):
        NotificationService.create_event_reminder(
            event=self.event, user=self.user, interval='24h',
            title='24h', body='24h',
        )
        result = NotificationService.create_event_reminder(
            event=self.event, user=self.user, interval='1h',
            title='1h', body='1h',
        )
        self.assertIsNotNone(result)
        self.assertEqual(Notification.objects.count(), 2)
