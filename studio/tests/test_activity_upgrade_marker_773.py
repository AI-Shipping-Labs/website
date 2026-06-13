"""Tests for the CRM Activity timeline `resource_view` rows + the
"Upgraded to paid" marker (issue #773)."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from analytics.models import UserActivity
from studio.views.users import (
    USER_ACTIVITY_DISPLAY_LIMIT,
    _build_activity_timeline,
)

User = get_user_model()


class ActivityTimelineBuildTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='timeline-773@test.com', password='pw',
        )
        UserActivity.objects.all().delete()

    def _add(self, event_type, label, minutes_ago=0):
        return UserActivity.objects.create(
            user=self.member,
            event_type=event_type,
            label=label,
            occurred_at=timezone.now() - timezone.timedelta(minutes=minutes_ago),
        )

    def test_display_limit_is_30(self):
        self.assertEqual(USER_ACTIVITY_DISPLAY_LIMIT, 30)

    def test_resource_view_interleaves_newest_first(self):
        self._add(UserActivity.EVENT_RESOURCE_VIEW, 'Viewed article: A', 30)
        self._add(UserActivity.EVENT_PAYMENT, 'Payment: Main', 20)
        self._add(UserActivity.EVENT_RESOURCE_VIEW, 'Viewed article: D', 10)

        result = _build_activity_timeline(self.member)
        labels = [a['label'] for a in result['activities']]
        self.assertEqual(
            labels,
            ['Viewed article: D', 'Payment: Main', 'Viewed article: A'],
        )

    def test_first_payment_at_set_when_payment_exists(self):
        self._add(UserActivity.EVENT_RESOURCE_VIEW, 'Viewed article: A', 30)
        first_pay = self._add(UserActivity.EVENT_PAYMENT, 'Payment: Main', 20)
        # A later second payment must NOT move the marker.
        self._add(UserActivity.EVENT_PAYMENT, 'Payment: Premium', 5)

        result = _build_activity_timeline(self.member)
        self.assertEqual(result['first_payment_at'], first_pay.occurred_at)

        marker_rows = [a for a in result['activities'] if a['is_upgrade_marker']]
        self.assertEqual(len(marker_rows), 1)
        self.assertEqual(marker_rows[0]['occurred_at'], first_pay.occurred_at)

    def test_no_first_payment_when_never_paid(self):
        self._add(UserActivity.EVENT_RESOURCE_VIEW, 'Viewed article: A', 30)
        result = _build_activity_timeline(self.member)
        self.assertIsNone(result['first_payment_at'])
        self.assertFalse(
            any(a['is_upgrade_marker'] for a in result['activities'])
        )


class ActivityMarkerRenderTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff-773@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member-773@test.com', password='pw',
        )
        UserActivity.objects.all().delete()

    def setUp(self):
        self.client.force_login(self.staff)

    def _url(self):
        return reverse('studio_user_detail', args=[self.member.pk])

    def _add(self, event_type, label, target_url='', minutes_ago=0):
        return UserActivity.objects.create(
            user=self.member,
            event_type=event_type,
            label=label,
            target_url=target_url,
            occurred_at=timezone.now() - timezone.timedelta(minutes=minutes_ago),
        )

    def test_marker_renders_for_paid_member(self):
        self._add(
            UserActivity.EVENT_RESOURCE_VIEW, 'Viewed article: A',
            target_url='/blog/a', minutes_ago=30,
        )
        self._add(UserActivity.EVENT_PAYMENT, 'Payment: Main', minutes_ago=20)

        response = self.client.get(self._url())
        self.assertContains(response, 'data-testid="user-activity-upgrade-marker"')
        self.assertContains(response, 'Upgraded to paid')
        # resource_view row renders with its public link.
        self.assertContains(response, 'Viewed article: A')
        self.assertContains(response, 'href="/blog/a"')

    def test_no_marker_for_unpaid_member(self):
        self._add(
            UserActivity.EVENT_RESOURCE_VIEW, 'Viewed article: A',
            minutes_ago=10,
        )
        response = self.client.get(self._url())
        self.assertNotContains(
            response, 'data-testid="user-activity-upgrade-marker"',
        )
