"""Tests for Notification and EventReminderLog models."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from notifications.models import Notification, EventReminderLog

User = get_user_model()


class NotificationModelTest(TestCase):
    """Test Notification model fields, defaults, and constraints."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='testuser@example.com', password='testpass123',
        )

    def test_create_notification_with_required_fields(self):
        n = Notification.objects.create(
            user=self.user,
            title='Test Notification',
        )
        self.assertEqual(n.title, 'Test Notification')
        self.assertEqual(n.user, self.user)
        self.assertFalse(n.read)
        self.assertEqual(n.notification_type, 'new_content')
        self.assertIsNotNone(n.created_at)

    def test_notification_body_default_empty(self):
        n = Notification.objects.create(user=self.user, title='Test')
        self.assertEqual(n.body, '')

    def test_notification_url_default_empty(self):
        n = Notification.objects.create(user=self.user, title='Test')
        self.assertEqual(n.url, '')

    def test_notification_read_default_false(self):
        n = Notification.objects.create(user=self.user, title='Test')
        self.assertFalse(n.read)

    def test_notification_type_choices(self):
        for ntype in ('new_content', 'event_reminder', 'announcement'):
            n = Notification.objects.create(
                user=self.user,
                title=f'Test {ntype}',
                notification_type=ntype,
            )
            self.assertEqual(n.notification_type, ntype)

    def test_notification_user_nullable(self):
        """User FK should be nullable for broadcast notifications."""
        n = Notification.objects.create(
            user=None,
            title='Broadcast',
            notification_type='announcement',
        )
        self.assertIsNone(n.user)

    def test_notification_ordering_newest_first(self):
        n1 = Notification.objects.create(user=self.user, title='First')
        n2 = Notification.objects.create(user=self.user, title='Second')
        notifications = list(Notification.objects.all())
        self.assertEqual(notifications[0], n2)
        self.assertEqual(notifications[1], n1)

    def test_notification_str(self):
        n = Notification.objects.create(
            user=self.user,
            title='My Notification',
            notification_type='new_content',
        )
        self.assertEqual(str(n), 'My Notification (new_content)')

    def test_notification_cascade_delete_user(self):
        """Notifications should be deleted when user is deleted."""
        Notification.objects.create(user=self.user, title='Test')
        self.assertEqual(Notification.objects.count(), 1)
        self.user.delete()
        self.assertEqual(Notification.objects.count(), 0)

    def test_notification_all_fields(self):
        n = Notification.objects.create(
            user=self.user,
            title='Full Test',
            body='This is the body text',
            url='/blog/test-article',
            notification_type='event_reminder',
            read=True,
        )
        n.refresh_from_db()
        self.assertEqual(n.title, 'Full Test')
        self.assertEqual(n.body, 'This is the body text')
        self.assertEqual(n.url, '/blog/test-article')
        self.assertEqual(n.notification_type, 'event_reminder')
        self.assertTrue(n.read)


class EventReminderLogModelTest(TestCase):
    """Test EventReminderLog model for deduplication."""

    def setUp(self):
        from events.models import Event
        from django.utils import timezone
        self.user = User.objects.create_user(
            email='testuser@example.com', password='testpass123',
        )
        self.event = Event.objects.create(
            title='Test Event',
            slug='test-event',
            start_datetime=timezone.now(),
            status='upcoming',
        )

    def test_create_reminder_log(self):
        log = EventReminderLog.objects.create(
            event=self.event,
            user=self.user,
            interval='24h',
        )
        self.assertEqual(log.event, self.event)
        self.assertEqual(log.user, self.user)
        self.assertEqual(log.interval, '24h')

    def test_unique_constraint_prevents_duplicate(self):
        """Same (event, user, interval) should not be allowed twice."""
        from django.db import IntegrityError
        EventReminderLog.objects.create(
            event=self.event, user=self.user, interval='24h',
        )
        with self.assertRaises(IntegrityError):
            EventReminderLog.objects.create(
                event=self.event, user=self.user, interval='24h',
            )

    def test_different_intervals_allowed(self):
        """Same event+user can have both 24h and 1h reminders."""
        EventReminderLog.objects.create(
            event=self.event, user=self.user, interval='24h',
        )
        log2 = EventReminderLog.objects.create(
            event=self.event, user=self.user, interval='1h',
        )
        self.assertEqual(EventReminderLog.objects.count(), 2)
        self.assertEqual(log2.interval, '1h')

    def test_str(self):
        log = EventReminderLog.objects.create(
            event=self.event, user=self.user, interval='24h',
        )
        self.assertIn('24h', str(log))
