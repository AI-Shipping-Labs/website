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
class NotificationServiceWorkshopEmailTest(TestCase):
    """Tests for the workshop email channel (issue #655).

    The channel reuses ``EmailService.send`` so we stub ``_send_ses`` and
    let the real ``EmailLog`` rows land in the database. That gives us
    accurate behaviour for the per-user skip path (globally unsubscribed
    promotional recipients return ``None`` from ``send``) without making
    real SES API calls.
    """

    def setUp(self):
        from payments.models import Tier

        self.free_tier = Tier.objects.get(slug='free')

        self.user1 = User.objects.create_user(
            email='user1@example.com', password='test123',
        )
        self.user1.tier = self.free_tier
        self.user1.email_verified = True
        self.user1.save()

        self.user2 = User.objects.create_user(
            email='user2@example.com', password='test123',
        )
        self.user2.tier = self.free_tier
        self.user2.email_verified = True
        self.user2.save()

        self.user3 = User.objects.create_user(
            email='user3@example.com', password='test123',
        )
        self.user3.tier = self.free_tier
        self.user3.email_verified = True
        self.user3.save()

        self.workshop = Workshop.objects.create(
            title='Build a RAG App', slug='build-a-rag-app',
            date=date(2026, 1, 1), status='published',
            description='Hands-on workshop',
            landing_required_level=0,
            pages_required_level=10,
            recording_required_level=20,
        )

    def _make_workshop(self, slug='another-workshop', title='Another'):
        return Workshop.objects.create(
            title=title, slug=slug,
            date=date(2026, 2, 1), status='published',
            description='Another hands-on workshop',
            landing_required_level=0,
            pages_required_level=10,
            recording_required_level=20,
        )

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    @patch('email_app.services.email_service.EmailService._send_ses',
           return_value='ses-msg-001')
    def test_notify_workshop_returns_notified_and_emailed_counts(
        self, mock_ses, mock_slack,
    ):
        result = NotificationService.notify('workshop', self.workshop.pk)

        self.assertEqual(result, {'notified': 3, 'emailed': 3})
        from email_app.models import EmailLog

        self.assertEqual(
            EmailLog.objects.filter(
                email_type='workshop_announcement',
            ).count(),
            3,
        )

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    @patch('email_app.services.email_service.EmailService._send_ses',
           return_value='ses-msg-002')
    def test_notify_workshop_excludes_users_who_opted_out_of_workshop_emails(
        self, mock_ses, mock_slack,
    ):
        self.user2.email_preferences = {'workshop_emails': False}
        self.user2.save(update_fields=['email_preferences'])

        result = NotificationService.notify('workshop', self.workshop.pk)

        self.assertEqual(result['notified'], 3)
        self.assertEqual(result['emailed'], 2)

        from email_app.models import EmailLog
        emailed_users = set(
            EmailLog.objects.filter(
                email_type='workshop_announcement',
            ).values_list('user__email', flat=True),
        )
        self.assertNotIn('user2@example.com', emailed_users)
        self.assertEqual(emailed_users, {'user1@example.com', 'user3@example.com'})

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    @patch('email_app.services.email_service.EmailService._send_ses',
           return_value='ses-msg-003')
    def test_notify_workshop_excludes_globally_unsubscribed_users_from_email(
        self, mock_ses, mock_slack,
    ):
        self.user2.unsubscribed = True
        self.user2.save(update_fields=['unsubscribed'])

        result = NotificationService.notify('workshop', self.workshop.pk)

        self.assertEqual(result['notified'], 3)
        self.assertEqual(result['emailed'], 2)

        # The unsubscribed user still receives the bell notification.
        self.assertTrue(
            Notification.objects.filter(user=self.user2).exists(),
        )

        from email_app.models import EmailLog
        emailed_users = set(
            EmailLog.objects.filter(
                email_type='workshop_announcement',
            ).values_list('user__email', flat=True),
        )
        self.assertNotIn('user2@example.com', emailed_users)

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    @patch('email_app.services.email_service.EmailService._send_ses',
           return_value='ses-msg-004')
    def test_notify_workshop_excludes_unverified_users_from_email(
        self, mock_ses, mock_slack,
    ):
        self.user2.email_verified = False
        self.user2.save(update_fields=['email_verified'])

        result = NotificationService.notify('workshop', self.workshop.pk)

        # Bell notification still fires for the unverified user.
        self.assertEqual(result['notified'], 3)
        self.assertEqual(result['emailed'], 2)
        self.assertTrue(
            Notification.objects.filter(user=self.user2).exists(),
        )

        from email_app.models import EmailLog
        self.assertFalse(
            EmailLog.objects.filter(
                user=self.user2,
                email_type='workshop_announcement',
            ).exists(),
        )

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_notify_workshop_email_send_failure_does_not_stop_loop(
        self, mock_slack,
    ):
        """A raise from one EmailService.send call must not halt the
        loop -- the other two users still get their email and
        ``emailed`` reports the two successful sends."""
        from email_app.services.email_service import EmailService

        original_send = EmailService.send

        def flaky_send(self, user, template_name, context=None):
            if user.email == 'user2@example.com':
                raise RuntimeError('Simulated SES outage for user2')
            return original_send(self, user, template_name, context)

        with patch(
            'email_app.services.email_service.EmailService._send_ses',
            return_value='ses-msg-005',
        ), patch.object(EmailService, 'send', flaky_send), self.assertLogs(
            'notifications.services.notification_service',
            level='WARNING',
        ) as logs:
            result = NotificationService.notify('workshop', self.workshop.pk)

        self.assertEqual(result['notified'], 3)
        self.assertEqual(result['emailed'], 2)
        self.assertTrue(
            any('user2@example.com' in line for line in logs.output),
            f'Expected WARNING with user2 email; got {logs.output!r}',
        )

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_notify_workshop_email_context_includes_title_description_and_deep_link(
        self, mock_slack,
    ):
        from email_app.services.email_service import EmailService

        captured = []

        def capture_send(self, user, template_name, context=None):
            captured.append((user, template_name, context))
            return None  # Skip actual rendering / EmailLog write.

        with patch.object(EmailService, 'send', capture_send):
            NotificationService.notify('workshop', self.workshop.pk)

        self.assertEqual(len(captured), 3)
        for _user, template_name, context in captured:
            self.assertEqual(template_name, 'workshop_announcement')
            self.assertEqual(context['workshop_title'], 'Build a RAG App')
            self.assertEqual(context['workshop_slug'], 'build-a-rag-app')
            self.assertIn('Hands-on workshop', context['workshop_description'])
            self.assertEqual(context['workshop_url'], '/workshops/build-a-rag-app')

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_notify_non_workshop_content_types_do_not_send_emails(
        self, mock_slack,
    ):
        """Article / course / event / recording / download / poll send
        zero emails and report ``emailed=0`` (issue #655)."""
        from email_app.models import EmailLog
        from email_app.services.email_service import EmailService

        article = Article.objects.create(
            title='Article', slug='article',
            date=date(2025, 1, 1), published=True, required_level=0,
        )
        course = Course.objects.create(
            title='Course', slug='course',
            status='published', required_level=0,
        )
        event = Event.objects.create(
            title='Event', slug='event',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming', required_level=0,
        )
        recording = Event.objects.create(
            title='Recording', slug='recording',
            start_datetime=timezone.now(), status='completed',
            recording_url='https://youtube.com/watch?v=x',
            published=True, required_level=0,
        )
        download = Download.objects.create(
            title='Download', slug='download',
            file_url='https://example.com/f.pdf',
            published=True, required_level=0,
        )
        poll = Poll.objects.create(title='Poll', status='open')

        with patch.object(EmailService, 'send') as mock_send:
            for content_type, pk in [
                ('article', article.pk),
                ('course', course.pk),
                ('event', event.pk),
                ('recording', recording.pk),
                ('download', download.pk),
                ('poll', poll.pk),
            ]:
                result = NotificationService.notify(content_type, pk)
                self.assertEqual(
                    result['emailed'], 0,
                    f'{content_type} unexpectedly returned emailed='
                    f"{result['emailed']}",
                )

            mock_send.assert_not_called()

        self.assertEqual(
            EmailLog.objects.filter(
                email_type='workshop_announcement',
            ).count(),
            0,
        )

    def test_get_email_eligible_users_filters_correctly(self):
        """The helper returns tier-eligible verified opted-in users only."""
        from notifications.services.notification_service import (
            get_email_eligible_users,
        )

        # Three opted-in verified users + variants that should be excluded.
        unverified = User.objects.create_user(
            email='unverified@example.com', password='p',
        )
        unverified.tier = self.free_tier
        unverified.email_verified = False
        unverified.save()

        unsubscribed = User.objects.create_user(
            email='unsubscribed@example.com', password='p',
        )
        unsubscribed.tier = self.free_tier
        unsubscribed.email_verified = True
        unsubscribed.unsubscribed = True
        unsubscribed.save()

        opted_out = User.objects.create_user(
            email='opted-out@example.com', password='p',
        )
        opted_out.tier = self.free_tier
        opted_out.email_verified = True
        opted_out.email_preferences = {'workshop_emails': False}
        opted_out.save()

        qs = get_email_eligible_users('workshop', self.workshop)
        emails = set(qs.values_list('email', flat=True))

        self.assertEqual(
            emails,
            {'user1@example.com', 'user2@example.com', 'user3@example.com'},
        )

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    @patch('email_app.services.email_service.EmailService._send_ses',
           return_value='ses-msg-default')
    def test_email_preferences_workshop_emails_default_true(
        self, mock_ses, mock_slack,
    ):
        """A brand-new user with empty ``email_preferences`` is treated as
        opted-in and lands in the email audience."""
        from notifications.services.notification_service import (
            get_email_eligible_users,
        )

        fresh = User.objects.create_user(
            email='fresh@example.com', password='p',
        )
        fresh.tier = self.free_tier
        fresh.email_verified = True
        # email_preferences default is ``{}`` (JSONField default=dict).
        fresh.save()

        self.assertEqual(fresh.email_preferences, {})

        eligible = get_email_eligible_users('workshop', self.workshop)
        self.assertIn(fresh, eligible)


@tag('core')
class CONTENT_TYPE_CONFIGTest(TestCase):
    """Issue #655: only ``workshop`` has an ``email_template`` configured."""

    def test_only_workshop_has_email_template_key(self):
        from notifications.services.notification_service import (
            CONTENT_TYPE_CONFIG,
        )

        types_with_email = {
            ct for ct, config in CONTENT_TYPE_CONFIG.items()
            if config.get('email_template')
        }
        self.assertEqual(types_with_email, {'workshop'})

    def test_workshop_email_template_is_workshop_announcement(self):
        from notifications.services.notification_service import (
            CONTENT_TYPE_CONFIG,
        )

        self.assertEqual(
            CONTENT_TYPE_CONFIG['workshop'].get('email_template'),
            'workshop_announcement',
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
