"""Override-aware notification audience (issue #966).

``_get_eligible_users`` must include active-override holders so a member who
can open gated content is actually notified about it. The ``required_level==0``
fast path (all active users) is preserved unchanged.
"""

from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import TierOverride
from content.access import LEVEL_MAIN
from content.models import Article
from notifications.models import Notification
from notifications.services.notification_service import (
    NotificationService,
    _get_eligible_users,
)
from tests.fixtures import TierSetupMixin

User = get_user_model()


@tag('core')
class EligibleUsersOverrideTest(TierSetupMixin, TestCase):
    """_get_eligible_users includes active override members."""

    def _user(self, email, tier, *, is_active=True):
        return User.objects.create_user(
            email=email, password="pw", tier=tier, is_active=is_active,
        )

    def _override(self, user, tier, *, is_active=True, expires_in_days=7):
        TierOverride.objects.create(
            user=user,
            original_tier=user.tier,
            override_tier=tier,
            expires_at=timezone.now() + timedelta(days=expires_in_days),
            is_active=is_active,
        )

    def setUp(self):
        self.main_base = self._user("main@t.com", self.main_tier)
        self.override_user = self._user("override@t.com", self.free_tier)
        self._override(self.override_user, self.main_tier)
        self.expired_user = self._user("expired@t.com", self.free_tier)
        self._override(self.expired_user, self.main_tier, expires_in_days=-1)
        self.free_user = self._user("free@t.com", self.free_tier)

    def test_includes_override_excludes_no_override_free(self):
        eligible = set(
            _get_eligible_users(LEVEL_MAIN).values_list("email", flat=True),
        )
        self.assertIn("override@t.com", eligible)
        self.assertIn("main@t.com", eligible)
        self.assertNotIn("free@t.com", eligible)
        self.assertNotIn("expired@t.com", eligible)

    def test_distinct_no_double_count(self):
        dup = self._user("dup@t.com", self.main_tier)
        self._override(dup, self.main_tier)
        self.assertEqual(
            _get_eligible_users(LEVEL_MAIN).filter(pk=dup.pk).count(), 1,
        )

    def test_level_zero_returns_all_active_users_unchanged(self):
        inactive = self._user("inactive@t.com", self.free_tier, is_active=False)
        eligible_ids = set(
            _get_eligible_users(0).values_list("pk", flat=True),
        )
        # All active users regardless of tier/override.
        self.assertIn(self.free_user.pk, eligible_ids)
        self.assertIn(self.override_user.pk, eligible_ids)
        self.assertIn(self.main_base.pk, eligible_ids)
        self.assertNotIn(inactive.pk, eligible_ids)

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_notify_creates_notification_for_override_member(self, mock_slack):
        article = Article.objects.create(
            title='Main Article', slug='main-article',
            date=date(2025, 1, 1), published=True,
            required_level=LEVEL_MAIN,
        )
        NotificationService.notify('article', article.pk)
        self.assertTrue(
            Notification.objects.filter(user=self.override_user).exists(),
        )
        # The no-override free user is not notified.
        self.assertFalse(
            Notification.objects.filter(user=self.free_user).exists(),
        )
