"""Tests for notification API endpoints."""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase, Client

from notifications.models import Notification

User = get_user_model()


class NotificationApiListTest(TestCase):
    """Tests for GET /api/notifications."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email='testuser@example.com', password='testpass123',
        )

    def test_unauthenticated_returns_redirect(self):
        response = self.client.get('/api/notifications')
        self.assertEqual(response.status_code, 302)

    def test_empty_notification_list(self):
        self.client.login(email='testuser@example.com', password='testpass123')
        response = self.client.get('/api/notifications')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['notifications'], [])
        self.assertEqual(data['total'], 0)
        self.assertEqual(data['page'], 1)
        self.assertFalse(data['has_next'])

    def test_returns_user_notifications(self):
        self.client.login(email='testuser@example.com', password='testpass123')
        Notification.objects.create(
            user=self.user,
            title='Test Notification',
            body='Test body',
            url='/blog/test',
        )
        response = self.client.get('/api/notifications')
        data = json.loads(response.content)
        self.assertEqual(len(data['notifications']), 1)
        self.assertEqual(data['notifications'][0]['title'], 'Test Notification')
        self.assertEqual(data['total'], 1)

    def test_does_not_return_other_users_notifications(self):
        other_user = User.objects.create_user(
            email='other@example.com', password='testpass123',
        )
        Notification.objects.create(user=other_user, title='Other User Notification')
        self.client.login(email='testuser@example.com', password='testpass123')
        response = self.client.get('/api/notifications')
        data = json.loads(response.content)
        self.assertEqual(data['total'], 0)

    def test_pagination_defaults_to_20(self):
        self.client.login(email='testuser@example.com', password='testpass123')
        for i in range(25):
            Notification.objects.create(user=self.user, title=f'Notif {i}')
        response = self.client.get('/api/notifications')
        data = json.loads(response.content)
        self.assertEqual(len(data['notifications']), 20)
        self.assertTrue(data['has_next'])
        self.assertEqual(data['total'], 25)

    def test_pagination_page_2(self):
        self.client.login(email='testuser@example.com', password='testpass123')
        for i in range(25):
            Notification.objects.create(user=self.user, title=f'Notif {i}')
        response = self.client.get('/api/notifications?page=2')
        data = json.loads(response.content)
        self.assertEqual(len(data['notifications']), 5)
        self.assertFalse(data['has_next'])
        self.assertEqual(data['page'], 2)

    def test_notification_body_truncated_to_80(self):
        self.client.login(email='testuser@example.com', password='testpass123')
        long_body = 'x' * 200
        Notification.objects.create(user=self.user, title='Test', body=long_body)
        response = self.client.get('/api/notifications')
        data = json.loads(response.content)
        self.assertEqual(len(data['notifications'][0]['body']), 80)

    def test_notification_includes_read_status(self):
        self.client.login(email='testuser@example.com', password='testpass123')
        Notification.objects.create(user=self.user, title='Unread')
        Notification.objects.create(user=self.user, title='Read', read=True)
        response = self.client.get('/api/notifications')
        data = json.loads(response.content)
        # Newest first
        read_statuses = [n['read'] for n in data['notifications']]
        self.assertIn(True, read_statuses)
        self.assertIn(False, read_statuses)

    def test_post_not_allowed(self):
        self.client.login(email='testuser@example.com', password='testpass123')
        response = self.client.post('/api/notifications')
        self.assertEqual(response.status_code, 405)


class UnreadCountApiTest(TestCase):
    """Tests for GET /api/notifications/unread-count."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email='testuser@example.com', password='testpass123',
        )

    def test_unauthenticated_returns_redirect(self):
        response = self.client.get('/api/notifications/unread-count')
        self.assertEqual(response.status_code, 302)

    def test_returns_zero_when_no_notifications(self):
        self.client.login(email='testuser@example.com', password='testpass123')
        response = self.client.get('/api/notifications/unread-count')
        data = json.loads(response.content)
        self.assertEqual(data['count'], 0)

    def test_returns_correct_unread_count(self):
        self.client.login(email='testuser@example.com', password='testpass123')
        Notification.objects.create(user=self.user, title='Unread 1')
        Notification.objects.create(user=self.user, title='Unread 2')
        Notification.objects.create(user=self.user, title='Read', read=True)
        response = self.client.get('/api/notifications/unread-count')
        data = json.loads(response.content)
        self.assertEqual(data['count'], 2)

    def test_does_not_count_other_users_notifications(self):
        other_user = User.objects.create_user(
            email='other@example.com', password='testpass123',
        )
        Notification.objects.create(user=other_user, title='Other User')
        self.client.login(email='testuser@example.com', password='testpass123')
        response = self.client.get('/api/notifications/unread-count')
        data = json.loads(response.content)
        self.assertEqual(data['count'], 0)


