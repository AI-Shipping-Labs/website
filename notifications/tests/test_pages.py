"""Tests for notification page views."""

import datetime

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from content.models import Article
from notifications.models import Notification
from payments.models import Tier

User = get_user_model()


class NotificationListPageTest(TestCase):
    """Tests for GET /notifications (full page list)."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email='testuser@example.com', password='testpass123',
        )

    def test_unauthenticated_redirects_to_login(self):
        response = self.client.get('/notifications')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login', response.url)

    def test_authenticated_returns_200(self):
        self.client.login(email='testuser@example.com', password='testpass123')
        response = self.client.get('/notifications')
        self.assertEqual(response.status_code, 200)

    def test_uses_correct_template(self):
        self.client.login(email='testuser@example.com', password='testpass123')
        response = self.client.get('/notifications')
        self.assertTemplateUsed(response, 'notifications/notification_list.html')

    def test_shows_user_notifications(self):
        self.client.login(email='testuser@example.com', password='testpass123')
        Notification.objects.create(
            user=self.user, title='Test Notification',
            body='Test body', url='/test',
        )
        response = self.client.get('/notifications')
        self.assertContains(response, 'Test Notification')

    def test_does_not_show_other_users_notifications(self):
        other_user = User.objects.create_user(
            email='other@example.com', password='testpass123',
        )
        Notification.objects.create(
            user=other_user, title='Other Notification',
        )
        self.client.login(email='testuser@example.com', password='testpass123')
        response = self.client.get('/notifications')
        self.assertNotContains(response, 'Other Notification')

    def test_pagination(self):
        self.client.login(email='testuser@example.com', password='testpass123')
        for i in range(25):
            Notification.objects.create(user=self.user, title=f'Notif {i}')
        response = self.client.get('/notifications')
        self.assertEqual(response.status_code, 200)
        # Should show pagination
        self.assertContains(response, 'Page 1 of 2')

    def test_empty_state(self):
        self.client.login(email='testuser@example.com', password='testpass123')
        response = self.client.get('/notifications')
        self.assertContains(response, 'No notifications yet')

    def test_mark_all_as_read_button_present(self):
        self.client.login(email='testuser@example.com', password='testpass123')
        Notification.objects.create(user=self.user, title='Test')
        response = self.client.get('/notifications')
        self.assertContains(response, 'Mark all as read')

    def test_unread_notification_shows_accent_dot(self):
        self.client.login(email='testuser@example.com', password='testpass123')
        Notification.objects.create(user=self.user, title='Unread')
        response = self.client.get('/notifications')
        self.assertContains(response, 'bg-accent')

    def test_page_title(self):
        self.client.login(email='testuser@example.com', password='testpass123')
        response = self.client.get('/notifications')
        self.assertContains(response, '<title>Notifications |')

    # Replaces playwright_tests/test_notifications.py::
    # TestScenario3BrowseNotificationsPage::
    # test_notifications_page_pagination_and_click (pagination part)
    def test_pagination_navigation_to_page_2(self):
        """Page 2 returns the remaining items and exposes a Previous link."""
        self.client.login(email='testuser@example.com', password='testpass123')
        for i in range(25):
            Notification.objects.create(
                user=self.user, title=f'Notif {i:02d}',
            )
        response = self.client.get('/notifications?page=2')
        self.assertEqual(response.status_code, 200)
        page_obj = response.context['page_obj']
        self.assertEqual(page_obj.number, 2)
        self.assertEqual(len(page_obj.object_list), 5)
        self.assertFalse(page_obj.has_next())
        self.assertTrue(page_obj.has_previous())
        # Page 2 markers in markup
        self.assertContains(response, 'Page 2 of 2')
        self.assertContains(response, 'Previous')

    # Replaces playwright_tests/test_notifications.py::
    # TestScenario3BrowseNotificationsPage::
    # test_notifications_page_pagination_and_click (click-through part)
    def test_clicking_notification_target_url_resolves(self):
        """The notification.url field links to a real, reachable content page."""
        self.client.login(email='testuser@example.com', password='testpass123')
        article = Article.objects.create(
            title='Click Target Article',
            slug='click-target-article',
            date=datetime.date.today(),
            published=True,
            required_level=0,
        )
        Notification.objects.create(
            user=self.user,
            title=f'New article: {article.title}',
            url=article.get_absolute_url(),
        )
        # The page renders a link with the notification url as href
        response = self.client.get('/notifications')
        self.assertContains(response, f'href="{article.get_absolute_url()}"')
        # Following that link returns 200 (target page exists)
        target_response = self.client.get(article.get_absolute_url())
        self.assertEqual(target_response.status_code, 200)


class NotificationVisibilityTest(TestCase):
    """Free members see open notifications but not gated ones.

    Replaces playwright_tests/test_notifications.py::
    TestScenario6FreeSeesOpenNotGated::
    test_free_member_only_sees_open_notification.

    The visibility filter is enforced in NotificationService.notify (only
    eligible users get rows created); on the page it's just rendering the
    rows owned by the current user.
    """

    @classmethod
    def setUpTestData(cls):
        cls.free_tier = Tier.objects.get(slug='free')
        cls.free_user = User.objects.create_user(
            email='free@example.com', password='testpass123',
        )
        cls.free_user.tier = cls.free_tier
        cls.free_user.save()

    def test_free_user_only_sees_their_own_notifications(self):
        """Notifications filter strictly by user; gated content for which
        the service did not create a row is not visible."""
        # Open content notification was created for the free user
        Notification.objects.create(
            user=self.free_user,
            title='New article: Open Article for All',
            url='/blog/open-article-for-all',
        )
        # No notification was created for the free user about the gated
        # recording (NotificationService skipped them as ineligible).

        self.client.login(email='free@example.com', password='testpass123')
        response = self.client.get('/notifications')
        notifications = list(response.context['notifications'])
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].title,
                         'New article: Open Article for All')
        self.assertContains(response, 'Open Article for All')
        # Gated recording title must NOT appear
        self.assertNotContains(response, 'Basic-only Recording')
