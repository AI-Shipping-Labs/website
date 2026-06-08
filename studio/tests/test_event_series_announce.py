"""Tests for the series-level Studio notify / Slack-announce endpoints.

Issue #868.
"""

from datetime import time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from events.models import Event, EventSeries
from notifications.models import Notification

User = get_user_model()


class SeriesActionMixin:
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from payments.models import Tier

        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pass', is_staff=True,
        )
        free_tier = Tier.objects.get(slug='free')
        cls.member = User.objects.create_user(
            email='member@test.com', password='pass',
        )
        cls.member.tier = free_tier
        cls.member.save()

        cls.series = EventSeries.objects.create(
            name='Build Club',
            slug='build-club',
            description='Weekly shipping.',
            start_time=time(18, 0),
            timezone='Europe/Berlin',
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='pass')

    def _session(self, position, days_ahead, **kw):
        return Event.objects.create(
            title=f'Build Club — Session {position}',
            slug=kw.pop('slug', f'build-club-session-{position}'),
            start_datetime=timezone.now() + timedelta(days=days_ahead),
            status=kw.pop('status', 'upcoming'),
            required_level=kw.pop('level', 0),
            event_series=self.series,
            series_position=position,
        )

    def _notify_url(self):
        return f'/studio/event-series/{self.series.pk}/notify'

    def _slack_url(self):
        return f'/studio/event-series/{self.series.pk}/announce-slack'


class SeriesNotifyEndpointTest(SeriesActionMixin, TestCase):
    def test_notify_is_staff_only(self):
        self._session(1, 3)
        anon = Client()
        resp = anon.post(self._notify_url())
        self.assertIn(resp.status_code, (302, 403))

        plain = User.objects.create_user(email='plain@test.com', password='pass')
        c = Client()
        c.login(email='plain@test.com', password='pass')
        resp = c.post(self._notify_url())
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Notification.objects.filter(user=plain).exists())

    def test_notify_requires_post(self):
        self._session(1, 3)
        resp = self.client.get(self._notify_url())
        self.assertEqual(resp.status_code, 405)

    def test_notify_creates_one_notification_per_user(self):
        self._session(1, 3)
        resp = self.client.post(self._notify_url())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['notified'], Notification.objects.count())
        n = Notification.objects.filter(user=self.member).first()
        self.assertIsNotNone(n)
        self.assertEqual(n.url, '/events/groups/build-club')
        self.assertEqual(n.title, 'New event series: Build Club')

    def test_re_notify_within_24h_returns_409_and_no_duplicates(self):
        self._session(1, 3)
        self.client.post(self._notify_url())
        first_count = Notification.objects.count()

        resp = self.client.post(self._notify_url())
        self.assertEqual(resp.status_code, 409)
        self.assertIn('error', resp.json())
        # No second batch created.
        self.assertEqual(Notification.objects.count(), first_count)

    def test_member_who_cannot_attend_is_not_notified(self):
        # Only upcoming session requires a paid tier; free member excluded.
        self._session(1, 3, level=10)
        resp = self.client.post(self._notify_url())
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Notification.objects.filter(user=self.member).exists())


class SeriesSlackEndpointTest(SeriesActionMixin, TestCase):
    def test_slack_is_staff_only(self):
        self._session(1, 3)
        c = Client()
        User.objects.create_user(email='plain2@test.com', password='pass')
        c.login(email='plain2@test.com', password='pass')
        resp = c.post(self._slack_url())
        self.assertEqual(resp.status_code, 403)

    def test_slack_requires_post(self):
        self._session(1, 3)
        resp = self.client.get(self._slack_url())
        self.assertEqual(resp.status_code, 405)

    @patch(
        'studio.views.event_series.post_series_slack_announcement',
        return_value=True,
    )
    def test_slack_posts_whole_series_in_one_call(self, mock_post):
        self._session(1, 3)
        self._session(2, 10)
        resp = self.client.post(self._slack_url())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'posted': True})
        mock_post.assert_called_once_with(self.series)

    @patch('studio.views.event_series.post_series_slack_announcement')
    def test_slack_empty_series_reports_no_upcoming(self, mock_post):
        # Only a past session.
        self._session(1, -3, slug='past')
        resp = self.client.post(self._slack_url())
        self.assertEqual(resp.status_code, 500)
        self.assertIn('No upcoming sessions', resp.json()['error'])
        # Guard should short-circuit before attempting a post.
        mock_post.assert_not_called()

    @patch(
        'studio.views.event_series.post_series_slack_announcement',
        return_value=False,
    )
    def test_slack_post_failure_returns_500(self, mock_post):
        self._session(1, 3)
        resp = self.client.post(self._slack_url())
        self.assertEqual(resp.status_code, 500)
        self.assertIn('error', resp.json())


class SeriesDetailButtonsTest(SeriesActionMixin, TestCase):
    def test_detail_page_shows_both_buttons(self):
        self._session(1, 3)
        resp = self.client.get(f'/studio/event-series/{self.series.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-testid="event-series-notify"')
        self.assertContains(resp, 'data-testid="event-series-announce-slack"')
        self.assertContains(resp, self._notify_url())
        self.assertContains(resp, self._slack_url())
