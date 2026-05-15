"""Tests for NotificationService.notify() and event reminders."""

from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from content.models import Article, Course, Download, Workshop
from events.models import Event
from notifications.models import EventReminderLog, Notification
from notifications.services.notification_service import NotificationService
from voting.models import Poll

User = get_user_model()


@tag('core')
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
        recording = Event.objects.create(
            title='Test Recording', slug='test-recording',
            start_datetime=timezone.now(), status='completed',
            recording_url='https://youtube.com/watch?v=test',
            published=True, required_level=0,
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

    # --- Workshop notification tests (issue #647) ---

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_notify_workshop_creates_notifications_for_all_users_when_open(
        self, mock_slack,
    ):
        """A published workshop with landing_required_level=0 notifies every
        active user (free + basic + main)."""
        workshop = Workshop.objects.create(
            title='Build a RAG App', slug='build-a-rag-app',
            date=date(2026, 1, 1), status='published',
            landing_required_level=0,
            pages_required_level=10,
            recording_required_level=20,
        )
        NotificationService.notify('workshop', workshop.pk)
        self.assertEqual(Notification.objects.count(), 3)

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_notify_workshop_with_basic_landing_filters_out_free(
        self, mock_slack,
    ):
        """A workshop with landing_required_level=10 notifies basic + main
        but NOT free."""
        workshop = Workshop.objects.create(
            title='Basic Landing Workshop', slug='basic-landing-workshop',
            date=date(2026, 1, 1), status='published',
            landing_required_level=10,
            pages_required_level=10,
            recording_required_level=20,
        )
        NotificationService.notify('workshop', workshop.pk)

        users_notified = set(
            Notification.objects.values_list('user__email', flat=True),
        )
        self.assertIn('basic@example.com', users_notified)
        self.assertIn('main@example.com', users_notified)
        self.assertNotIn('free@example.com', users_notified)

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_notify_workshop_with_main_landing_filters_out_free_and_basic(
        self, mock_slack,
    ):
        """A workshop with landing_required_level=20 notifies main only."""
        workshop = Workshop.objects.create(
            title='Main Only Workshop', slug='main-only-workshop',
            date=date(2026, 1, 1), status='published',
            landing_required_level=20,
            pages_required_level=20,
            recording_required_level=20,
        )
        NotificationService.notify('workshop', workshop.pk)

        users_notified = set(
            Notification.objects.values_list('user__email', flat=True),
        )
        self.assertEqual(users_notified, {'main@example.com'})

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_notify_workshop_uses_workshop_url(self, mock_slack):
        """Notification.url is /workshops/<slug> and title starts with
        'New workshop:'."""
        workshop = Workshop.objects.create(
            title='URL Workshop', slug='url-workshop',
            date=date(2026, 1, 1), status='published',
            landing_required_level=0,
            pages_required_level=10,
            recording_required_level=20,
        )
        NotificationService.notify('workshop', workshop.pk)
        n = Notification.objects.filter(user=self.free_user).first()
        self.assertEqual(n.url, '/workshops/url-workshop')
        self.assertEqual(n.title, 'New workshop: URL Workshop')
        self.assertEqual(n.notification_type, 'new_content')
        self.assertFalse(n.read)

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_notify_workshop_uses_workshop_description_for_body(
        self, mock_slack,
    ):
        """Notification.body is the first 200 chars of Workshop.description."""
        long_desc = 'A great workshop. ' * 30  # > 200 chars
        workshop = Workshop.objects.create(
            title='Desc Workshop', slug='desc-workshop',
            date=date(2026, 1, 1), status='published',
            description=long_desc,
            landing_required_level=0,
            pages_required_level=10,
            recording_required_level=20,
        )
        NotificationService.notify('workshop', workshop.pk)
        n = Notification.objects.filter(user=self.free_user).first()
        self.assertEqual(n.body, long_desc[:200])
        self.assertEqual(len(n.body), 200)

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_notify_workshop_calls_slack_announcement(self, mock_slack):
        """post_slack_announcement is called once with ('workshop', workshop)."""
        workshop = Workshop.objects.create(
            title='Slack Workshop', slug='slack-workshop',
            date=date(2026, 1, 1), status='published',
            landing_required_level=0,
            pages_required_level=10,
            recording_required_level=20,
        )
        NotificationService.notify('workshop', workshop.pk)
        mock_slack.assert_called_once_with('workshop', workshop)

    def test_notify_workshop_nonexistent_id_does_not_crash(self):
        """NotificationService.notify('workshop', 99999) logs and creates
        zero notifications."""
        with self.assertLogs(
            'notifications.services.notification_service',
            level='ERROR',
        ) as logs:
            NotificationService.notify('workshop', 99999)
        self.assertIn(
            'Failed to load content for notify: workshop/99999',
            logs.output[0],
        )
        self.assertEqual(Notification.objects.count(), 0)

    def test_notify_unknown_content_type_does_not_crash(self):
        """Unknown content types should be handled gracefully."""
        NotificationService.notify('unknown_type', 1)
        self.assertEqual(Notification.objects.count(), 0)

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_notify_nonexistent_content_does_not_crash(self, mock_slack):
        """Non-existent content IDs should be handled gracefully."""
        with self.assertLogs(
            'notifications.services.notification_service',
            level='ERROR',
        ) as logs:
            NotificationService.notify('article', 99999)
        self.assertIn('Failed to load content for notify: article/99999', logs.output[0])
        self.assertEqual(Notification.objects.count(), 0)

    # Replaces playwright_tests/test_notifications.py::
    # TestScenario5NotificationOnArticlePublish::
    # test_publish_creates_notification_for_eligible_not_ineligible
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_publish_basic_article_notifies_basic_not_free(self, mock_slack):
        """A required_level=10 article notifies basic+main but not free."""
        article = Article.objects.create(
            title='Exclusive Basic Article',
            slug='exclusive-basic-article',
            description='This article is for Basic and above.',
            date=date(2025, 1, 1),
            published=True,
            required_level=10,
        )
        NotificationService.notify('article', article.pk)

        # Basic user got it
        basic_notif = Notification.objects.filter(user=self.basic_user).first()
        self.assertIsNotNone(basic_notif)
        self.assertIn('Exclusive Basic Article', basic_notif.title)
        self.assertEqual(basic_notif.url, '/blog/exclusive-basic-article')

        # Free user did not
        self.assertFalse(
            Notification.objects.filter(user=self.free_user).exists(),
        )

    # Replaces playwright_tests/test_notifications.py::
    # TestScenario8NotificationLinksToCorrectContent::
    # test_notifications_link_to_correct_content_types
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_notification_url_matches_content_type(self, mock_slack):
        """Each content type produces a notification.url for its detail page."""
        from datetime import timedelta

        article = Article.objects.create(
            title='Article For Main', slug='article-for-main',
            date=date(2025, 1, 1), published=True, required_level=0,
        )
        course = Course.objects.create(
            title='Course For Main', slug='course-for-main',
            status='published', required_level=0,
        )
        event = Event.objects.create(
            title='Event For Main', slug='event-for-main',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming', required_level=0,
        )
        download = Download.objects.create(
            title='Download For Main', slug='download-for-main',
            file_url='https://example.com/file.pdf',
            published=True, required_level=0,
        )

        NotificationService.notify('article', article.pk)
        NotificationService.notify('course', course.pk)
        NotificationService.notify('event', event.pk)
        NotificationService.notify('download', download.pk)

        urls_by_title = dict(
            Notification.objects.filter(user=self.main_user)
            .values_list('title', 'url'),
        )
        self.assertEqual(
            urls_by_title['New article: Article For Main'],
            '/blog/article-for-main',
        )
        self.assertEqual(
            urls_by_title['New course: Course For Main'],
            '/courses/course-for-main',
        )
        self.assertEqual(
            urls_by_title['Upcoming event: Event For Main'],
            '/events/event-for-main',
        )
        self.assertEqual(
            urls_by_title['New download: Download For Main'],
            '/downloads/download-for-main',
        )


@tag('core')
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
