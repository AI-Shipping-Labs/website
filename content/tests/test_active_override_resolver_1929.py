"""Shared effective-tier resolver in ``content.access`` (issue #1929).

The batched resolver must pick exactly the override that
``get_active_override`` (and therefore ``get_user_level``) picks, so
Studio labels can never disagree with access checks.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from freezegun import freeze_time

from content.access import (
    get_active_override,
    get_active_overrides_by_user,
    get_user_level,
    resolve_effective_tier,
)
from payments.models import TierOverride
from tests.fixtures import TierSetupMixin, set_membership

User = get_user_model()

FROZEN_NOW = '2026-10-09 12:00:00'
UTC = datetime.timezone.utc


def _at(month, day, year=2026):
    return datetime.datetime(year, month, day, 12, 0, tzinfo=UTC)


@freeze_time(FROZEN_NOW)
class ActiveOverridesByUserTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Maven Basic grant created after a staff Premium grant: the
        # strongest tier wins, not the latest-created row.
        cls.coexisting = User.objects.create_user(email='coexist@test.com')
        set_membership(cls.coexisting, tier=cls.free_tier)
        cls.staff_premium = TierOverride.objects.create(
            user=cls.coexisting, override_tier=cls.premium_tier,
            expires_at=_at(11, 1),
        )
        cls.maven_basic = TierOverride.objects.create(
            user=cls.coexisting, override_tier=cls.basic_tier,
            expires_at=_at(12, 31), source='maven:cohort-1',
        )

        # Same level twice: the later expiry wins.
        cls.tied = User.objects.create_user(email='tied@test.com')
        set_membership(cls.tied, tier=cls.free_tier)
        TierOverride.objects.create(
            user=cls.tied, override_tier=cls.main_tier, expires_at=_at(10, 20),
        )
        cls.tied_later = TierOverride.objects.create(
            user=cls.tied, override_tier=cls.main_tier, expires_at=_at(11, 20),
        )

        # Expired-but-still-flagged-active and revoked rows never count.
        cls.lapsed = User.objects.create_user(email='lapsed@test.com')
        set_membership(cls.lapsed, tier=cls.free_tier)
        TierOverride.objects.create(
            user=cls.lapsed, override_tier=cls.main_tier,
            expires_at=_at(10, 8), is_active=True,
        )
        TierOverride.objects.create(
            user=cls.lapsed, override_tier=cls.premium_tier,
            expires_at=_at(12, 1), is_active=False,
        )

        cls.plain = User.objects.create_user(email='plain@test.com')
        set_membership(cls.plain, tier=cls.basic_tier)

    def test_batched_map_matches_get_active_override_for_every_user(self):
        users = [self.coexisting, self.tied, self.lapsed, self.plain]

        with self.assertNumQueries(1):
            override_map = get_active_overrides_by_user(users)

        self.assertEqual(override_map[self.coexisting.pk], self.staff_premium)
        self.assertEqual(override_map[self.tied.pk], self.tied_later)
        self.assertNotIn(self.lapsed.pk, override_map)
        self.assertNotIn(self.plain.pk, override_map)
        for user in users:
            with self.subTest(user=user.email):
                self.assertEqual(
                    override_map.get(user.pk), get_active_override(user),
                )

    def test_batched_map_accepts_primary_keys(self):
        override_map = get_active_overrides_by_user(
            [self.coexisting.pk, self.plain.pk],
        )

        self.assertEqual(
            override_map, {self.coexisting.pk: self.staff_premium},
        )

    def test_empty_input_runs_no_query(self):
        with self.assertNumQueries(0):
            self.assertEqual(get_active_overrides_by_user([]), {})

    def test_effective_tier_level_agrees_with_access_level(self):
        override_map = get_active_overrides_by_user(
            [self.coexisting, self.tied, self.lapsed, self.plain],
        )
        for user in (self.coexisting, self.tied, self.lapsed, self.plain):
            user = User.objects.select_related('membership__tier').get(pk=user.pk)
            with self.subTest(user=user.email):
                effective = resolve_effective_tier(user, override_map.get(user.pk))
                self.assertEqual(effective.level, get_user_level(user))


@freeze_time(FROZEN_NOW)
class ResolveEffectiveTierTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(email='resolve@test.com')

    def _override(self, tier):
        return TierOverride(
            user=self.user, override_tier=tier, expires_at=_at(10, 30),
        )

    def test_override_above_base_wins_with_its_expiry(self):
        set_membership(self.user, tier=self.free_tier)

        effective = resolve_effective_tier(
            self.user, self._override(self.main_tier),
        )

        self.assertEqual(
            (effective.name, effective.slug, effective.level, effective.source),
            ('Main', 'main', 20, 'override'),
        )
        self.assertEqual(effective.override_expires_at, _at(10, 30))
        self.assertTrue(effective.is_override)

    def test_override_at_or_below_base_keeps_base_tier(self):
        set_membership(self.user, tier=self.main_tier)
        for override_tier in (self.basic_tier, self.main_tier):
            with self.subTest(override=override_tier.slug):
                effective = resolve_effective_tier(
                    self.user, self._override(override_tier),
                )
                self.assertEqual(
                    (effective.name, effective.slug, effective.source),
                    ('Main', 'main', 'base'),
                )
                self.assertIsNone(effective.override_expires_at)

    def test_user_without_tier_is_free(self):
        set_membership(self.user, tier=None)

        effective = resolve_effective_tier(self.user, None)

        self.assertEqual(
            (effective.name, effective.slug, effective.level, effective.source),
            ('Free', 'free', 0, 'base'),
        )
