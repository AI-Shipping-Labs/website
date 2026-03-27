"""Tests for studio notification log and notify/announce actions.

Verifies:
- Notification log page access and display
- Pagination on the notification log
- Sidebar link presence
- Notify subscribers endpoint (success, duplicate guard, non-staff access)
- Post to Slack endpoint
- Buttons visible only on published content edit pages
- All endpoints require staff and POST method
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone

from content.models import Article, Download, Course
from events.models import Event
from notifications.models import Notification

User = get_user_model()


class StudioNotificationLogTest(TestCase):
    """Test the /studio/notifications/ page."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_notification_log_returns_200(self):
        response = self.client.get('/studio/notifications/')
        self.assertEqual(response.status_code, 200)

    def test_notification_log_uses_correct_template(self):
        response = self.client.get('/studio/notifications/')
        self.assertTemplateUsed(response, 'studio/notifications/list.html')

    def test_notification_log_shows_deduplicated_batches(self):
        """Notifications for the same title/url are grouped into one row."""
        user1 = User.objects.create_user(email='u1@test.com', password='p')
        user2 = User.objects.create_user(email='u2@test.com', password='p')
        user3 = User.objects.create_user(email='u3@test.com', password='p')

        # Create 3 notifications for the same article
        for user in [user1, user2, user3]:
            Notification.objects.create(
                user=user,
                title='New article: Test Article',
                url='/blog/test-article',
                notification_type='new_content',
            )

        response = self.client.get('/studio/notifications/')
        self.assertContains(response, 'New article: Test Article')
        # Should show user count of 3
        self.assertContains(response, '3')

    def test_notification_log_shows_multiple_batches(self):
        """Two different notification batches appear as two rows."""
        user1 = User.objects.create_user(email='u1@test.com', password='p')

        Notification.objects.create(
            user=user1,
            title='New article: Article One',
            url='/blog/article-one',
            notification_type='new_content',
        )
        Notification.objects.create(
            user=user1,
            title='New article: Article Two',
            url='/blog/article-two',
            notification_type='new_content',
        )

        response = self.client.get('/studio/notifications/')
        self.assertContains(response, 'Article One')
        self.assertContains(response, 'Article Two')

    def test_notification_log_paginates_at_20(self):
        """Only 20 items per page, with Next link when more exist."""
        user = User.objects.create_user(email='u@test.com', password='p')
        for i in range(25):
            Notification.objects.create(
                user=user,
                title=f'Batch {i}',
                url=f'/content/{i}',
                notification_type='new_content',
            )

        response = self.client.get('/studio/notifications/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Next')

        # Page 2 should have the remaining 5
        response2 = self.client.get('/studio/notifications/?page=2')
        self.assertEqual(response2.status_code, 200)

    def test_non_staff_redirected_to_login(self):
        """Non-staff users cannot access the notification log."""
        self.client.logout()
        regular = User.objects.create_user(
            email='regular@test.com', password='testpass', is_staff=False,
        )
        self.client.login(email='regular@test.com', password='testpass')
        response = self.client.get('/studio/notifications/')
        # Non-staff authenticated users get 403
        self.assertEqual(response.status_code, 403)

    def test_anonymous_redirected_to_login(self):
        """Anonymous users are redirected to login."""
        self.client.logout()
        response = self.client.get('/studio/notifications/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)


class StudioNotificationSidebarTest(TestCase):
    """Test that the Notifications link appears in the sidebar."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_sidebar_contains_notifications_link(self):
        """The studio sidebar has a Notifications link with bell icon."""
        response = self.client.get('/studio/')
        self.assertContains(response, 'Notifications')
        self.assertContains(response, '/studio/notifications/')
        self.assertContains(response, 'bell')


class StudioArticleNotifyTest(TestCase):
    """Test notify subscribers and Slack announce for articles."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.article = Article.objects.create(
            title='Published Article',
            slug='published-article',
            date=timezone.now().date(),
            published=True,
            required_level=0,
        )

    def test_notify_creates_notifications(self):
        """POST to notify creates notifications for eligible users."""
        user1 = User.objects.create_user(email='u1@test.com', password='p', is_active=True)
        user2 = User.objects.create_user(email='u2@test.com', password='p', is_active=True)

        response = self.client.post(f'/studio/articles/{self.article.pk}/notify')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        # Should have notified at least user1 and user2 (plus the staff user)
        self.assertIn('notified', data)
        self.assertGreaterEqual(data['notified'], 2)

    def test_duplicate_notify_returns_409(self):
        """If already notified in the last 24h, return 409."""
        # First notify
        self.client.post(f'/studio/articles/{self.article.pk}/notify')

        # Second notify should be blocked
        response = self.client.post(f'/studio/articles/{self.article.pk}/notify')
        self.assertEqual(response.status_code, 409)
        data = response.json()
        self.assertIn('Already notified', data['error'])

    def test_notify_requires_post(self):
        """GET to notify returns 405."""
        response = self.client.get(f'/studio/articles/{self.article.pk}/notify')
        self.assertEqual(response.status_code, 405)

    def test_notify_requires_staff(self):
        """Non-staff user is redirected to login."""
        self.client.logout()
        regular = User.objects.create_user(
            email='regular@test.com', password='testpass', is_staff=False,
        )
        self.client.login(email='regular@test.com', password='testpass')
        response = self.client.post(f'/studio/articles/{self.article.pk}/notify')
        self.assertEqual(response.status_code, 403)

    def test_anonymous_notify_redirected(self):
        """Anonymous user is redirected to login."""
        self.client.logout()
        response = self.client.post(f'/studio/articles/{self.article.pk}/notify')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    @patch('studio.views.notifications.post_slack_announcement')
    def test_announce_slack_returns_json(self, mock_slack):
        """POST to announce-slack calls post_slack_announcement."""
        mock_slack.return_value = True
        response = self.client.post(f'/studio/articles/{self.article.pk}/announce-slack')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['posted'])

    def test_announce_slack_requires_post(self):
        """GET to announce-slack returns 405."""
        response = self.client.get(f'/studio/articles/{self.article.pk}/announce-slack')
        self.assertEqual(response.status_code, 405)

    def test_announce_slack_requires_staff(self):
        """Non-staff user is rejected."""
        self.client.logout()
        regular = User.objects.create_user(
            email='regular@test.com', password='testpass', is_staff=False,
        )
        self.client.login(email='regular@test.com', password='testpass')
        response = self.client.post(f'/studio/articles/{self.article.pk}/announce-slack')
        self.assertEqual(response.status_code, 403)


