"""Tests for notification page views."""

from django.contrib.auth import get_user_model
from django.test import TestCase, Client

from notifications.models import Notification

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
