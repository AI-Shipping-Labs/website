"""Tests for Notification and EventReminderLog models.

The previous ``NotificationModelTest`` covered Django defaults
(``BooleanField`` default, FK nullability, choices round-trip,
``Meta.ordering``) which are framework-owned and have been
removed per ``_docs/testing-guidelines.md`` Rule 3. Real
``Notification`` behaviour (creation triggers, eligibility,
read/unread transitions) is exercised in
``notifications/tests/test_views.py`` and the per-feature
delivery suites.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from notifications.models import EventReminderLog

User = get_user_model()


class EventReminderLogModelTest(TestCase):
    """Test EventReminderLog model for deduplication."""

    def setUp(self):
        from django.utils import timezone

        from events.models import Event
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