class StudioArticleFormButtonsTest(TestCase):
    """Test that notify/slack buttons appear only on published articles."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_buttons_visible_on_published_article(self):
        """Published article edit page shows notify and slack buttons."""
        article = Article.objects.create(
            title='Published', slug='pub', date=timezone.now().date(), published=True,
        )
        response = self.client.get(f'/studio/articles/{article.pk}/edit')
        self.assertContains(response, 'Notify subscribers')
        self.assertContains(response, 'Post to Slack')

    def test_buttons_hidden_on_draft_article(self):
        """Draft article edit page does not show notify or slack buttons."""
        article = Article.objects.create(
            title='Draft', slug='draft', date=timezone.now().date(), published=False,
        )
        response = self.client.get(f'/studio/articles/{article.pk}/edit')
        self.assertNotContains(response, 'Notify subscribers')
        self.assertNotContains(response, 'Post to Slack')

    def test_create_url_removed(self):
        """New article create URL is removed (synced content types)."""
        response = self.client.get('/studio/articles/new')
        self.assertEqual(response.status_code, 404)


class StudioRecordingNotifyTest(TestCase):
    """Test notify and announce for recordings."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.recording = Event.objects.create(
            title='Published Recording',
            slug='published-recording',
            start_datetime=timezone.now(), status='completed',
            published=True,
            required_level=0,
        )

    def test_notify_creates_notifications(self):
        user1 = User.objects.create_user(email='u1@test.com', password='p', is_active=True)
        response = self.client.post(f'/studio/recordings/{self.recording.pk}/notify')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('notified', data)

    def test_notify_requires_post(self):
        response = self.client.get(f'/studio/recordings/{self.recording.pk}/notify')
        self.assertEqual(response.status_code, 405)

    def test_buttons_visible_on_published_recording(self):
        response = self.client.get(f'/studio/recordings/{self.recording.pk}/edit')
        self.assertContains(response, 'Notify subscribers')
        self.assertContains(response, 'Post to Slack')

    def test_buttons_hidden_on_unpublished_recording(self):
        self.recording.published = False
        self.recording.save()
        response = self.client.get(f'/studio/recordings/{self.recording.pk}/edit')
        self.assertNotContains(response, 'Notify subscribers')
        self.assertNotContains(response, 'Post to Slack')


