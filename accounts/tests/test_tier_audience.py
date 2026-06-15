"""Tests for the shared effective-level audience predicate (issue #966).

``effective_level_at_least_q(min_level)`` returns the canonical
base-OR-active-override Q object. A user reaches ``min_level`` either by
their real ``tier`` row OR by an active, non-expired ``TierOverride``.
Querysets using it MUST ``.distinct()`` because the override join can
duplicate rows.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import TierOverride
from accounts.tier_audience import effective_level_at_least_q
from content.access import LEVEL_MAIN
from tests.fixtures import TierSetupMixin

User = get_user_model()


@tag('core')
class EffectiveLevelAtLeastQTest(TierSetupMixin, TestCase):
    """Unit tests for the shared predicate."""

    def _user(self, email, tier):
        user = User.objects.create_user(email=email, password="pw")
        user.tier = tier
        user.save(update_fields=["tier"])
        return user

    def _override(self, user, tier, *, is_active=True, expires_in_days=7):
        return TierOverride.objects.create(
            user=user,
            original_tier=user.tier,
            override_tier=tier,
            expires_at=timezone.now() + timedelta(days=expires_in_days),
            is_active=is_active,
        )

    def _matches_main(self, user):
        return (
            User.objects.filter(effective_level_at_least_q(LEVEL_MAIN))
            .distinct()
            .filter(pk=user.pk)
            .exists()
        )

    def test_free_base_with_active_main_override_matches(self):
        user = self._user("active@test.com", self.free_tier)
        self._override(user, self.main_tier)
        self.assertTrue(self._matches_main(user))

    def test_free_base_with_expired_main_override_excluded(self):
        user = self._user("expired@test.com", self.free_tier)
        self._override(user, self.main_tier, expires_in_days=-1)
        self.assertFalse(self._matches_main(user))

    def test_free_base_with_inactive_main_override_excluded(self):
        user = self._user("inactive@test.com", self.free_tier)
        self._override(user, self.main_tier, is_active=False)
        self.assertFalse(self._matches_main(user))

    def test_free_base_with_basic_override_excluded_at_main(self):
        # Override grants a level below LEVEL_MAIN -> not in a Main audience.
        user = self._user("below@test.com", self.free_tier)
        self._override(user, self.basic_tier)
        self.assertFalse(self._matches_main(user))

    def test_main_base_no_override_matches(self):
        user = self._user("mainbase@test.com", self.main_tier)
        self.assertTrue(self._matches_main(user))

    def test_free_base_no_override_excluded(self):
        user = self._user("free@test.com", self.free_tier)
        self.assertFalse(self._matches_main(user))

    def test_distinct_collapses_base_plus_override_duplicate(self):
        # User qualifies via BOTH a Main base tier AND an active Main override.
        # Without .distinct() the override join duplicates the row.
        user = self._user("both@test.com", self.main_tier)
        self._override(user, self.main_tier)
        qs = (
            User.objects.filter(effective_level_at_least_q(LEVEL_MAIN))
            .distinct()
            .filter(pk=user.pk)
        )
        self.assertEqual(qs.count(), 1)

    def test_uses_now_at_call_time_not_import_time(self):
        # An override expiring 1 second in the future matches now; the same
        # predicate built later (after expiry) must not. Proves now() is
        # evaluated when the Q is built, not at module import.
        user = self._user("edge@test.com", self.free_tier)
        TierOverride.objects.create(
            user=user,
            original_tier=self.free_tier,
            override_tier=self.main_tier,
            expires_at=timezone.now() - timedelta(seconds=1),
            is_active=True,
        )
        # Built fresh now -> already expired -> excluded.
        self.assertFalse(self._matches_main(user))
