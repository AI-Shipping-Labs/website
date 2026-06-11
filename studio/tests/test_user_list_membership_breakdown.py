"""Membership breakdown: tier x source decomposition (issue #923).

The Studio users dashboard replaces the single (incorrect) "Paid" stat with a
3x2 matrix: Paid {Basic,Main,Premium} (active Stripe subscription, grouped by
base tier) and Override {Basic,Main,Premium} (active TierOverride, excluding
subscription holders), plus Total paying and Total comped.

"Paid" everywhere means an active Stripe subscription only — overrides are
never counted as paid. The local signal for an active subscription is a
non-empty ``subscription_id`` paired with a paid base tier (the webhook clears
both on cancel/expire).
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from accounts.models import TierOverride
from payments.models import Tier

User = get_user_model()
FAST_PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']


@tag('core')
@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class MembershipBreakdownCountsTest(TestCase):
    """Six cells + two totals on /studio/users/, and edge cases."""

    @classmethod
    def setUpTestData(cls):
        cls.free = Tier.objects.get(slug='free')
        cls.basic = Tier.objects.get(slug='basic')
        cls.main = Tier.objects.get(slug='main')
        cls.premium = Tier.objects.get(slug='premium')

        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
            tier=cls.free,
        )

        # --- Paid via Stripe (active subscription, grouped by base tier) ---
        # 1 paid Basic, 2 paid Main, 1 paid Premium.
        cls.paid_basic = User.objects.create_user(
            email='paid-basic@test.com', password='pw',
            tier=cls.basic, subscription_id='sub_basic',
        )
        cls.paid_main_1 = User.objects.create_user(
            email='paid-main-1@test.com', password='pw',
            tier=cls.main, subscription_id='sub_main_1',
        )
        cls.paid_main_2 = User.objects.create_user(
            email='paid-main-2@test.com', password='pw',
            tier=cls.main, subscription_id='sub_main_2',
        )
        cls.paid_premium = User.objects.create_user(
            email='paid-premium@test.com', password='pw',
            tier=cls.premium, subscription_id='sub_premium',
        )

        # --- Override grants (comped, no subscription) ---
        # 1 override Basic, 1 override Main, 1 override Premium.
        cls.ov_basic = cls._comp(cls, 'ov-basic@test.com', cls.basic)
        cls.ov_main = cls._comp(cls, 'ov-main@test.com', cls.main)
        cls.ov_premium = cls._comp(cls, 'ov-premium@test.com', cls.premium)

        # --- Edge: user with BOTH active sub AND active override ---
        # Counts under Paid (by sub tier = Main), never under Override.
        cls.both = User.objects.create_user(
            email='both@test.com', password='pw',
            tier=cls.main, subscription_id='sub_both',
        )
        TierOverride.objects.create(
            user=cls.both, original_tier=cls.main, override_tier=cls.premium,
            expires_at=timezone.now() + timedelta(days=7),
            granted_by=cls.staff, is_active=True,
        )

        # --- Edge: canceled subscriber (webhook cleared sub + reverted) ---
        cls.canceled = User.objects.create_user(
            email='canceled@test.com', password='pw',
            tier=cls.free, subscription_id='',
        )

        # --- Edge: stale subscription_id but base tier Free ---
        cls.stale = User.objects.create_user(
            email='stale@test.com', password='pw',
            tier=cls.free, subscription_id='sub_stale',
        )

        # --- Edge: expired override (must not count as comped) ---
        cls.expired_ov = User.objects.create_user(
            email='expired-ov@test.com', password='pw', tier=cls.free,
        )
        TierOverride.objects.create(
            user=cls.expired_ov, original_tier=cls.free,
            override_tier=cls.premium,
            expires_at=timezone.now() - timedelta(days=1),
            granted_by=cls.staff, is_active=True,
        )

        # --- Edge: inactive override (must not count as comped) ---
        cls.inactive_ov = User.objects.create_user(
            email='inactive-ov@test.com', password='pw', tier=cls.free,
        )
        TierOverride.objects.create(
            user=cls.inactive_ov, original_tier=cls.free,
            override_tier=cls.main,
            expires_at=timezone.now() + timedelta(days=7),
            granted_by=cls.staff, is_active=False,
        )

    def _comp(self, email, override_tier):
        user = User.objects.create_user(
            email=email, password='pw', tier=self.free,
        )
        TierOverride.objects.create(
            user=user, original_tier=self.free, override_tier=override_tier,
            expires_at=timezone.now() + timedelta(days=7),
            granted_by=self.staff, is_active=True,
        )
        return user

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def _ctx(self):
        return self.client.get('/studio/users/').context

    def test_paid_cells_grouped_by_base_subscription_tier(self):
        ctx = self._ctx()
        # paid_main = 2 dedicated + 1 "both" (sub tier Main) = 3.
        self.assertEqual(ctx['paid_basic'], 1)
        self.assertEqual(ctx['paid_main'], 3)
        self.assertEqual(ctx['paid_premium'], 1)

    def test_override_cells_grouped_by_override_tier(self):
        ctx = self._ctx()
        # "both" is excluded from override (has a sub); expired/inactive
        # overrides excluded.
        self.assertEqual(ctx['override_basic'], 1)
        self.assertEqual(ctx['override_main'], 1)
        self.assertEqual(ctx['override_premium'], 1)

    def test_total_paying_is_sum_of_paid_cells(self):
        ctx = self._ctx()
        self.assertEqual(ctx['total_paying'], 5)
        self.assertEqual(
            ctx['total_paying'],
            ctx['paid_basic'] + ctx['paid_main'] + ctx['paid_premium'],
        )

    def test_total_comped_is_sum_of_override_cells(self):
        ctx = self._ctx()
        self.assertEqual(ctx['total_comped'], 3)
        self.assertEqual(
            ctx['total_comped'],
            ctx['override_basic'] + ctx['override_main']
            + ctx['override_premium'],
        )

    def test_both_sub_and_override_counts_under_paid_only(self):
        ctx = self._ctx()
        # The "both" user is in paid_main (sub tier) and NOT in override_premium
        # (would have been its override tier). Override premium stays at 1
        # (only the dedicated comp), and paid_main includes the "both" user.
        self.assertEqual(ctx['paid_main'], 3)
        self.assertEqual(ctx['override_premium'], 1)

    def test_canceled_and_stale_excluded_from_paid(self):
        ctx = self._ctx()
        # Neither the canceled user (empty sub, Free) nor the stale user
        # (sub_id but Free base tier) is counted as paid anywhere.
        self.assertEqual(ctx['total_paying'], 5)

    def test_paid_count_equals_total_paying(self):
        ctx = self._ctx()
        self.assertEqual(ctx['paid_count'], ctx['total_paying'])

    def test_paid_chip_rows_equal_total_paying(self):
        # The Paid filter chip's row list must match the Total paying number
        # exactly — they share one predicate.
        ctx = self._ctx()
        response = self.client.get('/studio/users/?filter=paid')
        paid_emails = {row['email'] for row in response.context['user_rows']}
        self.assertEqual(len(paid_emails), ctx['total_paying'])
        self.assertEqual(
            paid_emails,
            {
                'paid-basic@test.com',
                'paid-main-1@test.com',
                'paid-main-2@test.com',
                'paid-premium@test.com',
                'both@test.com',
            },
        )

    def test_paid_chip_excludes_override_only_users(self):
        response = self.client.get('/studio/users/?filter=paid')
        paid_emails = {row['email'] for row in response.context['user_rows']}
        for comped in (
            'ov-basic@test.com', 'ov-main@test.com', 'ov-premium@test.com',
        ):
            self.assertNotIn(comped, paid_emails)

    def test_paid_chip_excludes_canceled_and_stale(self):
        response = self.client.get('/studio/users/?filter=paid')
        paid_emails = {row['email'] for row in response.context['user_rows']}
        self.assertNotIn('canceled@test.com', paid_emails)
        self.assertNotIn('stale@test.com', paid_emails)

    def test_csv_paid_export_contains_only_subscription_users(self):
        import csv
        import io

        response = self.client.get('/studio/users/export?filter=paid')
        rows = list(csv.DictReader(io.StringIO(response.content.decode())))
        emails = {row['email'] for row in rows}
        self.assertEqual(
            emails,
            {
                'paid-basic@test.com',
                'paid-main-1@test.com',
                'paid-main-2@test.com',
                'paid-premium@test.com',
                'both@test.com',
            },
        )

    def test_breakdown_cells_rendered_with_testids(self):
        response = self.client.get('/studio/users/')
        self.assertContains(response, 'data-testid="membership-breakdown"')
        self.assertContains(response, 'data-testid="paid-basic"')
        self.assertContains(response, 'data-testid="paid-main"')
        self.assertContains(response, 'data-testid="paid-premium"')
        self.assertContains(response, 'data-testid="override-basic"')
        self.assertContains(response, 'data-testid="override-main"')
        self.assertContains(response, 'data-testid="override-premium"')
        self.assertContains(response, 'data-testid="total-paying"')
        self.assertContains(response, 'data-testid="total-comped"')

    def test_paid_count_is_pure_db_query_no_stripe_call(self):
        # Rendering the page must not call out to Stripe. Patch the Stripe
        # SDK module surface the dashboard would use and assert no call.
        from unittest import mock

        with mock.patch('stripe.Subscription.retrieve') as retrieve:
            self.client.get('/studio/users/')
        retrieve.assert_not_called()