class StudioEventNotifyTest(TestCase):
    """Test notify and announce for events."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.event = Event.objects.create(
            title='Upcoming Event',
            slug='upcoming-event',
            status='upcoming',
            start_datetime=timezone.now() + timedelta(days=7),
            end_datetime=timezone.now() + timedelta(days=7, hours=1),
            required_level=0,
        )

    def test_notify_creates_notifications(self):
        user1 = User.objects.create_user(email='u1@test.com', password='p', is_active=True)
        response = self.client.post(f'/studio/events/{self.event.pk}/notify')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('notified', data)

    def test_notify_requires_post(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/notify')
        self.assertEqual(response.status_code, 405)

    def test_buttons_visible_on_upcoming_event(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(response, 'Notify subscribers')
        self.assertContains(response, 'Post to Slack')

    def test_buttons_hidden_on_draft_event(self):
        self.event.status = 'draft'
        self.event.save()
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertNotContains(response, 'Notify subscribers')
        self.assertNotContains(response, 'Post to Slack')


class StudioDownloadNotifyTest(TestCase):
    """Test notify and announce for downloads."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.download = Download.objects.create(
            title='Published Download',
            slug='published-download',
            file_url='https://example.com/file.pdf',
            published=True,
            required_level=0,
        )

    def test_notify_creates_notifications(self):
        user1 = User.objects.create_user(email='u1@test.com', password='p', is_active=True)
        response = self.client.post(f'/studio/downloads/{self.download.pk}/notify')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('notified', data)

    def test_notify_requires_post(self):
        response = self.client.get(f'/studio/downloads/{self.download.pk}/notify')
        self.assertEqual(response.status_code, 405)

    def test_buttons_visible_on_published_download(self):
        response = self.client.get(f'/studio/downloads/{self.download.pk}/edit')
        self.assertContains(response, 'Notify subscribers')
        self.assertContains(response, 'Post to Slack')

    def test_buttons_hidden_on_unpublished_download(self):
        self.download.published = False
        self.download.save()
        response = self.client.get(f'/studio/downloads/{self.download.pk}/edit')
        self.assertNotContains(response, 'Notify subscribers')
        self.assertNotContains(response, 'Post to Slack')


class StudioCourseNotifyTest(TestCase):
    """Test notify and announce for courses."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.course = Course.objects.create(
            title='Published Course',
            slug='published-course',
            status='published',
            required_level=0,
        )

    def test_notify_creates_notifications(self):
        user1 = User.objects.create_user(email='u1@test.com', password='p', is_active=True)
        response = self.client.post(f'/studio/courses/{self.course.pk}/notify')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('notified', data)

    def test_notify_requires_post(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/notify')
        self.assertEqual(response.status_code, 405)

    def test_buttons_visible_on_published_course(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')
        self.assertContains(response, 'Notify subscribers')
        self.assertContains(response, 'Post to Slack')

    def test_buttons_hidden_on_draft_course(self):
        self.course.status = 'draft'
        self.course.save()
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')
        self.assertNotContains(response, 'Notify subscribers')
        self.assertNotContains(response, 'Post to Slack')


class StudioNotifyDoubleGuardTest(TestCase):
    """Test the duplicate notification guard across content types."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_duplicate_guard_recording(self):
        recording = Event.objects.create(
            title='Test Rec', slug='test-rec',
            start_datetime=timezone.now(), status='completed', published=True, required_level=0,
        )
        # First notify
        self.client.post(f'/studio/recordings/{recording.pk}/notify')
        # Second should be blocked
        response = self.client.post(f'/studio/recordings/{recording.pk}/notify')
        self.assertEqual(response.status_code, 409)

    def test_duplicate_guard_event(self):
        event = Event.objects.create(
            title='Test Event', slug='test-evt',
            status='upcoming',
            start_datetime=timezone.now() + timedelta(days=7),
            end_datetime=timezone.now() + timedelta(days=7, hours=1),
            required_level=0,
        )
        self.client.post(f'/studio/events/{event.pk}/notify')
        response = self.client.post(f'/studio/events/{event.pk}/notify')
        self.assertEqual(response.status_code, 409)

    def test_duplicate_guard_download(self):
        download = Download.objects.create(
            title='Test DL', slug='test-dl',
            file_url='https://example.com/file.pdf',
            published=True, required_level=0,
        )
        self.client.post(f'/studio/downloads/{download.pk}/notify')
        response = self.client.post(f'/studio/downloads/{download.pk}/notify')
        self.assertEqual(response.status_code, 409)

    def test_duplicate_guard_course(self):
        course = Course.objects.create(
            title='Test Course', slug='test-course',
            status='published', required_level=0,
        )
        self.client.post(f'/studio/courses/{course.pk}/notify')
        response = self.client.post(f'/studio/courses/{course.pk}/notify')
        self.assertEqual(response.status_code, 409)