class MarkReadApiTest(TestCase):
    """Tests for POST /api/notifications/{id}/read."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email='testuser@example.com', password='testpass123',
        )

    def test_unauthenticated_returns_redirect(self):
        n = Notification.objects.create(user=self.user, title='Test')
        response = self.client.post(f'/api/notifications/{n.pk}/read')
        self.assertEqual(response.status_code, 302)

    def test_marks_notification_as_read(self):
        self.client.login(email='testuser@example.com', password='testpass123')
        n = Notification.objects.create(user=self.user, title='Test')
        self.assertFalse(n.read)
        response = self.client.post(f'/api/notifications/{n.pk}/read')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['ok'])
        n.refresh_from_db()
        self.assertTrue(n.read)

    def test_cannot_mark_other_users_notification(self):
        other_user = User.objects.create_user(
            email='other@example.com', password='testpass123',
        )
        n = Notification.objects.create(user=other_user, title='Other')
        self.client.login(email='testuser@example.com', password='testpass123')
        response = self.client.post(f'/api/notifications/{n.pk}/read')
        self.assertEqual(response.status_code, 404)

    def test_nonexistent_notification_returns_404(self):
        self.client.login(email='testuser@example.com', password='testpass123')
        response = self.client.post('/api/notifications/99999/read')
        self.assertEqual(response.status_code, 404)

    def test_get_not_allowed(self):
        self.client.login(email='testuser@example.com', password='testpass123')
        n = Notification.objects.create(user=self.user, title='Test')
        response = self.client.get(f'/api/notifications/{n.pk}/read')
        self.assertEqual(response.status_code, 405)


class MarkAllReadApiTest(TestCase):
    """Tests for POST /api/notifications/read-all."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email='testuser@example.com', password='testpass123',
        )

    def test_unauthenticated_returns_redirect(self):
        response = self.client.post('/api/notifications/read-all')
        self.assertEqual(response.status_code, 302)

    def test_marks_all_as_read(self):
        self.client.login(email='testuser@example.com', password='testpass123')
        Notification.objects.create(user=self.user, title='N1')
        Notification.objects.create(user=self.user, title='N2')
        Notification.objects.create(user=self.user, title='N3')
        response = self.client.post('/api/notifications/read-all')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['ok'])
        self.assertEqual(data['count'], 3)
        self.assertEqual(
            Notification.objects.filter(user=self.user, read=False).count(), 0,
        )

    def test_does_not_affect_other_users(self):
        other_user = User.objects.create_user(
            email='other@example.com', password='testpass123',
        )
        Notification.objects.create(user=other_user, title='Other')
        self.client.login(email='testuser@example.com', password='testpass123')
        self.client.post('/api/notifications/read-all')
        self.assertFalse(
            Notification.objects.get(user=other_user).read,
        )

    def test_returns_zero_count_when_already_read(self):
        self.client.login(email='testuser@example.com', password='testpass123')
        Notification.objects.create(user=self.user, title='Read', read=True)
        response = self.client.post('/api/notifications/read-all')
        data = json.loads(response.content)
        self.assertEqual(data['count'], 0)
