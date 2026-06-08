"""Tests for NotificationService.notify_series (issue #868)."""

from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from events.models import Event, EventSeries
from notifications.models import Notification
from notifications.services.notification_service import (
    NotificationService,
    series_notification_title,
)

User = get_user_model()


class NotifySeriesTest(TestCase):
    def setUp(self):
        from payments.models import Tier

        self.free_tier = Tier.objects.get(slug='free')
        self.basic_tier = Tier.objects.get(slug='basic')
        self.main_tier = Tier.objects.get(slug='main')

        self.free_user = User.objects.create_user(
            email='free@example.com', password='x',
        )
        self.free_user.tier = self.free_tier
        self.free_user.save()

        self.basic_user = User.objects.create_user(
            email='basic@example.com', password='x',
        )
        self.basic_user.tier = self.basic_tier
        self.basic_user.save()

        self.main_user = User.objects.create_user(
            email='main@example.com', password='x',
        )
        self.main_user.tier = self.main_tier
        self.main_user.save()

        self.series = EventSeries.objects.create(
            name='Build Club',
            slug='build-club',
            description='Weekly shipping.',
            start_time=time(18, 0),
            timezone='Europe/Berlin',
        )

    def _session(self, position, days_ahead, level=0, status='upcoming', slug=None):
        return Event.objects.create(
            title=f'Build Club — Session {position}',
            slug=slug or f'build-club-session-{position}',
            start_datetime=timezone.now() + timedelta(days=days_ahead),
            status=status,
            required_level=level,
            event_series=self.series,
            series_position=position,
        )

    def test_creates_one_notification_per_eligible_user(self):
        self._session(1, 3)
        self._session(2, 10)
        result = NotificationService.notify_series(self.series)
        # Level 0 -> all three users, ONE notification each (not per session).
        self.assertEqual(result['notified'], 3)
        self.assertEqual(Notification.objects.count(), 3)

    def test_notification_deep_links_to_public_series_page(self):
        self._session(1, 3)
        NotificationService.notify_series(self.series)
        n = Notification.objects.filter(user=self.free_user).first()
        self.assertEqual(n.title, series_notification_title(self.series))
        self.assertEqual(n.title, 'New event series: Build Club')
        self.assertEqual(n.url, '/events/groups/build-club')
        self.assertEqual(n.notification_type, 'new_content')
        self.assertEqual(n.body, 'Weekly shipping.')

    def test_audience_uses_lowest_required_level(self):
        # One session needs main (20), another needs only basic (10).
        # Anyone who clears the LOWEST level (10) should be notified.
        self._session(1, 3, level=20)
        self._session(2, 10, level=10)
        NotificationService.notify_series(self.series)
        notified = set(
            Notification.objects.values_list('user__email', flat=True),
        )
        self.assertIn('basic@example.com', notified)
        self.assertIn('main@example.com', notified)
        self.assertNotIn('free@example.com', notified)

    def test_member_who_cannot_access_any_session_not_notified(self):
        # Every upcoming session requires basic (10); free user excluded.
        self._session(1, 3, level=10)
        self._session(2, 10, level=10)
        NotificationService.notify_series(self.series)
        notified = set(
            Notification.objects.values_list('user__email', flat=True),
        )
        self.assertNotIn('free@example.com', notified)
        self.assertEqual(len(notified), 2)

    def test_empty_series_notifies_nobody(self):
        self._session(1, -3, slug='past')  # past
        self._session(2, 3, status='draft', slug='draft')  # draft
        result = NotificationService.notify_series(self.series)
        self.assertEqual(result['notified'], 0)
        self.assertEqual(Notification.objects.count(), 0)
